"""Accepts validated posts, applies domain admission rules and de-duplicates them.

Counters that arrive with a new post are stored as its first engagement snapshot.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..domain.models import EngagementSnapshot, Post, make_post_id
from .ports import EngagementRepository, PostRepository

SYNTHETIC_SOURCE = "synthetic"


@dataclass(frozen=True)
class EngagementCounts:
    like_count: int
    repost_count: int
    reply_count: int
    quote_count: int

    def snapshot(self, post_id: str, observed_at: datetime) -> EngagementSnapshot:
        return EngagementSnapshot(
            post_id=post_id,
            observed_at=observed_at,
            like_count=self.like_count,
            repost_count=self.repost_count,
            reply_count=self.reply_count,
            quote_count=self.quote_count,
        )


@dataclass(frozen=True)
class IncomingPost:
    """A post that already passed contract (schema) validation."""

    index: int
    source: str
    source_post_id: str
    author_id: str
    text: str
    created_at: datetime
    collected_at: datetime
    url: str | None
    schema_version: str
    author_handle: str | None = None
    author_display_name: str | None = None
    engagement: EngagementCounts | None = None
    query_id: str | None = None


@dataclass(frozen=True)
class Rejection:
    index: int
    source_post_id: str | None
    reason: str


@dataclass
class IngestionOutcome:
    received: int
    accepted: list[Post] = field(default_factory=list)
    duplicates: int = 0
    rejected: list[Rejection] = field(default_factory=list)


class IngestionService:
    def __init__(
        self,
        posts: PostRepository,
        *,
        accept_synthetic: bool,
        max_clock_skew: timedelta,
        engagement: EngagementRepository | None = None,
    ) -> None:
        self._posts = posts
        self._engagement = engagement
        self._accept_synthetic = accept_synthetic
        self._max_clock_skew = max_clock_skew

    def ingest(
        self,
        batch_id: str,
        items: Sequence[IncomingPost],
        now: datetime,
        prior_rejections: Sequence[Rejection] = (),
    ) -> IngestionOutcome:
        outcome = IngestionOutcome(received=len(items) + len(prior_rejections), rejected=list(prior_rejections))
        for item in items:
            reason = self._admission_error(item, now)
            if reason is not None:
                outcome.rejected.append(Rejection(item.index, item.source_post_id, reason))
                continue
            post = Post(
                post_id=make_post_id(item.source, item.source_post_id),
                source=item.source,
                source_post_id=item.source_post_id,
                author_id=item.author_id,
                author_handle=item.author_handle,
                author_display_name=item.author_display_name,
                text=item.text,
                created_at=item.created_at,
                collected_at=item.collected_at,
                ingested_at=now,
                url=item.url,
                schema_version=item.schema_version,
                batch_id=batch_id,
                query_id=item.query_id,
            )
            if self._posts.add_if_absent(post):
                outcome.accepted.append(post)
                if item.engagement is not None and self._engagement is not None:
                    self._engagement.save(item.engagement.snapshot(post.post_id, item.collected_at))
            else:
                outcome.duplicates += 1
        outcome.rejected.sort(key=lambda rejection: rejection.index)
        return outcome

    def _admission_error(self, item: IncomingPost, now: datetime) -> str | None:
        if item.source == SYNTHETIC_SOURCE and not self._accept_synthetic:
            return "synthetic posts are not accepted by this instance (MONITOR_ACCEPT_SYNTHETIC=false)"
        if item.source != SYNTHETIC_SOURCE and self._accept_synthetic:
            return "this instance runs in synthetic mode and accepts only source 'synthetic'"
        if item.created_at > now + self._max_clock_skew:
            return "created_at is in the future beyond the allowed clock skew"
        if item.collected_at > now + self._max_clock_skew:
            return "collected_at is in the future beyond the allowed clock skew"
        return None
