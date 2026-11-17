from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from social_incident_monitor.application.detection.reputation import (
    ReputationAggregator,
    ReputationRule,
    ReputationThresholds,
)
from social_incident_monitor.application.detection.rules import RuleContext
from social_incident_monitor.application.detection.window_aggregator import GroupKey, WindowStats
from social_incident_monitor.application.errors import ConfigurationError
from social_incident_monitor.classification.rule_based import RuleBasedClassifier, RuleSet
from social_incident_monitor.domain.enums import IssueCategory, ServiceCategory, Severity
from social_incident_monitor.domain.models import EngagementSnapshot
from social_incident_monitor.infrastructure.jsonl.repositories import (
    JsonlAnalysisRepository,
    JsonlEngagementRepository,
    JsonlPostRepository,
)

from conftest import START, make_post

CLASSIFIER = RuleBasedClassifier(RuleSet.load())
RUMOR = GroupKey(ServiceCategory.REPUTATION, IssueCategory.SOLVENCY_RUMOR)


def _signals(text: str) -> tuple[str, ...]:
    return CLASSIFIER.classify(make_post(text), START).risk_signals


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ziraat Bankası batıyor mu?", ("solvency_rumor",)),
        ("Ziraat iflas etti diyorlar", ("solvency_rumor",)),
        ("Ziraat'te mevduatlarımız güvende mi?", ("solvency_rumor",)),
        ("Ziraat'teki tüm paramı çektim, başka bankaya geçiyorum", ("deposit_flight",)),
        ("Ziraat hesabımı kapattım", ("deposit_flight",)),
        ("Herkes Ziraat'ten paranızı hemen çekin!", ("run_call",)),
        ("Ziraat batacak, paranızı çekin", ("solvency_rumor", "run_call")),
        # ordinary banking language must not look like a run
        ("Ziraat ATM'den paramı çektim", ()),
        ("Ziraat kredi kartı yüzünden borca battım", ()),
        ("Ziraat Batı Ataşehir şubesi kalabalık", ()),
        ("Ziraat Mobil açılmıyor", ()),
        # sports context without banking detail is excluded
        ("Ziraat Türkiye Kupası finalinde rakip batıyor", ()),
        # the bank must be named
        ("Bu şirket batıyor mu?", ()),
    ],
)
def test_reputation_lexicon(text: str, expected: tuple[str, ...]) -> None:
    assert _signals(text) == expected


def test_signal_marks_post_as_bank_related_and_is_explained() -> None:
    result = CLASSIFIER.classify(make_post("Ziraat Bankası batıyor mu?"), START)
    assert result.is_bank_related is True and result.is_complaint is False
    assert "risk:solvency_rumor" in result.matched_rules and result.classifier_version == "tr-1.4"


def test_unknown_signal_name_in_rules_file_is_rejected(tmp_path: Path) -> None:
    rules_file = Path(__file__).resolve().parents[2] / "src" / "social_incident_monitor" / "classification" / "rules_tr.json"
    rules = json.loads(rules_file.read_text(encoding="utf-8"))
    rules["reputation_signals"][0]["name"] = "panic"
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="reputation signal name"):
        RuleSet.load(path)


@pytest.fixture
def stores(tmp_path: Path) -> tuple[JsonlPostRepository, JsonlAnalysisRepository, JsonlEngagementRepository]:
    return JsonlPostRepository(tmp_path), JsonlAnalysisRepository(tmp_path), JsonlEngagementRepository(tmp_path)


def test_aggregator_counts_people_once_and_sums_each_persons_widest_reach(stores) -> None:  # type: ignore[no-untyped-def]
    posts, analyses, engagement = stores
    entries = [("ayse", 0), ("ayse", 30), ("ayse", 5), ("mehmet", 20), ("eski", 400)]
    for number, (author, reposts) in enumerate(entries, start=1):
        minutes_ago = 90 if author == "eski" else 10  # outside the 60-minute window
        post = make_post("Ziraat batıyor mu?", author=author, created_at=START - timedelta(minutes=minutes_ago), number=number)
        posts.add_if_absent(post)
        analyses.save(CLASSIFIER.classify(post, START))
        engagement.save(EngagementSnapshot(post_id=post.post_id, observed_at=START, like_count=0, repost_count=reposts, reply_count=0, quote_count=0))
    stats = ReputationAggregator(posts, analyses, engagement).aggregate(START, [60])
    window = stats[RUMOR][60]
    assert (window.unique_users_count, window.complaints_count, window.amplification) == (2, 4, 50)  # ayse 30 + mehmet 20


@pytest.mark.parametrize(
    ("users", "amplification", "level"),
    [(2, 10, Severity.NORMAL), (3, 0, Severity.WARNING), (1, 60, Severity.WARNING), (10, 0, Severity.CRITICAL), (1, 600, Severity.CRITICAL)],
)
def test_rule_levels(users: int, amplification: int, level: Severity) -> None:
    stats = WindowStats(60, START, START, users, users, 1, amplification=amplification)
    outcome = ReputationRule(60, ReputationThresholds()).evaluate(RuleContext(RUMOR, START, {60: stats}))
    assert outcome is not None and outcome.level is level
    assert outcome.details["rule"] == "reputation" and outcome.details["amplification"] == amplification


def test_rule_ignores_operational_groups() -> None:
    stats = WindowStats(60, START, START, 20, 20, 1, amplification=999)
    key = GroupKey(ServiceCategory.ATM, IssueCategory.ATM_FAILURE)
    assert ReputationRule(60, ReputationThresholds()).evaluate(RuleContext(key, START, {60: stats})) is None
