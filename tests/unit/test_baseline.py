from __future__ import annotations

import math
from datetime import timedelta
from pathlib import Path

import pytest

from social_incident_monitor.application.detection.baseline import (
    BaselineCalculator,
    BaselineRule,
    BaselineSettings,
    BaselineThresholds,
    _trimmed_mean,
    poisson_sf,
)
from social_incident_monitor.application.detection.rules import RuleContext
from social_incident_monitor.application.detection.window_aggregator import ComplaintWindowAggregator, GroupKey
from social_incident_monitor.classification.rule_based import RuleBasedClassifier, RuleSet
from social_incident_monitor.domain.enums import IssueCategory, ServiceCategory, Severity
from social_incident_monitor.infrastructure.jsonl.repositories import JsonlAnalysisRepository, JsonlPostRepository

from conftest import START, make_post

ATM = GroupKey(ServiceCategory.ATM, IssueCategory.ATM_FAILURE)
NOW = START + timedelta(days=14, hours=1)  # 10:00 UTC on day 15


def _exact_sf(observed: int, expected: float) -> float:
    return 1.0 - sum(math.exp(-expected) * expected**count / math.factorial(count) for count in range(observed))


@pytest.mark.parametrize(("observed", "expected"), [(1, 1.0), (3, 0.5), (5, 2.0), (50, 40.0), (8, 1.0)])
def test_poisson_tail_matches_the_direct_formula(observed: int, expected: float) -> None:
    assert poisson_sf(observed, expected) == pytest.approx(_exact_sf(observed, expected), rel=1e-9)


def test_poisson_tail_is_accurate_for_tiny_probabilities() -> None:
    # 10 people when 0.1 are expected: e^-0.1 * 0.1^10 / 10! (the first term dominates)
    leading = math.exp(-0.1) * 0.1**10 / math.factorial(10)
    assert poisson_sf(10, 0.1) == pytest.approx(leading * (1 + 0.1 / 11 + 0.01 / 132), rel=1e-6)
    assert poisson_sf(0, 3.0) == 1.0 and poisson_sf(4, 0.0) == 0.0


def test_trimmed_mean_counts_quiet_buckets_and_drops_spikes() -> None:
    assert _trimmed_mean([1] * 56, 56, 0.05) == 1.0
    assert _trimmed_mean([40, 2, 1], 56, 0.05) == 0.0  # the 3 largest of 56 buckets are dropped
    assert _trimmed_mean([1, 1, 1, 1, 30], 20, 0.05) == pytest.approx(4 / 19)
    assert _trimmed_mean([], 0, 0.05) == 0.0


class _History:
    def __init__(self, path: Path) -> None:
        self.posts = JsonlPostRepository(path)
        self.analyses = JsonlAnalysisRepository(path)
        self.classifier = RuleBasedClassifier(RuleSet.load())
        self.number = 0

    def add(self, minutes_before_now: float, author: str, text: str = "Ziraat ATM paramı vermedi") -> None:
        self.number += 1
        created = NOW - timedelta(minutes=minutes_before_now)
        post = make_post(text, author=author, created_at=created, number=self.number)
        self.posts.add_if_absent(post)
        self.analyses.save(self.classifier.classify(post, created))

    def calculator(self, **overrides: object) -> BaselineCalculator:
        return BaselineCalculator(self.posts, self.analyses, BaselineSettings(**overrides))  # type: ignore[arg-type]

    def rule(self, calculator: BaselineCalculator) -> BaselineRule:
        return BaselineRule(calculator, [5, 15], BaselineThresholds())

    def context(self, windows: tuple[int, ...] = (5, 15, 30)) -> RuleContext:
        stats = ComplaintWindowAggregator(self.posts, self.analyses).aggregate(NOW, windows)
        return RuleContext(ATM, NOW, stats[ATM])


@pytest.fixture
def history(tmp_path: Path) -> _History:
    return _History(tmp_path)


def _one_complaint_per_quarter_at_10_utc(history: _History, days: int = 14) -> None:
    for day in range(1, days + 1):
        for quarter in range(4):
            # 10:00-10:59 UTC of every previous day, one new person per 15-minute bucket
            history.add(day * 24 * 60 - quarter * 15 - 1, f"past-{day}-{quarter}")


