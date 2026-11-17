"""Reputation / deposit-risk signals.

Posts saying the bank is going bust, that people are withdrawing all their money or
closing accounts, or calling on others to do so are grouped per signal type under
``ServiceCategory.REPUTATION``. Research on the 2023 bank runs found that attention
(how many people talk about it, how far posts spread) predicted stress better than
negative tone, so the rule looks at two numbers per window:

* distinct people posting such a signal (one person's ten posts count once), and
* amplification: for every such person, the highest reposts + quotes of their posts,
  summed. Counters come from the collector's engagement refreshes.

Either number crossing its threshold raises the level. The output is meant for risk
and treasury teams; it never triggers any automatic external action.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from ...domain.enums import REPUTATION_ISSUES, ServiceCategory, Severity
from ...domain.models import Post
from ..ports import AnalysisRepository, EngagementRepository, PostRepository
from .rules import RuleContext, RuleOutcome
from .window_aggregator import CountedPost, GroupedWindowStats, GroupKey, window_stats

_SIGNALS = {issue.value: issue for issue in REPUTATION_ISSUES}


class ReputationAggregator:
    def __init__(self, posts: PostRepository, analyses: AnalysisRepository, engagement: EngagementRepository) -> None:
        self._posts = posts
        self._analyses = analyses
        self._engagement = engagement

    def aggregate(self, now: datetime, windows: Sequence[int]) -> GroupedWindowStats:
        if not windows:
            return {}
        signalled: dict[GroupKey, list[tuple[Post, int]]] = defaultdict(list)
        for post in self._posts.find_created_between(now - timedelta(minutes=max(windows)), now):
            analysis = self._analyses.get(post.post_id)
            if analysis is None or not analysis.risk_signals:
                continue
            snapshot = self._engagement.get(post.post_id)
            amplification = snapshot.amplification if snapshot is not None else 0
            for name in analysis.risk_signals:
                issue = _SIGNALS.get(name)
                if issue is not None:
                    signalled[GroupKey(ServiceCategory.REPUTATION, issue)].append((post, amplification))
        result: GroupedWindowStats = {}
        for key, items in signalled.items():
            result[key] = {}
            for minutes in windows:
                in_window = [(post, amplification) for post, amplification in items if post.created_at > now - timedelta(minutes=minutes)]
                highest: dict[str, int] = {}
                for post, amplification in in_window:
                    highest[post.author_id] = max(highest.get(post.author_id, 0), amplification)
                result[key][minutes] = window_stats(
                    now,
                    minutes,
                    [CountedPost(post.author_id, post.created_at) for post, _ in in_window],
                    amplification=sum(highest.values()),
                )
        return result


@dataclass(frozen=True)
class ReputationThresholds:
    warning_users: int = 3
    critical_users: int = 10
    warning_amplification: int = 50
    critical_amplification: int = 500

    def __post_init__(self) -> None:
        if not 0 < self.warning_users <= self.critical_users:
            raise ValueError("reputation users must satisfy 0 < warning <= critical")
        if not 0 < self.warning_amplification <= self.critical_amplification:
            raise ValueError("reputation amplification must satisfy 0 < warning <= critical")

    def level_for(self, users: int, amplification: int) -> Severity:
        if users >= self.critical_users or amplification >= self.critical_amplification:
            return Severity.CRITICAL
        if users >= self.warning_users or amplification >= self.warning_amplification:
            return Severity.WARNING
        return Severity.NORMAL


class ReputationRule:
    name = "reputation"

    def __init__(self, window_minutes: int, thresholds: ReputationThresholds) -> None:
        if window_minutes <= 0:
            raise ValueError("window_minutes must be positive")
        self._window = window_minutes
        self._thresholds = thresholds

    @property
    def windows(self) -> list[int]:
        return [self._window]

    def evaluate(self, context: RuleContext) -> RuleOutcome | None:
        stats = context.stats_by_window.get(self._window)
        if stats is None or stats.unique_users_count == 0 or context.key.service is not ServiceCategory.REPUTATION:
            return None
        users, amplification = stats.unique_users_count, stats.amplification
        thresholds = self._thresholds
        return RuleOutcome(
            level=thresholds.level_for(users, amplification),
            window_minutes=self._window,
            complaints_count=stats.complaints_count,
            unique_users_count=users,
            reason=(
                f"{users} people posted {context.key.issue.value} signals in the last {self._window} minutes; "
                f"amplification (reposts + quotes) {amplification}"
            ),
            details={
                "rule": self.name,
                "window_minutes": self._window,
                "unique_users": users,
                "posts": stats.complaints_count,
                "amplification": amplification,
                "warning_users": thresholds.warning_users,
                "critical_users": thresholds.critical_users,
                "warning_amplification": thresholds.warning_amplification,
                "critical_amplification": thresholds.critical_amplification,
            },
        )
