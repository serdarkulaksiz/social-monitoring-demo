from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from social_incident_monitor.application.topics_service import TopicSettings, TopicsService
from social_incident_monitor.classification.locations import LocationMatcher
from social_incident_monitor.classification.rule_based import RuleBasedClassifier, RuleSet
from social_incident_monitor.classification.terms import TermExtractor
from social_incident_monitor.classification.text_normalizer import fold
from social_incident_monitor.infrastructure.jsonl.repositories import JsonlAnalysisRepository, JsonlPostRepository

from conftest import START, FakeClock, make_post

MATCHER = LocationMatcher.load()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("İstanbul'da ATM çalışmıyor", ("istanbul",)),
        ("Ankara'daki şubede uzun kuyruk", ("ankara",)),
        ("İzmirli müşteriyim", ("izmir",)),
        ("Antep'te kart geçmiyor", ("gaziantep",)),
        ("Mersin'de ve Adana'da sorun var", ("mersin", "adana")),
        ("Ağrı'da ATM bozuk", ("agri",)),
        ("Van ili merkezde sorun", ("van",)),
        # everyday words that look like province names
        ("karşı tarafa para gitmedi", ()),
        ("müşteri hizmetleri berbat", ()),
        ("başım ağrıyor", ()),
        ("van kiraladım", ()),
        ("Samsung telefonumda uygulama açılmıyor", ()),
        ("bölümünde hata var", ()),
        ("Tokat attı", ()),
    ],
)
def test_province_mentions(text: str, expected: tuple[str, ...]) -> None:
    assert MATCHER.find(fold(text)) == expected


def test_province_display_names_and_count() -> None:
    assert MATCHER.name("istanbul") == "İstanbul" and MATCHER.name("sanliurfa") == "Şanlıurfa"
    assert MATCHER.name("unknown-key") == "unknown-key"


def test_classifier_reports_locations_only_for_bank_posts() -> None:
    classifier = RuleBasedClassifier(RuleSet.load(), MATCHER)
    assert classifier.classify(make_post("Ziraat ATM İstanbul'da paramı vermedi"), START).locations == ("istanbul",)
    assert classifier.classify(make_post("Ankara'da hava güzel"), START).locations == ()


def test_terms_use_five_letter_stems_without_stopwords() -> None:
    extractor = TermExtractor.load()
    terms = extractor.terms("Ziraat QR kodu ile para çekemiyorum, QR kod hata veriyor")
    assert {"kodu", "cekem", "kodu para"} <= terms
    assert not {"ziraa", "ile", "hata"} & terms  # bank name, function word, generic complaint word
    assert "qr" not in terms  # shorter than three letters
    assert extractor.display("cekem") == "çekemiyorum"  # shown with Turkish letters


class _Store:
    def __init__(self, path: Path) -> None:
        self.posts = JsonlPostRepository(path)
        self.analyses = JsonlAnalysisRepository(path)
        self.classifier = RuleBasedClassifier(RuleSet.load(), MATCHER)
        self.number = 0

    def add(self, text: str, author: str, minutes_ago: float) -> None:
        self.number += 1
        post = make_post(text, author=author, created_at=START - timedelta(minutes=minutes_ago), number=self.number)
        self.posts.add_if_absent(post)
        self.analyses.save(self.classifier.classify(post, post.created_at))

    def service(self, clock: FakeClock, **overrides: object) -> TopicsService:
        stop = TermExtractor.load().stop_stems
        return TopicsService(
            clock=clock,
            posts=self.posts,
            analyses=self.analyses,
            extractor_factory=lambda: TermExtractor(stop),
            settings=TopicSettings(**overrides),  # type: ignore[arg-type]
        )


@pytest.fixture
def store(tmp_path: Path) -> _Store:
    return _Store(tmp_path)


def test_new_problem_surfaces_while_everyday_chatter_does_not(store: _Store, clock: FakeClock) -> None:
    # a week of ordinary posts about cards and ATMs
    for day in range(1, 8):
        for person in range(3):
            store.add("Ziraat kartım ATM'de geçmiyor", f"history-{day}-{person}", day * 24 * 60)
    # now: something new that no rule describes, plus ordinary card posts
    for person in range(5):
        store.add("Ziraat karekod ile para çekme hata veriyor", f"new-{person}", 30 + person)
    for person in range(3):
        store.add("Ziraat kartım ATM'de geçmiyor", f"usual-{person}", 40)

    [topic] = store.service(clock).emerging()
    assert "karekod" in " ".join(topic.terms)
    assert topic.unique_users == 5 and topic.posts == 5
    assert topic.p_value <= 0.001 and topic.expected_users == 0.2
    assert len(topic.examples) == 5 and topic.first_seen == START - timedelta(minutes=34)
    assert sum(topic.categories.values()) == 5


def test_few_people_or_old_posts_are_not_a_topic(store: _Store, clock: FakeClock) -> None:
    for person in range(2):
        store.add("Ziraat karekod ile para çekme hata veriyor", f"new-{person}", 10)
    for person in range(5):
        store.add("Ziraat havale limit düştü", f"old-{person}", 6 * 60)  # outside the two-hour window
    assert store.service(clock).emerging() == []


def test_results_are_cached_briefly(store: _Store, clock: FakeClock) -> None:
    service = store.service(clock, cache_seconds=60)
    assert service.emerging() == []
    for person in range(4):
        store.add("Ziraat karekod ile para çekme hata veriyor", f"new-{person}", 1)
    assert service.emerging() == []  # cached
    clock.advance(seconds=61)
    assert len(service.emerging()) == 1


def test_topics_the_rules_cannot_place_come_first(store: _Store, clock: FakeClock) -> None:
    for person in range(8):
        store.add("Ziraat Mobil açılmıyor, giriş yapamıyorum", f"mobile-{person}", 5)
    for person in range(4):
        store.add("Ziraat karekod ile para çekme hata veriyor", f"new-{person}", 6)
    first, second = store.service(clock).emerging()
    assert "karekod" in " ".join(first.terms) and first.unrecognised_share == 1.0
    assert second.unrecognised_share == 0.0 and "giriş" in second.terms  # Turkish letters kept
