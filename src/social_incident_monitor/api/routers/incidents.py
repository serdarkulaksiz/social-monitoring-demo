"""Incidents, their lifecycle transitions and audit views."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response

from ...application.incident_views import IncidentEvidence, IncidentReport, IncidentTimings
from ...dashboard.report_markdown import render_incident_markdown
from ...domain.enums import IncidentStatus
from ..dependencies import Incidents, Monitoring, Queries
from ..schemas import (
    AnalysisOut,
    AuditEventOut,
    EvidencePostOut,
    IncidentActionIn,
    IncidentEvidenceOut,
    IncidentMetricsOut,
    IncidentNoteIn,
    IncidentOut,
    IncidentReportOut,
    IncidentTimingsOut,
    Page,
)
from .posts import post_out

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])


@router.get("", response_model=Page[IncidentOut])
def list_incidents(
    queries: Queries,
    status: IncidentStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[IncidentOut]:
    items = queries.incidents(status=status, limit=limit, offset=offset)
    return Page[IncidentOut](
        items=[IncidentOut.model_validate(item) for item in items], limit=limit, offset=offset, count=len(items)
    )


@router.get("/metrics", response_model=IncidentMetricsOut)
def incident_metrics(views: Incidents, days: Annotated[int, Query(ge=1, le=365)] = 30) -> IncidentMetricsOut:
    metrics = views.metrics(days)
    return IncidentMetricsOut(
        period_days=metrics.period_days,
        incidents=metrics.incidents,
        acknowledged=metrics.acknowledged,
        resolved=metrics.resolved,
        median_time_to_detect_seconds=_seconds(metrics.median_time_to_detect),
        median_time_to_acknowledge_seconds=_seconds(metrics.median_time_to_acknowledge),
        median_time_to_resolve_seconds=_seconds(metrics.median_time_to_resolve),
    )


@router.get("/{incident_id}", response_model=IncidentOut)
def get_incident(incident_id: str, views: Incidents) -> IncidentOut:
    return IncidentOut.model_validate(views.get(incident_id))


@router.post("/{incident_id}/acknowledge", response_model=IncidentOut)
def acknowledge_incident(incident_id: str, monitoring: Monitoring, body: IncidentActionIn | None = None) -> IncidentOut:
    action = body or IncidentActionIn()
    return IncidentOut.model_validate(monitoring.acknowledge_incident(incident_id, action.actor, action.note))


@router.post("/{incident_id}/resolve", response_model=IncidentOut)
def resolve_incident(incident_id: str, monitoring: Monitoring, body: IncidentActionIn | None = None) -> IncidentOut:
    action = body or IncidentActionIn()
    return IncidentOut.model_validate(monitoring.resolve_incident(incident_id, action.actor, action.note))


@router.post("/{incident_id}/notes", response_model=AuditEventOut, status_code=201)
def add_note(incident_id: str, body: IncidentNoteIn, monitoring: Monitoring) -> AuditEventOut:
    return AuditEventOut.model_validate(monitoring.add_incident_note(incident_id, body.actor, body.text))


@router.get("/{incident_id}/timeline", response_model=list[AuditEventOut])
def incident_timeline(incident_id: str, views: Incidents) -> list[AuditEventOut]:
    return [AuditEventOut.model_validate(event) for event in views.timeline(incident_id)]


@router.get("/{incident_id}/evidence", response_model=IncidentEvidenceOut)
def incident_evidence(
    incident_id: str, views: Incidents, queries: Queries, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> IncidentEvidenceOut:
    return evidence_out(views.evidence(incident_id, limit), queries)


@router.get("/{incident_id}/report", response_model=IncidentReportOut)
def incident_report(
    incident_id: str,
    views: Incidents,
    queries: Queries,
    report_format: Annotated[Literal["json", "markdown"], Query(alias="format")] = "json",
) -> IncidentReportOut | Response:
    report = views.report(incident_id)
    if report_format == "markdown":
        return Response(
            content=render_incident_markdown(report),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="olay-raporu-{incident_id}.md"'},
        )
    return report_out(report, queries)


def evidence_out(evidence: IncidentEvidence, queries: Queries) -> IncidentEvidenceOut:
    return IncidentEvidenceOut(
        period_start=evidence.period_start,
        period_end=evidence.period_end,
        total_posts=evidence.total_posts,
        unique_users=evidence.unique_users,
        posts=[
            EvidencePostOut(post=post_out(item.post, queries), analysis=AnalysisOut.model_validate(item.analysis))
            for item in evidence.posts
        ],
    )


def report_out(report: IncidentReport, queries: Queries) -> IncidentReportOut:
    return IncidentReportOut(
        generated_at=report.generated_at,
        incident=IncidentOut.model_validate(report.incident),
        timings=_timings(report.timings),
        timeline=[AuditEventOut.model_validate(event) for event in report.timeline],
        evidence=evidence_out(report.evidence, queries),
        audit_trail_verified=report.audit_trail_verified,
        disclaimer=report.disclaimer,
    )


def _timings(timings: IncidentTimings) -> IncidentTimingsOut:
    return IncidentTimingsOut(
        time_to_detect_seconds=_seconds(timings.time_to_detect),
        time_to_acknowledge_seconds=_seconds(timings.time_to_acknowledge),
        time_to_resolve_seconds=_seconds(timings.time_to_resolve),
    )


def _seconds(value: timedelta | None) -> float | None:
    return value.total_seconds() if value is not None else None

