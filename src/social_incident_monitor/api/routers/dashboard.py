"""Turkish, server-rendered dashboard.

Pages only read (no login during development; the server listens on this computer by
default). Write actions (acknowledge, resolve, notes, labels) go through
``/dashboard/actions/...`` and are guarded by ``require_dashboard_action``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, Response
from jinja2 import Environment

from ...application.feedback_service import Label, Sample, Verdict
from ...application.quality_service import ReviewMode
from ...container import Container
from ...dashboard.pages import DashboardPages
from ...dashboard.report_markdown import render_incident_markdown
from ...dashboard.templating import build_environment
from ..dependencies import Incidents, Monitoring, Quality, Queries, Topics, get_container, require_dashboard_action
from ..schemas import FeedbackIn, IncidentActionIn, IncidentNoteIn

router = APIRouter(prefix="/dashboard", tags=["dashboard"], include_in_schema=False)
actions = APIRouter(prefix="/dashboard/actions", tags=["dashboard"], include_in_schema=False, dependencies=[Depends(require_dashboard_action)])


@lru_cache(maxsize=1)
def _environment() -> Environment:
    return build_environment()


def _pages(
    queries: Queries,
    incidents: Incidents,
    quality: Quality,
    topics: Topics,
    container: Annotated[Container, Depends(get_container)],
) -> DashboardPages:
    return DashboardPages(
        queries=queries,
        incidents=incidents,
        quality=quality,
        topics=topics,
        actions_mode=container.settings.dashboard_actions,
    )


Pages = Annotated[DashboardPages, Depends(_pages)]


def _render(template: str, context: dict[str, Any]) -> HTMLResponse:
    return HTMLResponse(_environment().get_template(template).render(**context))


@router.get("", response_class=HTMLResponse)
def overview(pages: Pages) -> HTMLResponse:
    return _render("overview.html", pages.overview())


@router.get("/incidents/{incident_id}", response_class=HTMLResponse)
def incident(incident_id: str, pages: Pages) -> HTMLResponse:
    return _render("incident.html", pages.incident(incident_id))


@router.get("/incidents/{incident_id}/report", response_class=HTMLResponse)
def printable_report(incident_id: str, pages: Pages) -> HTMLResponse:
    return _render("report.html", pages.report(incident_id))


@router.get("/incidents/{incident_id}/report.md")
def markdown_report(incident_id: str, incidents: Incidents) -> Response:
    return Response(
        content=render_incident_markdown(incidents.report(incident_id)),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="olay-raporu-{incident_id}.md"'},
    )


@router.get("/review", response_class=HTMLResponse)
def review(pages: Pages, mode: Annotated[ReviewMode, Query()] = ReviewMode.INCIDENTS) -> HTMLResponse:
    return _render("review.html", pages.review(mode))


@router.get("/quality", response_class=HTMLResponse)
def quality_page(pages: Pages) -> HTMLResponse:
    return _render("quality.html", pages.quality())


@router.get("/topics", response_class=HTMLResponse)
def topics_page(pages: Pages) -> HTMLResponse:
    return _render("topics.html", pages.topics())


@router.get("/reputation", response_class=HTMLResponse)
def reputation_page(pages: Pages) -> HTMLResponse:
    return _render("reputation.html", pages.reputation())


@actions.post("/incidents/{incident_id}/acknowledge")
def acknowledge(incident_id: str, body: IncidentActionIn, monitoring: Monitoring) -> dict[str, Any]:
    incident = monitoring.acknowledge_incident(incident_id, body.actor, body.note)
    return {"ok": True, "status": incident.status.value}


@actions.post("/incidents/{incident_id}/resolve")
def resolve(incident_id: str, body: IncidentActionIn, monitoring: Monitoring) -> dict[str, Any]:
    incident = monitoring.resolve_incident(incident_id, body.actor, body.note)
    return {"ok": True, "status": incident.status.value}


@actions.post("/incidents/{incident_id}/notes")
def add_note(incident_id: str, body: IncidentNoteIn, monitoring: Monitoring) -> dict[str, Any]:
    event = monitoring.add_incident_note(incident_id, body.actor, body.text)
    return {"ok": True, "event_id": event.event_id}


@actions.post("/posts/{post_id}/feedback")
def label_post(post_id: str, body: FeedbackIn, monitoring: Monitoring) -> dict[str, Any]:
    label = Label(
        verdict=Verdict(body.verdict),
        service=body.service_category,
        issue=body.issue_category,
        risk_signals=tuple(body.risk_signals) if body.risk_signals is not None else None,
        sample=Sample(body.sample),
    )
    feedback = monitoring.submit_feedback(post_id, label, body.actor, body.note)
    return {"ok": True, "feedback_id": feedback.feedback_id}
