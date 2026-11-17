from __future__ import annotations

from datetime import UTC, datetime

import pytest

from social_incident_monitor.application.errors import ConfigurationError
from social_incident_monitor.classification.rule_based import RuleBasedClassifier, RuleSet
from social_incident_monitor.domain.enums import ComplaintSeverity, IssueCategory, ServiceCategory

from conftest import make_post

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def classifier() -> RuleBasedClassifier:
    return RuleBasedClassifier(RuleSet.load())


@pytest.mark.parametrize(
    ("text", "service", "issue"),
    [
        ("Ziraat Mobil açılmıyor.", ServiceCategory.MOBILE_BANKING, IssueCategory.APPLICATION_UNAVAILABLE),
        ("Ziraat Bankası FAST işlemi yapamıyorum.", ServiceCategory.TRANSFER, IssueCategory.TRANSFER_FAILURE),
        ("Ziraat ATM paramı vermedi.", ServiceCategory.ATM, IssueCategory.ATM_FAILURE),
        ("ZİRAAT internet şubesine giriş yapamıyorum", ServiceCategory.INTERNET_BANKING, IssueCategory.LOGIN_FAILURE),
        ("Ziraat mobilden havale gitmedi", ServiceCategory.TRANSFER, IssueCategory.TRANSFER_FAILURE),
        ("Ziraat kartım çalışmıyor", ServiceCategory.CARD, IssueCategory.CARD_FAILURE),
        ("Ziraat müşteri hizmetlerine ulaşamıyorum", ServiceCategory.CUSTOMER_SERVICE, IssueCategory.CUSTOMER_SERVICE_UNREACHABLE),
        ("Ziraat ATM çalışmıyor", ServiceCategory.ATM, IssueCategory.ATM_FAILURE),
    ],
)
def test_real_complaints(classifier: RuleBasedClassifier, text: str, service: ServiceCategory, issue: IssueCategory) -> None:
    result = classifier.classify(make_post(text), NOW)
    assert result.is_bank_related
    assert result.is_complaint
    assert result.service_category is service
    assert result.issue_category is issue
    assert result.severity is not ComplaintSeverity.NONE


@pytest.mark.parametrize(
    ("text", "bank_related"),
    [
        ("Ziraat mühendisleri tarım projesi geliştirdi.", False),
        ("Ziraat Türkiye Kupası maçı başladı.", False),
        ("Ziraat Bankası voleybol takımı kazandı.", False),
        ("Ziraat Bankası faiz oranları nedir?", True),
        ("Ziraat mobil düzeldi, artık çalışıyor", True),
        ("Bugün hava çok güzel", False),
    ],
)
def test_non_complaints(classifier: RuleBasedClassifier, text: str, bank_related: bool) -> None:
    result = classifier.classify(make_post(text), NOW)
    assert result.is_complaint is False
    assert result.is_bank_related is bank_related
    assert result.issue_category is IssueCategory.NONE
    assert result.severity is ComplaintSeverity.NONE


def test_confidence_is_labelled_as_heuristic_score(classifier: RuleBasedClassifier) -> None:
    result = classifier.classify(make_post("Ziraat Mobil açılmıyor."), NOW)
    assert result.confidence_method == "heuristic_rule_score"
    assert result.classifier_type == "rule_based"
    assert 0.0 <= result.confidence <= 1.0
    assert "issue:application_unavailable" in result.matched_rules


def test_question_lowers_score_of_complaint(classifier: RuleBasedClassifier) -> None:
    plain = classifier.classify(make_post("Ziraat Mobil açılmıyor"), NOW)
    question = classifier.classify(make_post("Ziraat Mobil neden açılmıyor?"), NOW)
    assert question.is_complaint
    assert question.confidence < plain.confidence


def test_invalid_rules_raise_configuration_error() -> None:
    with pytest.raises(ConfigurationError):
        RuleSet.from_mapping({"rules_version": "x"})


