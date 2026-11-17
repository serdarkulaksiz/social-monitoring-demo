"""FastAPI dependencies: container access and API key authentication."""

from __future__ import annotations

import secrets
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Header, HTTPException, Request

from ..application.incident_views import IncidentViews
from ..application.monitoring_service import MonitoringService
from ..application.quality_service import QualityService
from ..application.query_service import QueryService
from ..application.topics_service import TopicsService
from ..container import Container

API_KEY_HEADER = "X-API-Key"


def get_container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


def get_monitoring(container: Annotated[Container, Depends(get_container)]) -> MonitoringService:
    return container.monitoring


def get_queries(container: Annotated[Container, Depends(get_container)]) -> QueryService:
    return container.queries


def get_incident_views(container: Annotated[Container, Depends(get_container)]) -> IncidentViews:
    return container.incident_views


def get_quality(container: Annotated[Container, Depends(get_container)]) -> QualityService:
    return container.quality


def get_topics(container: Annotated[Container, Depends(get_container)]) -> TopicsService:
    return container.topics


def require_api_key(
    container: Annotated[Container, Depends(get_container)],
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> None:
    expected = container.settings.api_key.get_secret_value()
    if x_api_key is None or not secrets.compare_digest(x_api_key.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail=f"missing or invalid {API_KEY_HEADER} header")


LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "localhost"}
DASHBOARD_ACTION_HEADER = "X-Dashboard-Action"


def require_dashboard_action(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    x_dashboard_action: Annotated[str | None, Header(alias=DASHBOARD_ACTION_HEADER)] = None,
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> None:
    """Guards dashboard write actions while there is no login (MONITOR_DASHBOARD_ACTIONS).

    The custom header and the Origin / Sec-Fetch-Site checks stop other web sites from
    triggering actions through the analyst's browser (CSRF); the trusted-host check of the
    application stops DNS rebinding.
    """
    mode = container.settings.dashboard_actions
    if mode == "off":
        raise HTTPException(status_code=403, detail="dashboard actions are disabled (MONITOR_DASHBOARD_ACTIONS=off)")
    if x_dashboard_action != "1":
        raise HTTPException(status_code=403, detail=f"missing {DASHBOARD_ACTION_HEADER} header")
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="cross-origin request refused")
    if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
        raise HTTPException(status_code=403, detail="cross-site request refused")
    if mode == "local":
        client = request.client.host if request.client else ""
        if client not in LOOPBACK_CLIENTS:
            raise HTTPException(
                status_code=403, detail="dashboard actions are only accepted from this computer (MONITOR_DASHBOARD_ACTIONS=local)"
            )
    else:
        require_api_key(container, x_api_key)


Monitoring = Annotated[MonitoringService, Depends(get_monitoring)]
Queries = Annotated[QueryService, Depends(get_queries)]
Incidents = Annotated[IncidentViews, Depends(get_incident_views)]
Quality = Annotated[QualityService, Depends(get_quality)]
Topics = Annotated[TopicsService, Depends(get_topics)]
