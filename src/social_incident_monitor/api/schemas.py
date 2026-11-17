"""HTTP request/response models (contract v1.1). Kept separate from domain entities."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from ..domain.enums import (
    REPUTATION_ISSUES,
    AlertStatus,
    AlertType,
    AuditEventType,
    CollectorHealthStatus,
    CollectorRunStatus,
    ComplaintSeverity,
    IncidentStatus,
    IssueCategory,
    ServiceCategory,
    Severity,
)

CONTRACT_VERSION = "1.1"
SOURCE_PATTERN = r"^[a-z][a-z0-9_]{1,31}$"
SchemaVersion = Literal["1.0", "1.1"]  # 1.1 only adds optional fields, so 1.0 payloads stay valid


# ---------------------------------------------------------------- contract inputs


class EngagementV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    like_count: int = Field(ge=0)
    repost_count: int = Field(ge=0)
    reply_count: int = Field(ge=0)
    quote_count: int = Field(ge=0)


class SocialPostV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: SchemaVersion | None = None  # missing → the batch's schema_version applies
    source: str = Field(pattern=SOURCE_PATTERN)
    source_post_id: str = Field(min_length=1, max_length=512)
    author_id: str = Field(min_length=1, max_length=256)
    author_handle: str | None = Field(default=None, max_length=253)
    author_display_name: str | None = Field(default=None, max_length=640)
    text: str = Field(min_length=1, max_length=10000)
    created_at: AwareDatetime
    collected_at: AwareDatetime
    url: str | None = Field(default=None, max_length=2048)
    engagement: EngagementV1 | None = None
    query_id: str | None = Field(default=None, min_length=1, max_length=64)  # sender's search/filter; not used for classification


class PostsBatchRequest(BaseModel):
    """Envelope is validated as a whole; posts are validated one by one."""

    model_config = ConfigDict(extra="forbid")

    schema_version: SchemaVersion
    batch_id: str = Field(min_length=1, max_length=64)
    posts: list[dict[str, Any]] = Field(min_length=1, max_length=500)


class EngagementUpdateV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(pattern=SOURCE_PATTERN)
    source_post_id: str = Field(min_length=1, max_length=512)
    observed_at: AwareDatetime
    engagement: EngagementV1


class EngagementBatchRequest(BaseModel):
    """Envelope is validated as a whole; updates are validated one by one."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"]
    batch_id: str = Field(min_length=1, max_length=64)
    updates: list[dict[str, Any]] = Field(min_length=1, max_length=500)


class QueryRunResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(min_length=1, max_length=64)
    status: Literal["success", "failed"]
    fetched_count: int = Field(ge=0)
    truncated: bool = False
    error_message: str | None = Field(default=None, max_length=1000)


class CollectorRunReportV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: SchemaVersion
    run_id: str = Field(min_length=1, max_length=64)
    source: str = Field(pattern=SOURCE_PATTERN)
    collector_version: str | None = Field(default=None, max_length=32)
    started_at: AwareDatetime
    finished_at: AwareDatetime
    status: CollectorRunStatus
    queries: list[QueryRunResultV1] = Field(default_factory=list)
    fetched_count: int = Field(ge=0)
    new_count: int = Field(ge=0)
    sent_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    error_message: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------- operator inputs


