from __future__ import annotations

from pathlib import Path

import pytest

from social_incident_monitor.application.errors import NotFoundError
from social_incident_monitor.application.feedback_service import (
    FeedbackAwareAnalyses,
    FeedbackService,
    Label,
    Verdict,
)
from social_incident_monitor.application.quality_service import compare
from social_incident_monitor.classification.rule_based import RuleBasedClassifier, RuleSet
from social_incident_monitor.domain.enums import IssueCategory, ServiceCategory
from social_incident_monitor.infrastructure.jsonl.repositories import (
    JsonlAnalysisRepository,
    JsonlFeedbackRepository,
    JsonlPostRepository,
)

from conftest import START, make_post


@pytest.fixture
def stores(tmp_path: Path) -> tuple[JsonlPostRepository, JsonlAnalysisRepository, JsonlFeedbackRepository]:
    posts, analyses, feedback = JsonlPostRepository(tmp_path), JsonlAnalysisRepository(tmp_path), JsonlFeedbackRepository(tmp_path)
    classifier = RuleBasedClassifier(RuleSet.load())
    for number, text in enumerate(["Ziraat ATM paramı vermedi", "Ziraat Bankası faiz oranları nedir?"], start=1):
        post = make_post(text, number=number)
        posts.add_if_absent(post)
        analyses.save(classifier.classify(post, START))
    return posts, analyses, feedback


def _ids(posts: JsonlPostRepository) -> list[str]:
    """Post ids in the order the fixture created them (ATM complaint, then question)."""
    by_text = {post.text: post.post_id for post in posts.find(limit=10)}
    return [by_text["Ziraat ATM paramı vermedi"], by_text["Ziraat Bankası faiz oranları nedir?"]]


def test_labels_override_the_classifier_and_keep_its_prediction(stores) -> None:  # type: ignore[no-untyped-def]
    posts, analyses, feedback = stores
    atm, question = _ids(posts)
    service = FeedbackService(posts, analyses, feedback)
    effective = FeedbackAwareAnalyses(analyses, feedback)

    label = service.submit(atm, Label(Verdict.NOT_COMPLAINT), "Ayşe", "test kartı", START)
    assert (label.predicted_is_complaint, label.predicted_service) == (True, ServiceCategory.ATM)
    assert (label.is_complaint, label.issue_category) == (False, IssueCategory.NONE)
    overridden = effective.get(atm)
    assert overridden is not None and overridden.is_complaint is False and overridden.label_source == "feedback"
    assert analyses.get(atm).is_complaint is True  # type: ignore[union-attr] # classifier output stays as it was
    assert effective.find(is_complaint=True, limit=10) == []

    corrected = service.submit(
        question, Label(Verdict.COMPLAINT, service=ServiceCategory.CUSTOMER_SERVICE, issue=IssueCategory.CUSTOMER_SERVICE_UNREACHABLE), "Ayşe", None, START
    )
    assert corrected.is_complaint and corrected.service_category is ServiceCategory.CUSTOMER_SERVICE
    assert [result.post_id for result in effective.find(is_complaint=True, limit=10)] == [question]

    # the latest label wins and survives a restart
    service.submit(atm, Label(Verdict.CONFIRM), "Mehmet", None, START)
    reloaded = FeedbackAwareAnalyses(analyses, JsonlFeedbackRepository(feedback._file.path.parent))
    assert reloaded.get(atm).is_complaint is True  # type: ignore[union-attr]


def test_not_bank_related_clears_categories_and_signals(stores) -> None:  # type: ignore[no-untyped-def]
    posts, analyses, feedback = stores
    atm, _ = _ids(posts)
    label = FeedbackService(posts, analyses, feedback).submit(atm, Label(Verdict.NOT_BANK_RELATED), "Ayşe", None, START)
    assert (label.is_bank_related, label.is_complaint, label.service_category, label.risk_signals) == (
        False, False, ServiceCategory.UNKNOWN, ()
    )


def test_unknown_post_cannot_be_labelled(stores) -> None:  # type: ignore[no-untyped-def]
    posts, analyses, feedback = stores
    with pytest.raises(NotFoundError):
        FeedbackService(posts, analyses, feedback).submit("missing", Label(Verdict.CONFIRM), "Ayşe", None, START)


def test_quality_comparison() -> None:
    matrix, category_accuracy, by_service = compare(
        [
            (True, "atm", "atm_failure", True, "atm", "atm_failure"),  # TP, right category
            (True, "atm", "atm_failure", True, "card", "card_failure"),  # TP, wrong category
            (True, "mobile_banking", "login_failure", False, "mobile_banking", "none"),  # FP
            (False, "unknown", "none", True, "payments", "payment_failure"),  # FN
            (False, "unknown", "none", False, "unknown", "none"),  # TN
        ]
    )
    assert (matrix.true_positive, matrix.false_positive, matrix.false_negative, matrix.true_negative) == (2, 1, 1, 1)
    assert matrix.precision == pytest.approx(2 / 3) and matrix.recall == pytest.approx(2 / 3) and matrix.accuracy == 0.6
    assert category_accuracy == 0.5
    assert {item.service: (item.labelled_complaints, item.correct_category) for item in by_service} == {
        "atm": (1, 1), "card": (1, 0), "payments": (1, 0)
    }
    empty, none_accuracy, _ = compare([])
    assert empty.precision is None and empty.recall is None and none_accuracy is None


@pytest.mark.parametrize(("service", "issue"), [(None, IssueCategory.ATM_FAILURE), (ServiceCategory.ATM, None), (ServiceCategory.ATM, IssueCategory.NONE)])
def test_complaint_label_must_name_its_categories(service: ServiceCategory | None, issue: IssueCategory | None) -> None:
    with pytest.raises(ValueError, match="service and an issue"):
        Label(Verdict.COMPLAINT, service=service, issue=issue)


def test_label_keeps_the_place_it_was_given(stores) -> None:  # type: ignore[no-untyped-def]
    from social_incident_monitor.application.feedback_service import Sample

    posts, analyses, feedback = stores
    atm, _ = _ids(posts)
    label = FeedbackService(posts, analyses, feedback).submit(atm, Label(Verdict.CONFIRM, sample=Sample.REVIEW_RANDOM), "Ayşe", None, START)
    assert label.sample == "review_random"
