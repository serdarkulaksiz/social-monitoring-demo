"""Collector run reports (data-collection health input)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response

from ...domain.models import CollectorRun, QueryRun
from ..dependencies import Monitoring, Queries
from ..schemas import CollectorRunAccepted, CollectorRunOut, CollectorRunReportV1, Page

router = APIRouter(prefix="/api/v1/collector-runs", tags=["collector-runs"])


@router.post("", response_model=CollectorRunAccepted, status_code=201)
def report_run(report: CollectorRunReportV1, monitoring: Monitoring, response: Response) -> CollectorRunAccepted:
    run = CollectorRun(
        run_id=report.run_id,
        source=report.source,
        collector_version=report.collector_version,
        started_at=report.started_at,
        finished_at=report.finished_at,
        received_at=report.finished_at,  # replaced with the monitor's clock on storage
        status=report.status,
        queries=tuple(QueryRun.model_validate(query.model_dump()) for query in report.queries),
        fetched_count=report.fetched_count,
        new_count=report.new_count,
        sent_count=report.sent_count,
        pending_count=report.pending_count,
        error_message=report.error_message,
    )
    stored = monitoring.record_collector_run(run)
    if not stored:
        response.status_code = 200  # idempotent re-delivery of an already stored run
    return CollectorRunAccepted(run_id=run.run_id, stored=stored)


@router.get("", response_model=Page[CollectorRunOut])
def list_runs(
    queries: Queries,
    source: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[CollectorRunOut]:
    items = queries.collector_runs(source=source, limit=limit, offset=offset)
    return Page[CollectorRunOut](
        items=[CollectorRunOut.model_validate(item) for item in items], limit=limit, offset=offset, count=len(items)
    )