def test_rules_with_unknown_category_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json
    from importlib import resources

    data = json.loads(
        resources.files("social_incident_monitor.classification").joinpath("rules_tr.json").read_text(encoding="utf-8")
    )
    data["services"][0]["category"] = "teleportation"
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        RuleSet.load(path)


@pytest.mark.parametrize(
    "text",
    [
        "Ziraat hesabımdan haberim olmadan para çekilmiş",
        "Ziraat kartım kopyalanmış, yapmadığım harcama var",
        "Ziraat Bankası yetkisiz işlem yapılmış hesabımda",
        "Ziraat adına arayıp dolandırıldım",
    ],
)
def test_fraud_and_unauthorized_transactions(classifier: RuleBasedClassifier, text: str) -> None:
    result = classifier.classify(make_post(text), NOW)
    assert result.is_complaint and result.issue_category is IssueCategory.FRAUD_UNAUTHORIZED
    assert result.severity is ComplaintSeverity.HIGH


def test_ziraat_katilim_is_a_separate_institution(classifier: RuleBasedClassifier) -> None:
    only_katilim = classifier.classify(make_post("Ziraat Katılım mobil uygulaması çalışmıyor"), NOW)
    assert only_katilim.is_bank_related is False and only_katilim.matched_rules == ("other_institution",)
    both = classifier.classify(make_post("Ziraat Katılım değil, Ziraat Bankası mobil çöktü"), NOW)
    assert both.is_bank_related and both.is_complaint


def test_problem_words_inside_hashtags_are_not_problem_phrases(classifier: RuleBasedClassifier) -> None:
    spam = classifier.classify(make_post("Para her kapıyı açar mı? #çöktü Ziraat Bankası Atilla Taş Sezen Taş"), NOW)
    assert spam.is_complaint is False
    real = classifier.classify(make_post("#ziraatbankası mobil çöktü mü?"), NOW)
    assert real.is_bank_related and "issue:application_unavailable" in real.matched_rules


def test_other_banks_are_recorded_and_official_accounts_never_complain(classifier: RuleBasedClassifier) -> None:
    other = classifier.classify(make_post("@YapiKredi ATM'niz çalışmıyor, Ziraat ATM'si tıkır tıkır"), NOW)
    assert "other_bank" in other.matched_rules
    official = make_post("Merhaba, Ziraat Mobil'de yaşanan erişim sorunu giderilmiştir").model_copy(update={"author_handle": "ZiraatDestek"})
    result = classifier.classify(official, NOW)
    assert result.is_bank_related and result.is_complaint is False and "official_account" in result.matched_rules


@pytest.mark.parametrize(
    ("text", "service"),
    [
        ("Ziraat Bankası çöktü", ServiceCategory.ALL_CHANNELS),
        ("Ziraat mobil, ATM ve POS çalışmıyor", ServiceCategory.ALL_CHANNELS),
        ("Ziraat tüm sistem çöktü", ServiceCategory.ALL_CHANNELS),
        ("Ziraat Mobil açılmıyor", ServiceCategory.MOBILE_BANKING),
        ("Ziraat pos cihazı çalışmıyor", ServiceCategory.POS),
    ],
)
def test_general_outage_and_pos_services(classifier: RuleBasedClassifier, text: str, service: ServiceCategory) -> None:
    result = classifier.classify(make_post(text), NOW)
    assert result.is_complaint and result.service_category is service
    assert result.issue_category is IssueCategory.APPLICATION_UNAVAILABLE


def test_fund_topic_is_about_the_bank_but_never_a_complaint(classifier: RuleBasedClassifier) -> None:
    result = classifier.classify(make_post("Ziraat Bankası tp2 ara ödeme yatmadı, rezalet, mağdur olduk"), NOW)
    assert result.is_bank_related and result.is_complaint is False and "topic:fund" in result.matched_rules
