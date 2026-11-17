"""Baseline ("normal level") detection.

Fixed thresholds such as "10 people in 15 minutes" ignore that some services are always
busier than others, and that complaints follow the time of day. This rule learns, per
(service, issue) and hour of the day, how many distinct people normally complain in a
15-minute period, and raises an alarm when the current count is improbably high for
that hour.

* ``BaselineCalculator`` builds the profile from the last ``history_days`` of stored,
  classified posts (analyst labels included): the trimmed mean of distinct
  complainants per 15-minute bucket for every hour of the day. The highest
  ``trim_fraction`` of buckets are dropped so that past incidents do not raise the
  "normal" level. The most recent ``exclude_recent`` is left out so a running spike
  never feeds its own baseline. The profile is cached and rebuilt every ``refresh``.
* Hours are UTC. Türkiye has no daylight saving time, so UTC hours map one-to-one to
  local hours of the daily cycle.
* ``BaselineRule`` turns the expected value into a probability with the Poisson
  distribution: ``p = P(at least the observed number of people | expected)``. A
  WARNING needs both enough people (``min_users``) and a small enough ``p``. The
  shortest window acts as the velocity check: a sudden spike within minutes.
* Until ``min_history_days`` of data exist the rule abstains and the fixed thresholds
  keep working alone.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ...domain.enums import Severity
from ..ports import AnalysisRepository, PostRepository
from ..probability import poisson_sf
from .rules import RuleContext, RuleOutcome
from .window_aggregator import GroupKey

BUCKET_MINUTES = 15
_BUCKET = timedelta(minutes=BUCKET_MINUTES)


@dataclass(frozen=True)
class BaselineSettings:
    history_days: int = 14
    min_history_days: int = 7
    trim_fraction: float = 0.05
    min_expected_users: float = 0.1
    exclude_recent: timedelta = timedelta(minutes=30)
    refresh: timedelta = timedelta(hours=1)


@dataclass(frozen=True)
class Expectation:
    expected_users: float  # distinct people expected in the window at this hour
    history_days: float  # how much history the profile is based on


@dataclass(frozen=True)
class BaselineStatus:
    ready: bool
    history_days: float
    min_history_days: int


@dataclass(frozen=True)
class _Profile:
    built_at: datetime
    history_days: float
    per_bucket: dict[tuple[GroupKey, int], float]  # (group, UTC hour) -> trimmed mean per 15-minute bucket


class BaselineCalculator:
    def __init__(self, posts: PostRepository, analyses: AnalysisRepository, settings: BaselineSettings) -> None:
        self._posts = posts
        self._analyses = analyses
        self._settings = settings
        self._profile: _Profile | None = None

    def expected(self, key: GroupKey, now: datetime, window_minutes: int) -> Expectation | None:
        profile = self._current(now)
        if profile.history_days < self._settings.min_history_days:
            return None
        per_bucket = profile.per_bucket.get((key, now.astimezone(UTC).hour), 0.0)
        expected = max(self._settings.min_expected_users, per_bucket * window_minutes / BUCKET_MINUTES)
        return Expectation(expected, profile.history_days)

    def rate_per_bucket(self, key: GroupKey, at: datetime, now: datetime) -> float | None:
        """Normal distinct people per 15 minutes for the hour of ``at``; None until the history suffices."""
        profile = self._current(now)
        if profile.history_days < self._settings.min_history_days:
            return None
        return profile.per_bucket.get((key, at.astimezone(UTC).hour), 0.0)

    def status(self, now: datetime) -> BaselineStatus:
        profile = self._current(now)
        return BaselineStatus(
            ready=profile.history_days >= self._settings.min_history_days,
            history_days=round(profile.history_days, 1),
            min_history_days=self._settings.min_history_days,
        )

    def _current(self, now: datetime) -> _Profile:
        profile = self._profile
        if profile is None or now - profile.built_at >= self._settings.refresh or now < profile.built_at:
            profile = self._build(now)
            self._profile = profile
        return profile

    def _build(self, now: datetime) -> _Profile:
        end = _floor(now - self._settings.exclude_recent)
        start = end - timedelta(days=self._settings.history_days)
        posts = self._posts.find_created_between(start, end)
        if not posts:
            return _Profile(now, 0.0, {})
        coverage_start = max(start, _floor(min(post.created_at for post in posts)))
        history_days = (end - coverage_start) / timedelta(days=1)

        authors: dict[tuple[GroupKey, datetime], set[str]] = defaultdict(set)
        for post in posts:
            analysis = self._analyses.get(post.post_id)
            if analysis is None or not analysis.is_complaint:
                continue
            key = GroupKey(analysis.service_category, analysis.issue_category)
            authors[(key, _floor(post.created_at))].add(post.author_id)

        buckets_per_hour: dict[int, int] = defaultdict(int)
        bucket = coverage_start
        while bucket < end:
            buckets_per_hour[bucket.astimezone(UTC).hour] += 1
            bucket += _BUCKET
        counts: dict[tuple[GroupKey, int], list[int]] = defaultdict(list)
        for (key, bucket_start), people in authors.items():
            counts[(key, bucket_start.astimezone(UTC).hour)].append(len(people))
        per_bucket = {
            (key, hour): _trimmed_mean(values, buckets_per_hour[hour], self._settings.trim_fraction)
            for (key, hour), values in counts.items()
            if buckets_per_hour[hour]
        }
        return _Profile(now, history_days, per_bucket)


def _floor(moment: datetime) -> datetime:
    moment = moment.astimezone(UTC)
    return moment.replace(minute=moment.minute - moment.minute % BUCKET_MINUTES, second=0, microsecond=0)


def _trimmed_mean(non_zero: list[int], buckets: int, trim_fraction: float) -> float:
    """Mean over ``buckets`` values (``non_zero`` plus zeros) after dropping the largest ``trim_fraction``."""
    drop = math.ceil(buckets * trim_fraction)
    kept = sorted(non_zero)[: max(0, len(non_zero) - drop)]
    remaining = buckets - min(drop, buckets)
    return sum(kept) / remaining if remaining > 0 else 0.0


@dataclass(frozen=True)
class BaselineThresholds:
    min_users_warning: int = 4
    min_users_critical: int = 8
    p_warning: float = 0.001
    p_critical: float = 0.00001

    def __post_init__(self) -> None:
        if not 0 < self.min_users_warning <= self.min_users_critical:
            raise ValueError("baseline minimum users must satisfy 0 < warning <= critical")
        if not 0 < self.p_critical <= self.p_warning < 1:
            raise ValueError("baseline probabilities must satisfy 0 < critical <= warning < 1")

    def level_for(self, users: int, probability: float) -> Severity:
        if users >= self.min_users_critical and probability <= self.p_critical:
            return Severity.CRITICAL
        if users >= self.min_users_warning and probability <= self.p_warning:
            return Severity.WARNING
        return Severity.NORMAL


class BaselineRule:
    name = "baseline"

    def __init__(self, calculator: BaselineCalculator, windows: Sequence[int], thresholds: BaselineThresholds) -> None:
        if not windows:
            raise ValueError("the baseline rule needs at least one window")
        self._calculator = calculator
        self._windows = sorted(set(windows))
        self._thresholds = thresholds

    @property
    def windows(self) -> list[int]:
        return list(self._windows)

    def evaluate(self, context: RuleContext) -> RuleOutcome | None:
        best: tuple[RuleOutcome, float] | None = None
        for minutes in self._windows:
            stats = context.stats_by_window.get(minutes)
            if stats is None or stats.unique_users_count == 0:
                continue
            expectation = self._calculator.expected(context.key, context.now, minutes)
            if expectation is None:
                return None  # not enough history yet: leave the decision to the fixed thresholds
            users = stats.unique_users_count
            probability = poisson_sf(users, expectation.expected_users)
            level = self._thresholds.level_for(users, probability)
            outcome = RuleOutcome(
                level=level,
                window_minutes=minutes,
                complaints_count=stats.complaints_count,
                unique_users_count=users,
                reason=(
                    f"{users} unique users in the last {minutes} minutes; {expectation.expected_users:.2f} expected "
                    f"at this hour (p={probability:.1e}, {expectation.history_days:.0f}-day baseline)"
                ),
                details={
                    "rule": self.name,
                    "window_minutes": minutes,
                    "unique_users": users,
                    "complaints": stats.complaints_count,
                    "expected_users": round(expectation.expected_users, 3),
                    "p_value": probability,
                    "history_days": round(expectation.history_days, 1),
                    "velocity": minutes == self._windows[0] and len(self._windows) > 1,
                },
            )
            if best is None or level.is_higher_than(best[0].level) or (level is best[0].level and probability < best[1]):
                best = (outcome, probability)
        return best[0] if best else None
