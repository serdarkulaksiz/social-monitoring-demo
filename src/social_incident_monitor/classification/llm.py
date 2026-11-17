"""Provider-neutral LLM classification adapter.

No LLM provider is integrated. ``LLMClassifier`` only defines how a future
``LLMClient`` implementation plugs in; it never fabricates results. Selecting the LLM
classifier without a client fails at start-up (see ``container.build_classifier``).
Sending real posts to an external LLM service requires an explicit, reviewed decision
and is disabled by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..domain.enums import ComplaintSeverity, IssueCategory, ServiceCategory
from ..domain.models import AnalysisResult, Post

CLASSIFIER_TYPE = "llm"


@dataclass(frozen=True)
class LLMVerdict:
    """Structured answer expected from an LLM client."""

    is_bank_related: bool
    is_complaint: bool
    service_category: ServiceCategory
    issue_category: IssueCategory
    severity: ComplaintSeverity
    confidence: float
    confidence_method: str  # e.g. "model_reported" or "calibrated_probability"
    model_version: str


class LLMClient(Protocol):
    def classify_text(self, text: str) -> LLMVerdict: ...


class LLMClassifier:
    classifier_type = CLASSIFIER_TYPE

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def classify(self, post: Post, analyzed_at: datetime) -> AnalysisResult:
        verdict = self._client.classify_text(post.text)
        return AnalysisResult(
            post_id=post.post_id,
            is_bank_related=verdict.is_bank_related,
            is_complaint=verdict.is_complaint,
            service_category=verdict.service_category,
            issue_category=verdict.issue_category,
            severity=verdict.severity,
            confidence=verdict.confidence,
            confidence_method=verdict.confidence_method,
            classifier_type=CLASSIFIER_TYPE,
            classifier_version=verdict.model_version,
            analyzed_at=analyzed_at,
        )
