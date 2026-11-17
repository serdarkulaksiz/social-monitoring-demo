"""Classification results and analyst feedback on them."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ...application.feedback_service import Label, Sample, Verdict
from ...domain.enums import ServiceCategory
from ..dependencies import Monitoring, Queries
from ..schemas import AnalysisOut, FeedbackIn, FeedbackOut, Page

router = APIRouter(prefix="/api/v1/analysis", tags=["analysis"])


@router.get("", response_model=Page[AnalysisOut])
def list_analysis(
    queries: Queries,
    is_complaint: bool | None = None,
    service: ServiceCategory | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AnalysisOut]:
    items = queries.analyses(is_complaint=is_complaint, service=service, limit=limit, offset=offset)
    return Page[AnalysisOut](
        items=[AnalysisOut.model_validate(item) for item in items], limit=limit, offset=offset, count=len(items)
    )


@router.post("/{post_id}/feedback", response_model=FeedbackOut, status_code=201)
def submit_feedback(post_id: str, body: FeedbackIn, monitoring: Monitoring) -> FeedbackOut:
    label = Label(
        verdict=Verdict(body.verdict),
        service=body.service_category,
        issue=body.issue_category,
        risk_signals=tuple(body.risk_signals) if body.risk_signals is not None else None,
        sample=Sample(body.sample),
    )
    return FeedbackOut.model_validate(monitoring.submit_feedback(post_id, label, body.actor, body.note))
