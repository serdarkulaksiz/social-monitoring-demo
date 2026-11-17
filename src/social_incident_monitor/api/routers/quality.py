"""Analyst labels, classifier quality and the review queue."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ...application.quality_service import ReviewMode
from ..dependencies import Quality, Queries
from ..schemas import AnalysisOut, ConfusionMatrixOut, FeedbackOut, Page, QualityOut, ReviewItemOut, ServiceAccuracyOut
from .posts import post_out

router = APIRouter(prefix="/api/v1", tags=["quality"])


@router.get("/feedback", response_model=Page[FeedbackOut])
def list_feedback(
    quality: Quality,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[FeedbackOut]:
    items = quality.feedback(limit=limit, offset=offset)
    return Page[FeedbackOut](items=[FeedbackOut.model_validate(item) for item in items], limit=limit, offset=offset, count=len(items))


@router.get("/quality", response_model=QualityOut)
def classifier_quality(quality: Quality, classifier_version: str | None = None, sample: str | None = None) -> QualityOut:
    report = quality.report(classifier_version, sample)
    matrix = report.complaint
    return QualityOut(
        classifier_version=report.classifier_version,
        sample=report.sample,
        labelled=report.labelled,
        versions=report.versions,
        samples=report.samples,
        complaint=ConfusionMatrixOut(
            true_positive=matrix.true_positive,
            false_positive=matrix.false_positive,
            false_negative=matrix.false_negative,
            true_negative=matrix.true_negative,
            precision=matrix.precision,
            recall=matrix.recall,
            accuracy=matrix.accuracy,
        ),
        category_accuracy=report.category_accuracy,
        by_service=[ServiceAccuracyOut.model_validate(item) for item in report.by_service],
    )


@router.get("/review/queue", response_model=list[ReviewItemOut])
def review_queue(
    quality: Quality,
    queries: Queries,
    mode: ReviewMode = ReviewMode.INCIDENTS,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ReviewItemOut]:
    return [
        ReviewItemOut(post=post_out(item.post, queries), analysis=AnalysisOut.model_validate(item.analysis), reason=item.reason)
        for item in quality.queue(mode, limit)
    ]
