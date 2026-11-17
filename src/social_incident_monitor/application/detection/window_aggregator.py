"""Sliding-window complaint counting.

Windows are anchored at the evaluation time and use the post's publication time
(``created_at``), not the ingestion time, so late-arriving posts still count toward the
window they belong to. Only posts classified as complaints are counted. Duplicates are
already excluded at ingestion (one record per source + source_post_id). Each window
reports both the complaint count and the number of distinct authors; repeated
complaints by the same author are reported separately and do not inflate the
unique-user count that thresholds use.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from ...domain.enums import IssueCategory, ServiceCategory
from ..ports import AnalysisRepository, PostRepository


@dataclass(frozen=True, order=True)
class GroupKey:
    service: ServiceCategory
    issue: IssueCategory


@dataclass(frozen=True)
class WindowStats:
    window_minutes: int
    window_start: datetime
    window_end: datetime
    complaints_count: int
    unique_users_count: int
    max_complaints_per_user: int
    first_post_at: datetime | None = None  # earliest counted post in the window
    amplification: int = 0  # reputation windows: sum of each user's highest reposts + quotes

    @property
    def repeat_complaints_count(self) -> int:
        return self.complaints_count - self.unique_users_count


GroupedWindowStats = dict[GroupKey, dict[int, WindowStats]]


@dataclass(frozen=True)
class CountedPost:
    author_id: str
    created_at: datetime


class ComplaintWindowAggregator:
    def __init__(self, posts: PostRepository, analyses: AnalysisRepository) -> None:
        self._posts = posts
        self._analyses = analyses

    def aggregate(self, now: datetime, windows: Sequence[int]) -> GroupedWindowStats:
        """Per (service, issue) group, stats for every requested window size in minutes."""
        counted = self._complaints(now, windows)
        result: GroupedWindowStats = {}
        for key, per_window in counted.items():
            result[key] = {minutes: window_stats(now, minutes, items) for minutes, items in per_window.items()}
        return result

    def totals(self, now: datetime, windows: Sequence[int]) -> dict[int, WindowStats]:
        """Stats over all complaint groups combined, per window size."""
        combined: dict[int, list[CountedPost]] = {minutes: [] for minutes in windows}
        for per_window in self._complaints(now, windows).values():
            for minutes, items in per_window.items():
                combined[minutes].extend(items)
        return {minutes: window_stats(now, minutes, items) for minutes, items in combined.items()}

    def _complaints(self, now: datetime, windows: Sequence[int]) -> dict[GroupKey, dict[int, list[CountedPost]]]:
        if not windows:
            return {}
        longest = max(windows)
        grouped: dict[GroupKey, dict[int, list[CountedPost]]] = defaultdict(lambda: {minutes: [] for minutes in windows})
        for post in self._posts.find_created_between(now - timedelta(minutes=longest), now):
            analysis = self._analyses.get(post.post_id)
            if analysis is None or not analysis.is_complaint:
                continue
            key = GroupKey(analysis.service_category, analysis.issue_category)
            for minutes in windows:
                if post.created_at > now - timedelta(minutes=minutes):
                    grouped[key][minutes].append(CountedPost(post.author_id, post.created_at))
        return dict(grouped)


def window_stats(now: datetime, minutes: int, items: Sequence[CountedPost], amplification: int = 0) -> WindowStats:
    per_user: dict[str, int] = defaultdict(int)
    for item in items:
        per_user[item.author_id] += 1
    return WindowStats(
        window_minutes=minutes,
        window_start=now - timedelta(minutes=minutes),
        window_end=now,
        complaints_count=len(items),
        unique_users_count=len(per_user),
        max_complaints_per_user=max(per_user.values(), default=0),
        first_post_at=min((item.created_at for item in items), default=None),
        amplification=amplification,
    )
