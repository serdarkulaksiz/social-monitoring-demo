"""Analyst feedback: labelling posts and applying those labels everywhere.

An analyst can confirm the classifier, mark a post as a complaint (with the correct
category), as not a complaint, or as not about the bank at all. The latest label per
post wins and overrides the classifier wherever results are read
(``FeedbackAwareAnalyses``), so marking a false alarm lowers the counts of the
affected incident at the next evaluation. The classifier's own result stays stored
unchanged, so its accuracy can still be measured.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ..domain.enums import IssueCategory, ServiceCategory
from ..domain.models import AnalysisResult, Feedback
from .errors import NotFoundError
from .ports import AnalysisRepository, FeedbackRepository, PostRepository

LABEL_SOURCE_FEEDBACK = "feedback"


class Verdict(StrEnum):
    CONFIRM = "confirm"  # the classifier was right
    COMPLAINT = "complaint"  # a complaint, with the categories given in the label
    NOT_COMPLAINT = "not_complaint"  # about the bank, but not a complaint
    NOT_BANK_RELATED = "not_bank_related"  # not about the bank (e.g. sports, agriculture)


class Sample(StrEnum):
    """Where the analyst labelled the post (kept with the label, used to read quality numbers correctly)."""

    REVIEW_RANDOM = "review_random"  # unbiased: random posts mentioning the bank
    REVIEW_UNCERTAIN = "review_uncertain"
    REVIEW_INCIDENTS = "review_incidents"
    REVIEW_DISAGREEMENT = "review_disagreement"  # the text model disagrees with the rules
    INCIDENT_EVIDENCE = "incident_evidence"
    REPUTATION = "reputation"
    OTHER = "other"


@dataclass(frozen=True)
class Label:
    """An analyst's decision. ``confirm`` means every field of the classifier result was checked.

    A ``complaint`` label must name its service and issue: filling them from the
    classifier's own guess would make the classifier look more accurate than it is.
    """

    verdict: Verdict
    service: ServiceCategory | None = None
    issue: IssueCategory | None = None
    risk_signals: tuple[str, ...] | None = None  # None keeps the classifier's signals
    sample: Sample = Sample.OTHER

    def __post_init__(self) -> None:
        if self.verdict is Verdict.COMPLAINT and (self.service is None or self.issue in (None, IssueCategory.NONE)):
            raise ValueError("a complaint label needs both a service and an issue")


def apply_feedback(result: AnalysisResult, feedback: Feedback | None) -> AnalysisResult:
    if feedback is None:
        return result
    return result.model_copy(
        update={
            "is_bank_related": feedback.is_bank_related,
            "is_complaint": feedback.is_complaint,
            "service_category": feedback.service_category,
            "issue_category": feedback.issue_category,
            "risk_signals": feedback.risk_signals,
            "label_source": LABEL_SOURCE_FEEDBACK,
        }
    )


class FeedbackAwareAnalyses:
    """``AnalysisRepository`` view in which analyst labels override classifier results."""

    def __init__(self, analyses: AnalysisRepository, feedback: FeedbackRepository) -> None:
        self._analyses = analyses
        self._feedback = feedback

    def save(self, result: AnalysisResult) -> None:
        self._analyses.save(result)

    def get(self, post_id: str) -> AnalysisResult | None:
        result = self._analyses.get(post_id)
        return apply_feedback(result, self._feedback.get(post_id)) if result is not None else None

    def find(
        self,
        *,
        is_complaint: bool | None = None,
        service_category: ServiceCategory | None = None,
        limit: int,
        offset: int = 0,
    ) -> list[AnalysisResult]:
        matching = [
            result
            for result in self.iter_all()
            if (is_complaint is None or result.is_complaint == is_complaint)
            and (service_category is None or result.service_category == service_category)
        ]
        matching.sort(key=lambda result: result.analyzed_at, reverse=True)
        return matching[offset : offset + limit]

    def iter_all(self) -> Iterable[AnalysisResult]:
        return [apply_feedback(result, self._feedback.get(result.post_id)) for result in self._analyses.iter_all()]


def _new_feedback_id() -> str:
    return f"fb-{uuid.uuid4().hex[:16]}"


class FeedbackService:
    def __init__(
        self,
        posts: PostRepository,
        classifier_results: AnalysisRepository,
        feedback: FeedbackRepository,
        id_factory: Callable[[], str] = _new_feedback_id,
    ) -> None:
        self._posts = posts
        self._results = classifier_results  # raw classifier output, never the feedback-aware view
        self._feedback = feedback
        self._new_id = id_factory

    def submit(self, post_id: str, label: Label, actor: str, note: str | None, now: datetime) -> Feedback:
        if self._posts.get(post_id) is None:
            raise NotFoundError(f"post {post_id} not found")
        predicted = self._results.get(post_id)
        if predicted is None:
            raise NotFoundError(f"post {post_id} has no classification result yet")
        is_bank_related, is_complaint, service, issue, risk = self._resolve(label, predicted)
        feedback = Feedback(
            feedback_id=self._new_id(),
            post_id=post_id,
            verdict=label.verdict.value,
            is_bank_related=is_bank_related,
            is_complaint=is_complaint,
            service_category=service,
            issue_category=issue,
            risk_signals=risk,
            predicted_is_complaint=predicted.is_complaint,
            predicted_service=predicted.service_category,
            predicted_issue=predicted.issue_category,
            predicted_risk_signals=predicted.risk_signals,
            classifier_type=predicted.classifier_type,
            classifier_version=predicted.classifier_version,
            actor=actor,
            note=note,
            created_at=now,
            sample=label.sample.value,
        )
        self._feedback.save(feedback)
        return feedback

    @staticmethod
    def _resolve(
        label: Label, predicted: AnalysisResult
    ) -> tuple[bool, bool, ServiceCategory, IssueCategory, tuple[str, ...]]:
        risk = predicted.risk_signals if label.risk_signals is None else label.risk_signals
        if label.verdict is Verdict.CONFIRM:
            return predicted.is_bank_related, predicted.is_complaint, predicted.service_category, predicted.issue_category, risk
        if label.verdict is Verdict.COMPLAINT:
            assert label.service is not None and label.issue is not None  # guaranteed by Label
            return True, True, label.service, label.issue, risk
        if label.verdict is Verdict.NOT_COMPLAINT:
            return True, False, label.service or predicted.service_category, IssueCategory.NONE, risk
        return False, False, ServiceCategory.UNKNOWN, IssueCategory.NONE, ()
