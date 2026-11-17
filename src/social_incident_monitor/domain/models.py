"""Domain entities.

Entities are immutable Pydantic models: validation and JSON round-tripping come for
free, and no entity depends on HTTP, storage or a concrete classifier.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .enums import (
    AlertStatus,
    AlertType,
    AuditEventType,
    CollectorRunStatus,
    ComplaintSeverity,
    IncidentStatus,
    IssueCategory,
    ServiceCategory,
    Severity,
)

AUDIT_GENESIS_HASH = "0" * 64


class Entity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


def make_post_id(source: str, source_post_id: str) -> str:
    """Deterministic internal id; identical (source, source_post_id) always maps to the same id."""
    digest = hashlib.sha256(f"{source}\x1f{source_post_id}".encode("utf-8")).hexdigest()
    return digest[:32]


class Post(Entity):
    post_id: str
    source: str
    source_post_id: str
    author_id: str  # platform id as delivered (Bluesky DID); distinct users are counted by it
    text: str
    created_at: AwareDatetime
    collected_at: AwareDatetime
    ingested_at: AwareDatetime
    url: str | None = None
    schema_version: str = "1.0"
    batch_id: str | None = None
    author_handle: str | None = None
    author_display_name: str | None = None
    query_id: str | None = None  # the sender's search/filter that found the post


class EngagementSnapshot(Entity):
    """Public interaction counters of a post at ``observed_at``; the newest snapshot is current."""

    post_id: str
    observed_at: AwareDatetime
    like_count: int = Field(ge=0)
    repost_count: int = Field(ge=0)
    reply_count: int = Field(ge=0)
    quote_count: int = Field(ge=0)

    @property
    def amplification(self) -> int:
        """How far the post spread: reposts and quotes put it in front of other people."""
        return self.repost_count + self.quote_count


class AnalysisResult(Entity):
    post_id: str
    is_bank_related: bool
    is_complaint: bool
    service_category: ServiceCategory
    issue_category: IssueCategory
    severity: ComplaintSeverity
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_method: str
    classifier_type: str
    classifier_version: str
    matched_rules: tuple[str, ...] = ()
    analyzed_at: AwareDatetime
    risk_signals: tuple[str, ...] = ()  # reputation / deposit-risk signals found in the text
    locations: tuple[str, ...] = ()  # province keys mentioned in the text (a hint, not a verified location)
    label_source: str = "classifier"  # "feedback" when an analyst's label overrides the classifier
    text_model: dict[str, Any] | None = None  # the text model's view (probabilities, explanation, decision path)


class Feedback(Entity):
    """An analyst's label for one post. The latest label per post overrides the classifier."""

    feedback_id: str
    post_id: str
    verdict: str  # confirm | complaint | not_complaint | not_bank_related
    is_bank_related: bool
    is_complaint: bool
    service_category: ServiceCategory
    issue_category: IssueCategory
    risk_signals: tuple[str, ...] = ()
    predicted_is_complaint: bool
    predicted_service: ServiceCategory
    predicted_issue: IssueCategory
    predicted_risk_signals: tuple[str, ...] = ()
    classifier_type: str
    classifier_version: str
    actor: str
    note: str | None = None
    created_at: AwareDatetime
    # Where the analyst saw the post. Only "review_random" labels form an unbiased sample;
    # labels given while reviewing alarms over-represent the classifier's mistakes.
    sample: str = "other"


class Incident(Entity):
    incident_id: str
    service_category: ServiceCategory
    issue_category: IssueCategory
    status: IncidentStatus
    severity: Severity  # highest level reached while active
    current_level: Severity  # level of the latest evaluation
    opened_at: AwareDatetime
    updated_at: AwareDatetime
    last_triggered_at: AwareDatetime
    acknowledged_at: AwareDatetime | None = None
    resolved_at: AwareDatetime | None = None
    resolution_reason: str | None = None
    window_minutes: int
    complaints_count: int = Field(ge=0)
    unique_users_count: int = Field(ge=0)
    peak_unique_users_count: int = Field(ge=0)
    detection_reason: str
    version: int = Field(default=1, ge=1)
    # Audit fields (absent in records written before they existed).
    detection_rule: str | None = None
    detection_details: dict[str, Any] = Field(default_factory=dict)  # structured reason, rendered per language
    first_complaint_at: AwareDatetime | None = None  # earliest complaint of the triggering window
    peak_at: AwareDatetime | None = None
    acknowledged_by: str | None = None
    resolved_by: str | None = None
    resolution_note: str | None = None


class IncidentAuditEvent(Entity):
    """One entry of the append-only, hash-chained incident audit trail.

    ``record_hash = sha256(previous_hash + canonical JSON of every other field)``; the
    first entry links to ``AUDIT_GENESIS_HASH``. Changing, removing or reordering an
    entry breaks the chain, which ``verify-audit`` detects.
    """

    event_id: str
    incident_id: str
    event_type: AuditEventType
    occurred_at: AwareDatetime
    actor: str
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    previous_hash: str
    record_hash: str

    def computed_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"record_hash"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256((self.previous_hash + canonical).encode("utf-8")).hexdigest()


class Alert(Entity):
    alarm_id: str
    alert_type: AlertType
    incident_id: str | None
    service: str
    issue_category: str
    severity: Severity
    detected_at: AwareDatetime
    complaints_count: int = Field(ge=0)
    unique_users_count: int = Field(ge=0)
    detection_reason: str
    status: AlertStatus
    updated_at: AwareDatetime
    version: int = Field(default=1, ge=1)


class QueryRun(Entity):
    query_id: str
    status: str
    fetched_count: int = Field(ge=0)
    truncated: bool = False
    error_message: str | None = None


class CollectorRun(Entity):
    run_id: str
    source: str
    collector_version: str | None = None
    started_at: AwareDatetime
    finished_at: AwareDatetime
    received_at: AwareDatetime
    status: CollectorRunStatus
    queries: tuple[QueryRun, ...] = ()
    fetched_count: int = Field(ge=0)
    new_count: int = Field(ge=0)
    sent_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    error_message: str | None = None


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime is not allowed; use timezone-aware UTC values")
    return value.astimezone(UTC)
