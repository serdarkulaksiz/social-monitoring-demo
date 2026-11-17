"""Post ingestion, engagement updates and listing."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel, ValidationError

from ...application.engagement_service import IncomingEngagement
from ...application.ingestion_service import EngagementCounts, IncomingPost, Rejection
from ...application.query_service import QueryService
from ...domain.models import Post
from ..dependencies import Monitoring, Queries
from ..schemas import (
    EngagementBatchRequest,
    EngagementBatchResponse,
    EngagementOut,
    EngagementUpdateV1,
    EngagementV1,
    Page,
    PostOut,
    PostsBatchRequest,
    PostsBatchResponse,
    RejectedItem,
    SocialPostV1,
)

router = APIRouter(prefix="/api/v1/posts", tags=["posts"])


def _validate_each[M: BaseModel](items: list[dict[str, Any]], model: type[M]) -> tuple[list[tuple[int, M]], list[Rejection]]:
    """Validate items one by one so one bad item does not reject the whole batch."""
    valid: list[tuple[int, M]] = []
    rejected: list[Rejection] = []
    for index, raw in enumerate(items):
        try:
            valid.append((index, model.model_validate(raw)))
        except ValidationError as exc:
            first = exc.errors()[0]
            field = ".".join(str(part) for part in first.get("loc", ())) or "item"
            source_post_id = raw.get("source_post_id") if isinstance(raw, dict) and isinstance(raw.get("source_post_id"), str) else None
            rejected.append(Rejection(index, source_post_id, f"{field}: {first.get('msg', 'invalid')}"))
    return valid, rejected


def _counts(engagement: EngagementV1) -> EngagementCounts:
    return EngagementCounts(engagement.like_count, engagement.repost_count, engagement.reply_count, engagement.quote_count)


@router.post("/batch", response_model=PostsBatchResponse)
def ingest_batch(request: PostsBatchRequest, monitoring: Monitoring) -> PostsBatchResponse:
    valid, rejected = _validate_each(request.posts, SocialPostV1)
    items = [
        IncomingPost(
            index=index,
            source=item.source,
            source_post_id=item.source_post_id,
            author_id=item.author_id,
            text=item.text,
            created_at=item.created_at,
            collected_at=item.collected_at,
            url=item.url,
            schema_version=item.schema_version or request.schema_version,
            author_handle=item.author_handle,
            author_display_name=item.author_display_name,
            query_id=item.query_id,
            engagement=_counts(item.engagement) if item.engagement is not None else None,
        )
        for index, item in valid
    ]
    result = monitoring.process_batch(request.batch_id, items, rejected)
    outcome = result.ingestion
    return PostsBatchResponse(
        batch_id=request.batch_id,
        received=outcome.received,
        accepted=len(outcome.accepted),
        duplicates=outcome.duplicates,
        rejected=[RejectedItem.model_validate(item) for item in outcome.rejected],
        incidents_opened=len(result.evaluation.opened),
        alerts_created=len(result.evaluation.alerts),
    )


@router.post("/engagement", response_model=EngagementBatchResponse)
def update_engagement(request: EngagementBatchRequest, monitoring: Monitoring) -> EngagementBatchResponse:
    valid, rejected = _validate_each(request.updates, EngagementUpdateV1)
    items = [
        IncomingEngagement(index, item.source, item.source_post_id, item.observed_at, _counts(item.engagement))
        for index, item in valid
    ]
    outcome = monitoring.process_engagement(request.batch_id, items, rejected).engagement
    return EngagementBatchResponse(
        batch_id=request.batch_id,
        received=outcome.received,
        applied=outcome.applied,
        stale=outcome.stale,
        unknown=outcome.unknown,
        rejected=[RejectedItem.model_validate(item) for item in outcome.rejected],
    )


@router.get("", response_model=Page[PostOut])
def list_posts(
    queries: Queries,
    source: str | None = None,
    since: Annotated[AwareDatetime | None, Query(description="Only posts created at or after this time")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[PostOut]:
    items = queries.posts(source=source, created_since=since, limit=limit, offset=offset)
    return Page[PostOut](items=[post_out(post, queries) for post in items], limit=limit, offset=offset, count=len(items))


def post_out(post: Post, queries: QueryService) -> PostOut:
    snapshot = queries.engagement(post.post_id)
    return PostOut.model_validate(post).model_copy(
        update={"engagement": EngagementOut.model_validate(snapshot) if snapshot is not None else None}
    )