def _single_line(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("must not be empty")
    if any(ord(char) < 32 or ord(char) == 127 for char in cleaned):
        raise ValueError("must not contain control characters")
    return cleaned


class IncidentActionIn(BaseModel):
    """Optional body of acknowledge/resolve. Without a login, ``actor`` is the name the person enters."""

    model_config = ConfigDict(extra="forbid")

    actor: str = Field(default="api", min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("actor")
    @classmethod
    def _actor(cls, value: str) -> str:
        return _single_line(value)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class FeedbackIn(BaseModel):
    """An analyst's label for one post. ``service_category``/``issue_category`` matter for ``complaint``."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["confirm", "complaint", "not_complaint", "not_bank_related"]
    service_category: ServiceCategory | None = Field(default=None, description="required for 'complaint'")
    issue_category: IssueCategory | None = Field(default=None, description="required for 'complaint'")
    risk_signals: list[str] | None = Field(default=None, max_length=10, description="None keeps the classifier's signals")
    sample: Literal["review_random", "review_uncertain", "review_incidents", "review_disagreement", "incident_evidence", "reputation", "other"] = "other"
    actor: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _complaint_needs_categories(self) -> FeedbackIn:
        if self.verdict == "complaint" and (self.service_category is None or self.issue_category in (None, IssueCategory.NONE)):
            raise ValueError("a 'complaint' label needs service_category and issue_category")
        return self

    @field_validator("actor")
    @classmethod
    def _actor(cls, value: str) -> str:
        return _single_line(value)

    @field_validator("risk_signals")
    @classmethod
    def _risk_signals(cls, value: list[str] | None) -> list[str] | None:
        allowed = {issue.value for issue in REPUTATION_ISSUES}
        if value is not None and not set(value) <= allowed:
            raise ValueError(f"risk signals must be among {sorted(allowed)}")
        return value


class IncidentNoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("actor")
    @classmethod
    def _actor(cls, value: str) -> str:
        return _single_line(value)

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


# ---------------------------------------------------------------- responses


class OutModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RejectedItem(OutModel):
    index: int
    source_post_id: str | None = None
    reason: str


class PostsBatchResponse(OutModel):
    batch_id: str
    received: int
    accepted: int
    duplicates: int
    rejected: list[RejectedItem]
    incidents_opened: int = 0
    alerts_created: int = 0


class EngagementBatchResponse(OutModel):
    batch_id: str
    received: int
    applied: int
    stale: int
    unknown: int
    rejected: list[RejectedItem]


class CollectorRunAccepted(OutModel):
    run_id: str
    stored: bool


class EngagementOut(OutModel):
    observed_at: datetime
    like_count: int
    repost_count: int
    reply_count: int
    quote_count: int


class PostOut(OutModel):
    post_id: str
    source: str
    source_post_id: str
    author_id: str
    author_handle: str | None
    author_display_name: str | None
    text: str
    created_at: datetime
    collected_at: datetime
    ingested_at: datetime
    url: str | None
    query_id: str | None = None
    engagement: EngagementOut | None = None


class AnalysisOut(OutModel):
    post_id: str
    is_bank_related: bool
    is_complaint: bool
    service_category: ServiceCategory
    issue_category: IssueCategory
    severity: ComplaintSeverity
    confidence: float
    confidence_method: str
    classifier_type: str
    classifier_version: str
    matched_rules: list[str]
    analyzed_at: datetime
    risk_signals: list[str]
    locations: list[str]
    label_source: str
    text_model: dict[str, Any] | None = None


class FeedbackOut(OutModel):
    feedback_id: str
    post_id: str
    verdict: str
    is_bank_related: bool
    is_complaint: bool
    service_category: ServiceCategory
    issue_category: IssueCategory
    risk_signals: list[str]
    predicted_is_complaint: bool
    predicted_service: ServiceCategory
    predicted_issue: IssueCategory
    predicted_risk_signals: list[str]
    classifier_type: str
    classifier_version: str
    actor: str
    note: str | None
    created_at: datetime
    sample: str


class ConfusionMatrixOut(BaseModel):
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int
    precision: float | None
    recall: float | None
    accuracy: float | None


class ServiceAccuracyOut(OutModel):
    service: str
    labelled_complaints: int
    correct_category: int


class QualityOut(BaseModel):
    classifier_version: str | None
    sample: str | None
    labelled: int
    versions: dict[str, int]
    samples: dict[str, int]
    complaint: ConfusionMatrixOut
    category_accuracy: float | None
    by_service: list[ServiceAccuracyOut]


class IncidentOut(OutModel):
    incident_id: str
    service_category: ServiceCategory
    issue_category: IssueCategory
    status: IncidentStatus
    severity: Severity
    current_level: Severity
    opened_at: datetime
    updated_at: datetime
    last_triggered_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    resolution_reason: str | None
    window_minutes: int
    complaints_count: int
    unique_users_count: int
    peak_unique_users_count: int
    detection_reason: str
    version: int
    detection_rule: str | None
    detection_details: dict[str, Any]
    first_complaint_at: datetime | None
    peak_at: datetime | None
    acknowledged_by: str | None
    resolved_by: str | None
    resolution_note: str | None


class AuditEventOut(OutModel):
    event_id: str
    incident_id: str
    event_type: AuditEventType
    occurred_at: datetime
    actor: str
    summary: str
    details: dict[str, Any]
    previous_hash: str
    record_hash: str


class EvidencePostOut(BaseModel):
    post: PostOut
    analysis: AnalysisOut


class ReviewItemOut(BaseModel):
    post: PostOut
    analysis: AnalysisOut
    reason: str


class IncidentEvidenceOut(BaseModel):
    period_start: datetime
    period_end: datetime
    total_posts: int
    unique_users: int
    posts: list[EvidencePostOut]


class IncidentTimingsOut(BaseModel):
    time_to_detect_seconds: float | None
    time_to_acknowledge_seconds: float | None
    time_to_resolve_seconds: float | None


class IncidentMetricsOut(BaseModel):
    period_days: int
    incidents: int
    acknowledged: int
    resolved: int
    median_time_to_detect_seconds: float | None
    median_time_to_acknowledge_seconds: float | None
    median_time_to_resolve_seconds: float | None


class IncidentReportOut(BaseModel):
    generated_at: datetime
    incident: IncidentOut
    timings: IncidentTimingsOut
    timeline: list[AuditEventOut]
    evidence: IncidentEvidenceOut
    audit_trail_verified: bool
    disclaimer: str


class AlertOut(OutModel):
    alarm_id: str
    alert_type: AlertType
    incident_id: str | None
    service: str
    issue_category: str
    severity: Severity
    detected_at: datetime
    complaints_count: int
    unique_users_count: int
    detection_reason: str
    status: AlertStatus
    updated_at: datetime


class QueryRunOut(OutModel):
    query_id: str
    status: str
    fetched_count: int
    truncated: bool
    error_message: str | None


class CollectorRunOut(OutModel):
    run_id: str
    source: str
    collector_version: str | None
    started_at: datetime
    finished_at: datetime
    received_at: datetime
    status: CollectorRunStatus
    queries: list[QueryRunOut]
    fetched_count: int
    new_count: int
    sent_count: int
    pending_count: int
    error_message: str | None


class CollectorHealthOut(OutModel):
    source: str
    status: CollectorHealthStatus
    detail: str
    last_run_at: datetime | None
    last_run_status: str | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_fetched_count: int
    last_sent_count: int
    pending_count: int
    consecutive_failures: int
    last_error: str | None


class GroupDensityOut(OutModel):
    service: str
    issue: str
    complaints_count: int
    unique_users_count: int


class WindowDensityOut(OutModel):
    window_minutes: int
    complaints_count: int
    unique_users_count: int
    groups: list[GroupDensityOut]


class LocationCountOut(OutModel):
    key: str
    name: str
    unique_users: int
    posts: int


class TopicOut(BaseModel):
    label: str
    terms: list[str]
    unique_users: int
    posts: int
    expected_users: float
    p_value: float
    first_seen: datetime
    categories: dict[str, int]
    unrecognised_share: float
    examples: list[PostOut]


class BaselineStatusOut(OutModel):
    ready: bool
    history_days: float
    min_history_days: int


class StatsOut(OutModel):
    generated_at: datetime
    synthetic_mode: bool
    total_posts: int
    analyzed_posts: int
    bank_related_posts: int
    complaints: int
    complaints_by_service: dict[str, int]
    complaints_by_issue: dict[str, int]
    windows: list[WindowDensityOut]
    open_incidents: int
    active_alerts: int
    collector_health: list[CollectorHealthOut]
    storage_corrupt_lines: dict[str, int]
    baseline: BaselineStatusOut | None


class Page[T](BaseModel):
    items: list[T]
    limit: int
    offset: int
    count: int


class HealthOut(BaseModel):
    status: Literal["ok"]
    version: str
    contract_version: str
