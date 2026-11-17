from __future__ import annotations

from typing import Any

import pytest

from social_incident_monitor.classification.hybrid import HybridClassifier, HybridSettings
from social_incident_monitor.classification.model.text_model import Prediction
from social_incident_monitor.classification.rule_based import RuleBasedClassifier, RuleSet
from social_incident_monitor.domain.enums import ComplaintSeverity, IssueCategory, ServiceCategory

from conftest import START, make_post

RULES = RuleSet.load()


class FakeModel:
    """Answers with fixed probabilities, like a trained model would."""

    version = "fake-1"
    algorithm = "nb"

    def __init__(self, **values: Any) -> None:
        self.values = {"p_bank": 0.95, "p_complaint": 0.5, "service": None, "issue": None, "explanation": (("kelime", 1.0),)}
        self.values.update(values)

    def predict(self, text: str, rules: object) -> Prediction:
        return Prediction(**self.values)


class BrokenModel(FakeModel):
    def predict(self, text: str, rules: object) -> Prediction:
        raise RuntimeError("broken")


def classify(text: str, model: object | None, mode: str = "active") -> Any:
    classifier = HybridClassifier(RuleBasedClassifier(RULES), RULES, model, HybridSettings(mode=mode))  # type: ignore[arg-type]
    return classifier.classify(make_post(text), START)


def test_explicit_rule_wins_when_the_model_is_not_very_sure() -> None:
    result = classify("Ziraat ATM paramı vermedi", FakeModel(p_complaint=0.4))
    assert result.is_complaint and result.issue_category is IssueCategory.ATM_FAILURE
    assert result.classifier_type == "rule_based" and result.text_model["path"] == "rule"


def test_model_rescues_a_missed_complaint() -> None:
    text = "Ziraat bankası belanızı versin hesap numaramı bulamadım tarihi eser uygulama"
    assert classify(text, None).is_complaint is False  # the rules alone miss it
    result = classify(text, FakeModel(p_complaint=0.93, service=("mobile_banking", 0.8), issue=("other", 0.7)))
    assert result.is_complaint and result.is_bank_related
    assert result.service_category is ServiceCategory.MOBILE_BANKING and result.issue_category is IssueCategory.OTHER
    assert result.severity is ComplaintSeverity.LOW and result.confidence == 0.93
    assert result.classifier_type == "hybrid" and result.classifier_version.startswith("tr-1.4+fake-1")
    assert result.text_model["path"] == "model_rescue" and result.text_model["applied"] is True


def test_rescue_needs_enough_certainty() -> None:
    result = classify("Ziraat uygulaması tarihi eser", FakeModel(p_complaint=0.55))
    assert result.is_complaint is False and result.text_model["path"] == "rule"


def test_model_vetoes_a_complaint_found_only_from_generic_words() -> None:
    text = "Ziraat Bankası ile ilgili bir sorun değil, merak ettim"
    assert classify(text, None).is_complaint is True  # "sorun" alone makes the rules call it a complaint
    result = classify(text, FakeModel(p_complaint=0.04))
    assert result.is_complaint is False and result.issue_category is IssueCategory.NONE
    assert result.text_model["path"] == "model_veto"


def test_model_vetoes_brand_word_without_the_full_name() -> None:
    result = classify("Ziraat mobil uygulaması hakkında bir video izledim", FakeModel(p_bank=0.02))
    assert result.is_bank_related is False and result.text_model["path"] == "model_veto"
    kept = classify("Ziraat Bankası mobil uygulaması hakkında video", FakeModel(p_bank=0.02))
    assert kept.is_bank_related is True  # the bank's full name is written: the rules are sure


def test_model_fills_unknown_categories() -> None:
    result = classify("Ziraat Bankası rezalet, yapamıyorum", FakeModel(p_complaint=0.6, service=("fast_eft_transfer", 0.7), issue=("transfer_failure", 0.6)))
    assert result.is_complaint and result.service_category is ServiceCategory.TRANSFER
    assert result.issue_category is IssueCategory.TRANSFER_FAILURE and result.text_model["path"] == "model_category"


def test_shadow_mode_keeps_the_rules_answer_and_records_the_model() -> None:
    text = "Ziraat bankası belanızı versin hesap numaramı bulamadım"
    result = classify(text, FakeModel(p_complaint=0.95), mode="shadow")
    assert result.is_complaint is False and result.classifier_type == "rule_based"
    assert result.text_model["path"] == "model_rescue" and result.text_model["applied"] is False
    assert result.text_model["would_be"]["is_complaint"] is True


@pytest.mark.parametrize("model", [None, BrokenModel()])
def test_without_a_working_model_the_rules_decide(model: object | None) -> None:
    result = classify("Ziraat Mobil açılmıyor", model)
    assert result.is_complaint and result.text_model is None


def test_reputation_posts_stay_with_the_rules() -> None:
    result = classify("Ziraat batıyor mu, paranızı çekin", FakeModel(p_complaint=0.99, p_bank=0.01))
    assert result.risk_signals and result.text_model["path"] == "rule"


def test_categories_are_not_filled_when_the_model_doubts_the_complaint() -> None:
    result = classify("Ziraat Bankası rezalet, yapamıyorum", FakeModel(p_complaint=0.4, service=("atm", 0.9), issue=("atm_failure", 0.9)))
    assert result.is_complaint and result.service_category is ServiceCategory.UNKNOWN
    assert result.text_model["path"] == "rule"


def test_reply_without_the_bank_name_needs_a_sure_model() -> None:
    text = "uygulamanız sabahtan beri açılmıyor, hiçbir işlem yapamıyorum"
    assert classify(text, None).is_bank_related is False  # the rules need the bank's name
    rescued = classify(text, FakeModel(p_bank=0.92, p_complaint=0.95, service=("mobile_banking", 0.8)))
    assert rescued.is_complaint and rescued.is_bank_related and rescued.service_category is ServiceCategory.MOBILE_BANKING
    unsure = classify(text, FakeModel(p_bank=0.5, p_complaint=0.95))
    assert unsure.is_complaint is False and unsure.text_model["path"] == "rule"


def test_model_vetoes_noise_that_matches_a_problem_phrase() -> None:
    text = "Ziraat Bankası sponsorluğundaki dizide kahramanın dünyası çöktü"
    assert classify(text, None).is_complaint is True  # "çöktü" is a problem phrase for the rules
    result = classify(text, FakeModel(p_complaint=0.1))
    assert result.is_complaint is False and result.text_model["path"] == "model_veto"
    assert result.issue_category is IssueCategory.NONE and result.severity is ComplaintSeverity.NONE


def test_model_names_the_service_of_a_complaint_the_rules_found() -> None:
    text = "Ziraat Bankası hesabıma giriş yapamıyorum"
    assert classify(text, None).service_category is ServiceCategory.UNKNOWN
    result = classify(text, FakeModel(p_complaint=0.9, service=("mobile_banking", 0.8)))
    assert result.is_complaint and result.service_category is ServiceCategory.MOBILE_BANKING
    assert result.issue_category is IssueCategory.LOGIN_FAILURE  # the rules' explicit issue stays
    assert result.text_model["path"] == "model_category"


def test_fund_topic_stays_with_the_rules() -> None:
    result = classify("Ziraat Bankası PKV fonunu ne zaman açacak, rezalet", FakeModel(p_complaint=0.99))
    assert result.is_complaint is False and result.text_model["path"] == "rule"
