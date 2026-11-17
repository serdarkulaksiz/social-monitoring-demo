"""Read-only incident views for review and audit: timeline, evidence, timings, report."""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..domain.enums import ServiceCategory
from ..domain.models import AnalysisResult, EngagementSnapshot, Incident, IncidentAuditEvent, Post
from .errors import NotFoundError
from .ports import AnalysisRepository, AuditLog, Clock, EngagementRepository, IncidentRepository, PostRepository

DISCLAIMER = (
    "This report summarises public social media signals. It supports, but does not replace, "
    "the decision whether an incident must be reported to a regulator."
)


@dataclass(frozen=True)
class EvidencePost:
    post: Post
    analysis: AnalysisResult
    engagement: EngagementSnapshot | None


@dataclass(frozen=True)
class IncidentEvidence:
    period_start: datetime
    period_end: datetime
    total_posts: int
    unique_users: int
    posts: list[EvidencePost]  # chronological, at most ``limit``


@dataclass(frozen=True)
class IncidentTimings:
    time_to_detect: timedelta | None  # first complaint → incident opened
    time_to_acknowledge: timedelta | None  # opened → acknowledged
    time_to_resolve: timedelta | None  # opened → resolved

    @classmethod
    def of(cls, incident: Incident) -> IncidentTimings:
        def since_open(moment: datetime | None) -> timedelta | None:
            return moment - incident.opened_at if moment is not None else None

        detect = incident.opened_at - incident.first_complaint_at if incident.first_complaint_at else None
        return cls(detect, since_open(incident.acknowledged_at), since_open(incident.resolved_at))


@dataclass(frozen=True)
class IncidentMetrics:
    period_days: int
    incidents: int
    acknowledged: int
    resolved: int
    median_time_to_detect: timedelta | None
    median_time_to_acknowledge: timedelta | None
    median_time_to_resolve: timedelta | None


@dataclass(frozen=True)
class IncidentReport:
    generated_at: datetime
    incident: Incident
    timings: IncidentTimings
    timeline: list[IncidentAuditEvent]
    evidence: IncidentEvidence
    audit_trail_verified: bool
    disclaimer: str = DISCLAIMER


class IncidentViews:
    def __init__(
        self,
        *,
        clock: Clock,
        incidents: IncidentRepository,
        posts: PostRepository,
        analyses: AnalysisRepository,
        engagement: EngagementRepository,
        audit: AuditLog,
        matches: Callable[[Incident, AnalysisResult], bool] | None = None,
    ) -> None:
        self._clock = clock
        self._incidents = incidents
        self._posts = posts
        self._analyses = analyses
        self._engagement = engagement
        self._audit = audit
        self._matches = matches or _same_complaint_group

    def get(self, incident_id: str) -> Incident:
        incident = self._incidents.get(incident_id)
        if incident is None:
            raise NotFoundError(f"incident {incident_id} not found")
        return incident

    def timeline(self, incident_id: str) -> list[IncidentAuditEvent]:
        self.get(incident_id)
        return self._audit.events(incident_id)

    def evidence(self, incident_id: str, limit: int = 100) -> IncidentEvidence:
        incident = self.get(incident_id)
        start = incident.opened_at - timedelta(minutes=incident.window_minutes)
        if incident.first_complaint_at is not None:
            start = min(start, incident.first_complaint_at - timedelta(seconds=1))
        end = incident.resolved_at or self._clock.now()
        matching: list[EvidencePost] = []
        for post in self._posts.find_created_between(start, end):
            analysis = self._analyses.get(post.post_id)
            if analysis is not None and self._matches(incident, analysis):
                matching.append(EvidencePost(post, analysis, self._engagement.get(post.post_id)))
        return IncidentEvidence(
            period_start=start,
            period_end=end,
            total_posts=len(matching),
            unique_users=len({item.post.author_id for item in matching}),
            posts=matching[:limit],
        )

    def metrics(self, days: int) -> IncidentMetrics:
        since = self._clock.now() - timedelta(days=days)
        selected = [incident for incident in self._incidents.find(limit=100_000) if incident.opened_at >= since]
        timings = [IncidentTimings.of(incident) for incident in selected]
        return IncidentMetrics(
            period_days=days,
            incidents=len(selected),
            acknowledged=sum(1 for incident in selected if incident.acknowledged_at is not None),
            resolved=sum(1 for incident in selected if incident.resolved_at is not None),
            median_time_to_detect=_median([timing.time_to_detect for timing in timings]),
            median_time_to_acknowledge=_median([timing.time_to_acknowledge for timing in timings]),
            median_time_to_resolve=_median([timing.time_to_resolve for timing in timings]),
        )

    def audit_verified(self) -> bool:
        return self._audit.verify().ok

    def report(self, incident_id: str, evidence_limit: int = 50) -> IncidentReport:
        incident = self.get(incident_id)
        return IncidentReport(
            generated_at=self._clock.now(),
            incident=incident,
            timings=IncidentTimings.of(incident),
            timeline=self._audit.events(incident_id),
            evidence=self.evidence(incident_id, evidence_limit),
            audit_trail_verified=self.audit_verified(),
        )


def _same_complaint_group(incident: Incident, analysis: AnalysisResult) -> bool:
    if incident.service_category is ServiceCategory.REPUTATION:
        return incident.issue_category.value in analysis.risk_signals
    return (
        analysis.is_complaint
        and analysis.service_category == incident.service_category
        and analysis.issue_category == incident.issue_category
    )


def _median(values: list[timedelta | None]) -> timedelta | None:
    present = [value.total_seconds() for value in values if value is not None]
    return timedelta(seconds=statistics.median(present)) if present else None
