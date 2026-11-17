"""Alerts (service incidents and collector health)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ...domain.enums import AlertStatus, AlertType
from ..dependencies import Queries
from ..schemas import AlertOut, Page

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


@router.get("", response_model=Page[AlertOut])
def list_alerts(
    queries: Queries,
    alert_type: AlertType | None = None,
    status: AlertStatus | None = None,
    incident_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AlertOut]:
    items = queries.alerts(alert_type=alert_type, status=status, incident_id=incident_id, limit=limit, offset=offset)
    return Page[AlertOut](items=[AlertOut.model_validate(item) for item in items], limit=limit, offset=offset, count=len(items))
