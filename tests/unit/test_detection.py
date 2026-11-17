from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from social_incident_monitor.application.detection.engine import DetectionEngine, DetectionPipeline
from social_incident_monitor.application.detection.rules import AbsoluteThresholdRule, RuleContext, WindowThreshold
from social_incident_monitor.application.detection.window_aggregator import ComplaintWindowAggregator, GroupKey
from social_incident_monitor.classification.rule_based import RuleBasedClassifier, RuleSet
from social_incident_monitor.domain.enums import IssueCategory, ServiceCategory, Severity
from social_incident_monitor.infrastructure.jsonl.repositories import JsonlAnalysisRepository, JsonlPostRepository

from conftest import START, make_post

NOW = START + timedelta(minutes=30)
ATM_KEY = GroupKey(ServiceCategory.ATM, IssueCategory.ATM_FAILURE)


class _Store:
    def __init__(self, path: Path) -> None:
        self.posts = JsonlPostRepository(path)
        self.analyses = JsonlAnalysisRepository(path)
        self.classifier = RuleBasedClassifier(RuleSet.load())
        self.number = 0

    def add(self, text: str, author: str, minutes_ago: float) -> None:
        self.number += 1
        post = make_post(text, author=author, created_at=NOW - timedelta(minutes=minutes_ago), number=self.number)
        self.posts.add_if_absent(post)
        self.analyses.save(self.classifier.classify(post, NOW))


@pytest.fixture
def store(tmp_path: Path) -> _Store:
    return _Store(tmp_path)


def _engine(store: _Store, windows: list[int]) -> DetectionEngine:
    aggregator = ComplaintWindowAggregator(store.posts, store.analyses)
    return DetectionEngine([DetectionPipeline(aggregator, [AbsoluteThresholdRule([WindowThreshold(15, 10, 25)])], windows)])


def test_windows_count_complaints_and_unique_users(store: _Store) -> None:
    store.add("Ziraat ATM paramı vermedi", "u1", 2)
    store.add("Ziraat ATM paramı vermedi yine", "u1", 3)  # repeat by same user
    store.add("Ziraat ATM paramı vermedi", "u2", 10)
    store.add("Ziraat ATM paramı vermedi", "u3", 25)
    store.add("Ziraat ATM paramı vermedi", "u4", 45)  # outside every window
    store.add("Ziraat Türkiye Kupası maçı başladı", "u5", 1)  # not a complaint

    stats = ComplaintWindowAggregator(store.posts, store.analyses).aggregate(NOW, [5, 15, 30])
    assert list(stats) == [ATM_KEY]
    five, fifteen, thirty = stats[ATM_KEY][5], stats[ATM_KEY][15], stats[ATM_KEY][30]
    assert (five.complaints_count, five.unique_users_count, five.repeat_complaints_count) == (2, 1, 1)
    assert (fifteen.complaints_count, fifteen.unique_users_count) == (3, 2)
    assert (thirty.complaints_count, thirty.unique_users_count) == (4, 3)
    assert five.max_complaints_per_user == 2


def test_services_and_issues_are_grouped_separately(store: _Store) -> None:
    store.add("Ziraat ATM paramı vermedi", "u1", 1)
    store.add("Ziraat Mobil açılmıyor", "u2", 1)
    store.add("Ziraat mobil giriş yapamıyorum", "u3", 1)
    keys = set(ComplaintWindowAggregator(store.posts, store.analyses).aggregate(NOW, [15]))
    assert keys == {
        ATM_KEY,
        GroupKey(ServiceCategory.MOBILE_BANKING, IssueCategory.APPLICATION_UNAVAILABLE),
        GroupKey(ServiceCategory.MOBILE_BANKING, IssueCategory.LOGIN_FAILURE),
    }


@pytest.mark.parametrize(("users", "level"), [(9, Severity.NORMAL), (10, Severity.WARNING), (24, Severity.WARNING), (25, Severity.CRITICAL)])
def test_threshold_boundaries(store: _Store, users: int, level: Severity) -> None:
    for index in range(users):
        store.add("Ziraat ATM paramı vermedi", f"user-{index}", 5)
    engine = _engine(store, [5, 15, 30])
    [signal] = engine.detect(NOW)
    assert signal.level is level
    assert signal.unique_users_count == users
    assert signal.window_minutes == 15


def test_repeated_complaints_by_one_user_do_not_trigger(store: _Store) -> None:
    for _ in range(40):
        store.add("Ziraat ATM paramı vermedi", "same-user", 1)
    engine = _engine(store, [15])
    [signal] = engine.detect(NOW)
    assert signal.level is Severity.NORMAL
    assert signal.complaints_count == 40


def test_no_complaints_no_signals(store: _Store) -> None:
    store.add("Ziraat Bankası faiz oranları nedir?", "u1", 1)
    engine = _engine(store, [15])
    assert engine.detect(NOW) == []


def test_highest_level_across_windows_wins() -> None:
    from social_incident_monitor.application.detection.window_aggregator import WindowStats

    rule = AbsoluteThresholdRule([WindowThreshold(5, 3, 6), WindowThreshold(15, 10, 25)])
    stats = {
        5: WindowStats(5, NOW, NOW, 7, 6, 2),
        15: WindowStats(15, NOW, NOW, 12, 11, 2),
    }
    outcome = rule.evaluate(RuleContext(ATM_KEY, NOW, stats))
    assert outcome is not None and outcome.level is Severity.CRITICAL and outcome.window_minutes == 5


@pytest.mark.parametrize(("window", "warning", "critical"), [(0, 1, 2), (15, 0, 2), (15, 10, 5)])
def test_invalid_thresholds_rejected(window: int, warning: int, critical: int) -> None:
    with pytest.raises(ValueError):
        WindowThreshold(window, warning, critical)


def test_signal_carries_the_first_post_of_the_deciding_window(store: _Store) -> None:
    for index in range(10):
        store.add("Ziraat ATM paramı vermedi", f"user-{index}", 2 + index)
    [signal] = _engine(store, [15]).detect(NOW)
    assert signal.level is Severity.WARNING and signal.first_post_at == NOW - timedelta(minutes=11)
    assert signal.details["rule"] == "absolute_threshold" and signal.details["unique_users"] == 10


def test_pipeline_and_engine_require_rules_and_windows(store: _Store) -> None:
    aggregator = ComplaintWindowAggregator(store.posts, store.analyses)
    with pytest.raises(ValueError):
        DetectionPipeline(aggregator, [], [15])
    with pytest.raises(ValueError):
        DetectionPipeline(aggregator, [AbsoluteThresholdRule([WindowThreshold(15, 10, 25)])], [])
    with pytest.raises(ValueError):
        DetectionEngine([])