def test_profile_learns_the_hourly_normal_level(history: _History) -> None:
    _one_complaint_per_quarter_at_10_utc(history)
    calculator = history.calculator()
    expectation = calculator.expected(ATM, NOW, 15)
    assert expectation is not None and expectation.expected_users == pytest.approx(1.0)
    assert calculator.expected(ATM, NOW, 5).expected_users == pytest.approx(1 / 3)  # type: ignore[union-attr]
    # another hour of the day has no history: the floor applies
    assert calculator.expected(ATM, NOW + timedelta(hours=5), 15).expected_users == 0.1  # type: ignore[union-attr]
    status = calculator.status(NOW)
    assert status.ready and status.history_days >= 13.9


def test_rule_abstains_until_enough_history(history: _History) -> None:
    _one_complaint_per_quarter_at_10_utc(history, days=3)
    for index in range(9):
        history.add(2, f"now-{index}")
    calculator = history.calculator()
    assert calculator.status(NOW).ready is False
    assert history.rule(calculator).evaluate(history.context()) is None


@pytest.mark.parametrize(
    ("people", "minutes_ago", "level", "velocity"),
    [
        (2, 10, Severity.NORMAL, False),  # within the normal range for this hour
        (8, 10, Severity.WARNING, False),  # p ≈ 1.0e-5: improbable, but not below the CRITICAL limit
        (10, 10, Severity.CRITICAL, False),
        (5, 2, Severity.WARNING, True),  # sudden spike in 5 minutes (expected 0.33)
    ],
)
def test_rule_levels_against_the_learned_baseline(
    history: _History, people: int, minutes_ago: float, level: Severity, velocity: bool
) -> None:
    _one_complaint_per_quarter_at_10_utc(history)
    for index in range(people):
        history.add(minutes_ago, f"now-{index}")
    outcome = history.rule(history.calculator()).evaluate(history.context())
    assert outcome is not None and outcome.level is level
    if level is not Severity.NORMAL:
        assert outcome.details["velocity"] is velocity
        assert outcome.details["rule"] == "baseline" and outcome.details["history_days"] >= 13.9
        assert "expected at this hour" in outcome.reason


def test_quiet_hour_needs_far_fewer_people_than_the_fixed_threshold(history: _History) -> None:
    for day in range(1, 15):
        history.add(day * 24 * 60 + 180, f"history-{day}")  # some activity at another hour, so history exists
    for index in range(4):
        history.add(8, f"now-{index}")
    outcome = history.rule(history.calculator()).evaluate(history.context())
    assert outcome is not None and outcome.level is Severity.WARNING  # 4 people when 0.1 are expected
    assert outcome.details["expected_users"] == 0.1


def test_past_incident_does_not_raise_the_normal_level(history: _History) -> None:
    _one_complaint_per_quarter_at_10_utc(history)
    for index in range(40):
        history.add(3 * 24 * 60 - 20, f"incident-{index}")  # a big incident three days ago at 09:40
    assert history.calculator().expected(ATM, NOW, 15).expected_users == pytest.approx(1.0)  # type: ignore[union-attr]


def test_profile_is_cached_and_rebuilt_after_refresh(history: _History) -> None:
    _one_complaint_per_quarter_at_10_utc(history)
    calculator = history.calculator(refresh=timedelta(hours=1))
    first = calculator.expected(ATM, NOW, 15)
    history.add(4 * 24 * 60 - 20, "late-arrival")
    assert calculator.expected(ATM, NOW + timedelta(minutes=10), 15) == first  # cached
    assert calculator.expected(ATM, NOW + timedelta(hours=1), 15) is not None  # rebuilt without error


@pytest.mark.parametrize(("users", "p_values"), [((0, 8), (0.001, 1e-5)), ((9, 8), (0.001, 1e-5)), ((4, 8), (1e-5, 0.001)), ((4, 8), (0.001, 0.0))])
def test_invalid_thresholds_are_rejected(users: tuple[int, int], p_values: tuple[float, float]) -> None:
    with pytest.raises(ValueError):
        BaselineThresholds(*users, *p_values)
