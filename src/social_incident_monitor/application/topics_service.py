"""Emerging topics: what people suddenly talk about that the rules do not know yet.

The classification rules only recognise problems they were written for. When something
new breaks ("QR ile para çekme hata veriyor"), the posts land in "other" or are not
counted as complaints at all. This service looks at posts about the bank in the recent
window and finds terms used by clearly more distinct people than usual:

* expected people per term = people who used it during the previous ``history_days``,
  scaled to the length of the recent window (at least ``min_expected_users``);
* a term qualifies with at least ``min_users`` people and a Poisson probability of at
  most ``max_p`` (the same test the baseline rule uses);
* terms whose posts overlap strongly (Jaccard ≥ ``merge_overlap``) form one topic.

Topics never raise alarms. They point analysts to new problems and to rules worth
adding. Results are cached for ``cache_seconds``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..domain.enums import IssueCategory
from ..domain.models import AnalysisResult, Post
from .ports import AnalysisRepository, Clock, PostRepository, TermSource
from .probability import poisson_sf


@dataclass(frozen=True)
class TopicSettings:
    window_minutes: int = 120
    history_days: int = 7
    min_users: int = 3
    max_p: float = 0.001
    min_expected_users: float = 0.2
    merge_overlap: float = 0.5
    max_topics: int = 10
    examples: int = 5
    cache_seconds: int = 60


@dataclass(frozen=True)
class EmergingTopic:
    label: str  # the strongest terms, joined
    terms: tuple[str, ...]
    unique_users: int
    posts: int
    expected_users: float
    p_value: float
    first_seen: datetime
    categories: dict[str, int]  # "service/issue" → posts, as currently classified
    unrecognised_share: float  # share of posts the rules could not place in a known problem
    examples: list[Post]


@dataclass
class _Term:
    key: str
    users: set[str]
    post_ids: set[str]
    expected: float
    p_value: float


class TopicsService:
    def __init__(
        self,
        *,
        clock: Clock,
        posts: PostRepository,
        analyses: AnalysisRepository,
        extractor_factory: Callable[[], TermSource],
        settings: TopicSettings,
    ) -> None:
        self._clock = clock
        self._posts = posts
        self._analyses = analyses
        self._extractor_factory = extractor_factory
        self._settings = settings
        self._cache: tuple[datetime, list[EmergingTopic]] | None = None

    def emerging(self) -> list[EmergingTopic]:
        now = self._clock.now()
        if self._cache is not None and timedelta(0) <= now - self._cache[0] < timedelta(seconds=self._settings.cache_seconds):
            return self._cache[1]
        topics = self._find(now)
        self._cache = (now, topics)
        return topics

    def _find(self, now: datetime) -> list[EmergingTopic]:
        settings = self._settings
        window = timedelta(minutes=settings.window_minutes)
        history = timedelta(days=settings.history_days)
        extractor = self._extractor_factory()

        recent_users: dict[str, set[str]] = defaultdict(set)
        recent_posts: dict[str, set[str]] = defaultdict(set)
        posts_by_id: dict[str, Post] = {}
        for post in self._bank_posts(now - window, now):
            posts_by_id[post.post_id] = post
            for term in extractor.terms(post.text):
                recent_users[term].add(post.author_id)
                recent_posts[term].add(post.post_id)
        background_users: dict[str, set[str]] = defaultdict(set)
        for post in self._bank_posts(now - window - history, now - window):
            for term in extractor.terms(post.text):
                if term in recent_users:
                    background_users[term].add(post.author_id)

        scale = window / history
        candidates: list[_Term] = []
        for key, users in recent_users.items():
            if len(users) < settings.min_users:
                continue
            expected = max(settings.min_expected_users, len(background_users.get(key, ())) * scale)
            probability = poisson_sf(len(users), expected)
            if probability <= settings.max_p:
                candidates.append(_Term(key, users, recent_posts[key], expected, probability))
        candidates.sort(key=lambda term: (term.p_value, -len(term.users), term.key))

        groups: list[list[_Term]] = []
        for term in candidates:
            for group in groups:
                members = set().union(*(member.post_ids for member in group))
                if len(members & term.post_ids) / len(members | term.post_ids) >= settings.merge_overlap:
                    group.append(term)
                    break
            else:
                if len(groups) < settings.max_topics:
                    groups.append([term])
        topics = [self._topic(group, posts_by_id, extractor) for group in groups]
        # Problems the rules cannot place come first: known ones already have incidents.
        return sorted(topics, key=lambda topic: (-topic.unrecognised_share, topic.p_value, -topic.unique_users))

    def _bank_posts(self, start: datetime, end: datetime) -> list[Post]:
        selected = []
        for post in self._posts.find_created_between(start, end):
            analysis = self._analyses.get(post.post_id)
            if analysis is not None and _mentions_bank(analysis):
                selected.append(post)
        return selected

    def _topic(self, group: list[_Term], posts_by_id: dict[str, Post], extractor: TermSource) -> EmergingTopic:
        lead = group[0]
        post_ids = set().union(*(term.post_ids for term in group))
        users = set().union(*(term.users for term in group))
        posts = sorted((posts_by_id[post_id] for post_id in post_ids), key=lambda post: post.created_at)
        categories: Counter[str] = Counter()
        unrecognised = 0
        for post in posts:
            analysis = self._analyses.get(post.post_id)
            if analysis is None:
                continue
            categories[f"{analysis.service_category.value}/{analysis.issue_category.value}"] += 1
            if analysis.issue_category in (IssueCategory.NONE, IssueCategory.OTHER):
                unrecognised += 1
        # Word pairs describe a topic better than single words; equally strong terms list pairs first.
        ordered = sorted(group, key=lambda term: (term.p_value, " " not in term.key, -len(term.users), term.key))
        return EmergingTopic(
            label=", ".join(extractor.display(term.key) for term in ordered[:3]),
            terms=tuple(extractor.display(term.key) for term in ordered),
            unique_users=len(users),
            posts=len(posts),
            expected_users=round(lead.expected, 2),
            p_value=lead.p_value,
            first_seen=posts[0].created_at,
            categories=dict(categories.most_common()),
            unrecognised_share=round(unrecognised / len(posts), 2) if posts else 0.0,
            examples=posts[: self._settings.examples],
        )


def _mentions_bank(analysis: AnalysisResult) -> bool:
    """Posts naming the bank, including ones the rules could not place (exactly what topics are for).

    Sports and agriculture contexts stay out; an analyst's label always decides.
    """
    if analysis.label_source == "feedback":
        return analysis.is_bank_related
    if analysis.is_bank_related:
        return True
    return "brand" in analysis.matched_rules and not any(rule.startswith("exclusion:") for rule in analysis.matched_rules)
