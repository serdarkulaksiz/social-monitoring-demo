"""Applies newer interaction counters (likes, reposts, replies, quotes) to stored posts.

* Only posts the monitor already stored are updated; updates for other posts are
  counted as ``unknown`` and ignored (for example a synthetic post sent to a live instance).
* A snapshot that is not newer than the stored one is ``stale`` and ignored, so
  re-sending a batch is idempotent.
* ``observed_at`` in the future beyond the allowed clock skew is rejected.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..domain.models import make_post_id
from .ingestion_service import EngagementCounts, Rejection
from .ports import EngagementRepository, PostRepository


@dataclass(frozen=True)
class IncomingEngagement:
    """An engagement update that already passed contract (schema) validation."""

    index: int
    source: str
    source_post_id: str
    observed_at: datetime
    counts: EngagementCounts


@dataclass
class EngagementOutcome:
    received: int
    applied: int = 0
    stale: int = 0
    unknown: int = 0
    rejected: list[Rejection] = field(default_factory=list)


class EngagementService:
    def __init__(self, posts: PostRepository, engagement: EngagementRepository, *, max_clock_skew: timedelta) -> None:
        self._posts = posts
        self._engagement = engagement
        self._max_clock_skew = max_clock_skew

    def apply(
        self, items: Sequence[IncomingEngagement], now: datetime, prior_rejections: Sequence[Rejection] = ()
    ) -> EngagementOutcome:
        outcome = EngagementOutcome(received=len(items) + len(prior_rejections), rejected=list(prior_rejections))
        for item in items:
            if item.observed_at > now + self._max_clock_skew:
                outcome.rejected.append(
                    Rejection(item.index, item.source_post_id, "observed_at is in the future beyond the allowed clock skew")
                )
                continue
            post_id = make_post_id(item.source, item.source_post_id)
            if self._posts.get(post_id) is None:
                outcome.unknown += 1
            elif self._engagement.save(item.counts.snapshot(post_id, item.observed_at)):
                outcome.applied += 1
            else:
                outcome.stale += 1
        outcome.rejected.sort(key=lambda rejection: rejection.index)
        return outcome
