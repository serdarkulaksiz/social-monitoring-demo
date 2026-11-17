"""Classifier quality from analyst labels, and the queue of posts waiting for review.

Quality numbers compare the classifier's original prediction (stored with each label)
with the analyst's label. They are only as representative as the labelled sample:
reviewing only posts from incidents measures precision on alarms but says little about
missed complaints, so the queue offers a ``random`` mode for an unbiased sample.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from ..domain.models import AnalysisResult, EngagementSnapshot, Feedback, Post
from .ports import AnalysisRepository, Clock, EngagementRepository, FeedbackRepository, IncidentRepository, PostRepository

UNCERTAIN_CONFIDENCE = 0.6
QUEUE_LOOKBACK = timedelta(days=7)


@dataclass(frozen=True)
class ConfusionMatrix:
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    true_negative: int = 0

    @property
    def total(self) -> int:
        return self.true_positive + self.false_positive + self.false_negative + self.true_negative

    @property
    def precision(self) -> float | None:
        predicted = self.true_positive + self.false_positive
        return self.true_positive / predicted if predicted else None

    @property
    def recall(self) -> float | None:
        actual = self.true_positive + self.false_negative
        return self.true_positive / actual if actual else None

    @property
    def accuracy(self) -> float | None:
        return (self.true_positive + self.true_negative) / self.total if self.total else None

    @property
    def f1(self) -> float | None:
        precision, recall = self.precision, self.recall
        if precision is None or recall is None or precision + recall == 0:
            return None if precision is None and recall is None else 0.0
        return 2 * precision * recall / (precision + recall)

    @staticmethod
    def of(pairs: Iterable[tuple[bool, bool]]) -> ConfusionMatrix:
        """``(predicted, actual)`` pairs of one yes/no decision."""
        tp = fp = fn = tn = 0
        for predicted, actual in pairs:
            if predicted and actual:
                tp += 1
            elif predicted:
                fp += 1
            elif actual:
                fn += 1
            else:
                tn += 1
        return ConfusionMatrix(tp, fp, fn, tn)


@dataclass(frozen=True)
class ServiceAccuracy:
    service: str
    labelled_complaints: int
    correct_category: int


@dataclass(frozen=True)
class QualityReport:
    classifier_version: str | None  # None: every version together
    sample: str | None  # None: labels from every place together
    labelled: int
    versions: dict[str, int]
    samples: dict[str, int]  # labels per place they were given (see feedback_service.Sample)
    complaint: ConfusionMatrix
    category_accuracy: float | None  # among true complaints the classifier also caught: same service and issue
    by_service: list[ServiceAccuracy] = field(default_factory=list)


def compare(pairs: Iterable[tuple[bool, str, str, bool, str, str]]) -> tuple[ConfusionMatrix, float | None, list[ServiceAccuracy]]:
    """``(predicted_complaint, predicted_service, predicted_issue, true_complaint, true_service, true_issue)`` tuples."""
    tp = fp = fn = tn = 0
    caught = correct = 0
    per_service: dict[str, list[int]] = {}
    for p_complaint, p_service, p_issue, t_complaint, t_service, t_issue in pairs:
        if p_complaint and t_complaint:
            tp += 1
            caught += 1
            right = p_service == t_service and p_issue == t_issue
            correct += right
            stats = per_service.setdefault(t_service, [0, 0])
            stats[0] += 1
            stats[1] += right
        elif p_complaint:
            fp += 1
        elif t_complaint:
            fn += 1
            per_service.setdefault(t_service, [0, 0])[0] += 1
        else:
            tn += 1
    by_service = [ServiceAccuracy(service, total, right) for service, (total, right) in sorted(per_service.items())]
    return ConfusionMatrix(tp, fp, fn, tn), (correct / caught if caught else None), by_service


class ReviewMode(StrEnum):
    INCIDENTS = "incidents"  # posts counted in active incidents
    UNCERTAIN = "uncertain"  # low-confidence or borderline results
    RANDOM = "random"  # unbiased sample of posts mentioning the bank
    DISAGREEMENT = "disagreement"  # the text model would decide differently from the rules


@dataclass(frozen=True)
class ModelActivity:
    """What the text model did (or, in shadow mode, would have done) over the review period."""

    analysed: int  # posts with a model result
    versions: dict[str, int]
    modes: dict[str, int]
    paths: dict[str, int]  # model_rescue / model_veto / model_category → posts
    applied: int  # changes actually used (active mode)


@dataclass(frozen=True)
class ReviewItem:
    post: Post
    analysis: AnalysisResult
    engagement: EngagementSnapshot | None
    reason: str


class QualityService:
    def __init__(
        self,
        *,
        clock: Clock,
        posts: PostRepository,
        classifier_results: AnalysisRepository,
        effective_results: AnalysisRepository,
        feedback: FeedbackRepository,
        incidents: IncidentRepository,
        engagement: EngagementRepository,
    ) -> None:
        self._clock = clock
        self._posts = posts
        self._classifier = classifier_results
        self._effective = effective_results
        self._feedback = feedback
        self._incidents = incidents
        self._engagement = engagement

    def report(self, classifier_version: str | None = None, sample: str | None = None) -> QualityReport:
        labels = list(self._feedback.iter_latest())
        versions = dict(Counter(label.classifier_version for label in labels).most_common())
        samples = dict(Counter(label.sample for label in labels).most_common())
        selected = [
            label
            for label in labels
            if (classifier_version is None or label.classifier_version == classifier_version)
            and (sample is None or label.sample == sample)
        ]
        matrix, category_accuracy, by_service = compare(_pair(label) for label in selected)
        return QualityReport(
            classifier_version, sample, len(selected), versions, samples, matrix, category_accuracy, by_service
        )

    def feedback(self, *, limit: int, offset: int) -> list[Feedback]:
        return self._feedback.find(limit=limit, offset=offset)

    def queue(self, mode: ReviewMode, limit: int) -> list[ReviewItem]:
        now = self._clock.now()
        candidates = [
            (post, analysis)
            for post in self._posts.find_created_between(now - QUEUE_LOOKBACK, now)
            if self._feedback.get(post.post_id) is None
            and (analysis := self._classifier.get(post.post_id)) is not None
            and "brand" in analysis.matched_rules
        ]
        if mode is ReviewMode.INCIDENTS:
            active = {(incident.service_category, incident.issue_category) for incident in self._incidents.list_active()}
            selected = [
                (post, analysis, "aktif olayda sayılıyor")
                for post, analysis in reversed(candidates)
                if analysis.is_complaint and (analysis.service_category, analysis.issue_category) in active
            ]
        elif mode is ReviewMode.UNCERTAIN:
            selected = [
                (post, analysis, _uncertainty(analysis))
                for post, analysis in reversed(candidates)
                if _uncertainty(analysis)
            ]
        elif mode is ReviewMode.DISAGREEMENT:
            selected = [
                (post, analysis, _disagreement(analysis))
                for post, analysis in reversed(candidates)
                if _disagreement(analysis)
            ]
        else:
            rng = random.Random(now.date().isoformat())  # stable sample within a day
            sample = rng.sample(candidates, min(limit, len(candidates)))
            selected = [(post, analysis, "rastgele örnek") for post, analysis in sample]
        return [
            ReviewItem(post, self._effective.get(post.post_id) or analysis, self._engagement.get(post.post_id), reason)
            for post, analysis, reason in selected[:limit]
        ]


    def model_activity(self) -> ModelActivity:
        now = self._clock.now()
        versions: Counter[str] = Counter()
        modes: Counter[str] = Counter()
        paths: Counter[str] = Counter()
        analysed = applied = 0
        for post in self._posts.find_created_between(now - QUEUE_LOOKBACK, now):
            analysis = self._classifier.get(post.post_id)
            summary = analysis.text_model if analysis is not None else None
            if not summary:
                continue
            analysed += 1
            versions[str(summary.get("version"))] += 1
            modes[str(summary.get("mode"))] += 1
            path = str(summary.get("path", "rule"))
            if path != "rule":
                paths[path] += 1
            applied += bool(summary.get("applied"))
        return ModelActivity(analysed, dict(versions), dict(modes), dict(paths), applied)


_DISAGREEMENT_REASONS = {
    "model_rescue": "model şikâyet diyor, kurallar demiyor",
    "model_veto": "kurallar şikâyet / ilgili diyor, model demiyor",
    "model_category": "model hizmet veya sorunu farklı görüyor",
}


def _disagreement(analysis: AnalysisResult) -> str:
    summary = analysis.text_model or {}
    reason = _DISAGREEMENT_REASONS.get(str(summary.get("path", "")), "")
    probability = summary.get("p_complaint")
    if reason and isinstance(probability, (int, float)):
        reason += f" (şikâyet olasılığı %{round(probability * 100)})"
    return reason


def _uncertainty(analysis: AnalysisResult) -> str:
    if analysis.is_complaint and analysis.confidence <= UNCERTAIN_CONFIDENCE:
        return "düşük güven skoru"
    if not analysis.is_complaint and "generic_problem" in analysis.matched_rules:
        return "sorun ifadesi var ama şikâyet sayılmadı"
    if analysis.is_complaint and "question" in analysis.matched_rules:
        return "soru cümlesi içinde şikâyet"
    return ""


def _pair(label: Feedback) -> tuple[bool, str, str, bool, str, str]:
    return (
        label.predicted_is_complaint,
        label.predicted_service.value,
        label.predicted_issue.value,
        label.is_complaint,
        label.service_category.value,
        label.issue_category.value,
    )

