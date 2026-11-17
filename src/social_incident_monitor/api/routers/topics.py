"""Emerging topics and location hints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ..dependencies import Queries, Topics
from ..schemas import LocationCountOut, TopicOut
from .posts import post_out

router = APIRouter(prefix="/api/v1", tags=["topics"])


@router.get("/topics/emerging", response_model=list[TopicOut])
def emerging_topics(topics: Topics, queries: Queries) -> list[TopicOut]:
    return [
        TopicOut(
            label=topic.label,
            terms=list(topic.terms),
            unique_users=topic.unique_users,
            posts=topic.posts,
            expected_users=topic.expected_users,
            p_value=topic.p_value,
            first_seen=topic.first_seen,
            categories=topic.categories,
            unrecognised_share=topic.unrecognised_share,
            examples=[post_out(post, queries) for post in topic.examples],
        )
        for topic in topics.emerging()
    ]


@router.get("/locations", response_model=list[LocationCountOut])
def locations(
    queries: Queries,
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
    complaints_only: bool = True,
) -> list[LocationCountOut]:
    return [LocationCountOut.model_validate(item) for item in queries.locations(hours=hours, complaints_only=complaints_only)]
