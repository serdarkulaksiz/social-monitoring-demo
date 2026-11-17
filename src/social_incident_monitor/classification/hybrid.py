"""Rules and the text model deciding together.

The rules run first and stay in charge of what they are sure about. The model is
asked about the rest:

1. The rules found an explicit problem phrase ("kartımı yuttu") → the rules decide,
   unless the model is at least ``veto_p`` sure it is not a complaint (hashtag spam such
   as "#çöktü … Ziraat Bankası" matches problem phrases) → not a complaint (``model_veto``).
2. The rules found no complaint and the model is at least ``rescue_p`` sure it is a
   complaint → complaint (``model_rescue``). A post that does not name the bank (a reply
   to the bank's account) qualifies only if the model is also ``rescue_p`` sure it is
   about the bank. The model's service/issue are used where the rules have none.
3. The rules call it a complaint only from generic words ("rezalet", "sorun") and the
   model is at least ``veto_p`` sure it is not → not a complaint (``model_veto``).
   The same holds for "about the bank" when the bank's full name is not written.
4. Otherwise the rules' answer stands; for a complaint without a known service or
   issue the model fills them in when it is at least ``category_p`` sure
   (``model_category``), provided the model also sees a complaint.
6. For every complaint the model also sees, its service and issue replace the rules'
   when it is at least ``category_p`` sure (the rules' categories are the fallback).
5. No model (missing or damaged file) → rules only. Posts on the fund topic and
   reputation posts always keep the rules' answer.

In ``shadow`` mode the post keeps the rules' answer; what the model *would* have
changed is stored in ``text_model`` so analysts can compare before switching to
``active``. Reputation signals, locations and severity always come from the rules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from ..domain.enums import ComplaintSeverity, IssueCategory, ServiceCategory
from ..domain.models import AnalysisResult, Post
from .model.text_model import Prediction, TextModel
from .rule_based import RuleBasedClassifier, RuleSet

logger = logging.getLogger(__name__)

CLASSIFIER_TYPE = "hybrid"
MODEL_CONFIDENCE_METHOD = "model_probability"
RULE = "rule"
RESCUE = "model_rescue"
VETO = "model_veto"
CATEGORY = "model_category"


@dataclass(frozen=True)
class HybridSettings:
    mode: Literal["shadow", "active"] = "shadow"
    rescue_p: float = 0.6  # chosen on a validation split of the first labelled X data (2026-11-16)
    veto_p: float = 0.7
    category_p: float = 0.3  # several classes share the probability; chosen on validation (2026-11-16)


@dataclass(frozen=True)
class _Decision:
    is_bank_related: bool
    is_complaint: bool
    service: ServiceCategory
    issue: IssueCategory
    path: str
    confidence: float | None  # set when the model decided


class HybridClassifier:
    classifier_type = CLASSIFIER_TYPE

    def __init__(self, rules: RuleBasedClassifier, rule_set: RuleSet, model: TextModel | None, settings: HybridSettings) -> None:
        self._rules = rules
        self._rule_set = rule_set
        self._model = model
        self._settings = settings

    @property
    def model(self) -> TextModel | None:
        return self._model

    def classify(self, post: Post, analyzed_at: datetime) -> AnalysisResult:
        ruled = self._rules.classify(post, analyzed_at)
        if self._model is None:
            return ruled
        try:
            prediction = self._model.predict(post.text, ruled)
        except Exception:  # a broken model must never stop classification
            logger.exception("text model failed; using the rules' result")
            return ruled
        decision = decide(ruled, prediction, self._settings)
        summary = _summary(self._model, self._settings, prediction, decision, ruled)
        if self._settings.mode == "shadow" or decision.path == RULE:
            return ruled.model_copy(update={"text_model": summary})
        return self._apply(ruled, decision, summary)

    def _apply(self, ruled: AnalysisResult, decision: _Decision, summary: dict[str, Any]) -> AnalysisResult:
        severity = self._rule_set.issue_severity(decision.issue) if decision.is_complaint else ComplaintSeverity.NONE
        update: dict[str, Any] = {
            "is_bank_related": decision.is_bank_related,
            "is_complaint": decision.is_complaint,
            "service_category": decision.service,
            "issue_category": decision.issue,
            "severity": severity,
            "classifier_type": CLASSIFIER_TYPE,
            "classifier_version": f"{ruled.classifier_version}+{self._model.version if self._model else '-'}",
            "text_model": summary,
        }
        if decision.confidence is not None:
            update["confidence"] = round(min(max(decision.confidence, 0.0), 1.0), 2)
            update["confidence_method"] = MODEL_CONFIDENCE_METHOD
        return ruled.model_copy(update=update)


def decide(ruled: AnalysisResult, prediction: Prediction, settings: HybridSettings) -> _Decision:
    decision = _decide(ruled, prediction, settings)
    return _fill_categories(decision, prediction, settings)


def _fill_categories(decision: _Decision, prediction: Prediction, settings: HybridSettings) -> _Decision:
    """Any complaint, also one the rules found: the model's service and issue are used when it is at
    least ``category_p`` sure. The rules pick the first keyword in a fixed order ("mobil, ATM, POS
    çöktü" → ATM); the model learnt the analysts' choice. The rules' categories remain the fallback.
    """
    if not decision.is_complaint or (prediction.p_complaint is not None and prediction.p_complaint < 0.5):
        return decision
    service = _service(decision.service, prediction, settings, replace_known=True)
    issue = _issue(decision.issue, prediction, settings)
    if service == decision.service and issue == decision.issue:
        return decision
    path = CATEGORY if decision.path == RULE else decision.path
    return _Decision(decision.is_bank_related, True, service, issue, path, decision.confidence)


def _decide(ruled: AnalysisResult, prediction: Prediction, settings: HybridSettings) -> _Decision:
    rules = set(ruled.matched_rules)
    explicit_issue = any(rule.startswith("issue:") for rule in rules)
    names_bank = "brand" in rules
    full_name = "bank_entity" in rules
    keep = _Decision(ruled.is_bank_related, ruled.is_complaint, ruled.service_category, ruled.issue_category, RULE, None)
    p_bank, p_complaint = prediction.p_bank, prediction.p_complaint
    if ruled.risk_signals or "topic:fund" in rules:
        return keep  # reputation posts and the fund topic stay with the rules (never an alarm)
    if explicit_issue:
        # 1. the rules are sure, unless the model is very sure they matched noise
        if ruled.is_complaint and p_complaint is not None and 1.0 - p_complaint >= settings.veto_p:
            return _Decision(ruled.is_bank_related, False, ruled.service_category, IssueCategory.NONE, VETO, 1.0 - p_complaint)
        return keep
    # Replies to the bank's own account often do not name it ("uygulama açılmıyor");
    # the model may decide those only when it is sure the post is about the bank.
    if not names_bank and (p_bank is None or p_bank < settings.rescue_p):
        return keep
    # 3. veto "about the bank" when only the brand word (not the full name) is there
    if ruled.is_bank_related and not full_name and p_bank is not None and 1.0 - p_bank >= settings.veto_p:
        return _Decision(False, False, ServiceCategory.UNKNOWN, IssueCategory.NONE, VETO, 1.0 - p_bank)
    # 3. veto a complaint found only from generic words
    if ruled.is_complaint and p_complaint is not None and 1.0 - p_complaint >= settings.veto_p:
        return _Decision(ruled.is_bank_related, False, ruled.service_category, IssueCategory.NONE, VETO, 1.0 - p_complaint)
    # 2. rescue a complaint the rules missed
    if not ruled.is_complaint and p_complaint is not None and p_complaint >= settings.rescue_p:
        if ruled.is_bank_related or (p_bank is not None and p_bank >= settings.rescue_p):
            service = _service(ruled.service_category, prediction, settings)
            issue = _issue(IssueCategory.NONE, prediction, settings)
            return _Decision(True, True, service, issue, RESCUE, p_complaint)
        return keep
    # 4. fill in a complaint's unknown categories (only when the model also sees a complaint)
    if ruled.is_complaint and (p_complaint is None or p_complaint >= 0.5):
        service = _service(ruled.service_category, prediction, settings)
        issue = ruled.issue_category
        if issue in (IssueCategory.OTHER, IssueCategory.NONE) or service != ruled.service_category:
            issue = _issue(issue, prediction, settings)
        if service != ruled.service_category or issue != ruled.issue_category:
            return _Decision(True, True, service, issue, CATEGORY, None)
    return keep


def _service(current: ServiceCategory, prediction: Prediction, settings: HybridSettings, replace_known: bool = False) -> ServiceCategory:
    if prediction.service is None or (not replace_known and current not in (ServiceCategory.UNKNOWN, ServiceCategory.OTHER)):
        return current
    label, probability = prediction.service
    try:
        guess = ServiceCategory(label)
    except ValueError:
        return current
    return guess if probability >= settings.category_p and guess is not ServiceCategory.REPUTATION else current


def _issue(current: IssueCategory, prediction: Prediction, settings: HybridSettings) -> IssueCategory:
    fallback = IssueCategory.OTHER if current is IssueCategory.NONE else current
    if prediction.issue is None:
        return fallback
    label, probability = prediction.issue
    try:
        guess = IssueCategory(label)
    except ValueError:
        return fallback
    usable = probability >= settings.category_p and guess not in (IssueCategory.NONE, IssueCategory.SOLVENCY_RUMOR,
                                                                   IssueCategory.DEPOSIT_FLIGHT, IssueCategory.RUN_CALL)
    return guess if usable else fallback


def _summary(model: TextModel, settings: HybridSettings, prediction: Prediction, decision: _Decision, ruled: AnalysisResult) -> dict[str, Any]:
    changes = decision.path != RULE
    return {
        "version": model.version,
        "mode": settings.mode,
        "path": decision.path,
        "applied": changes and settings.mode == "active",
        "p_bank": _round(prediction.p_bank),
        "p_complaint": _round(prediction.p_complaint),
        "service": list(prediction.service[:1]) + [_round(prediction.service[1])] if prediction.service else None,
        "issue": list(prediction.issue[:1]) + [_round(prediction.issue[1])] if prediction.issue else None,
        "explanation": [[word, value] for word, value in prediction.explanation],
        "would_be": {
            "is_bank_related": decision.is_bank_related,
            "is_complaint": decision.is_complaint,
            "service_category": decision.service.value,
            "issue_category": decision.issue.value,
        }
        if changes
        else None,
        "rules": {"is_bank_related": ruled.is_bank_related, "is_complaint": ruled.is_complaint},
    }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)
