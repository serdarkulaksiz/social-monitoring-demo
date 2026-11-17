"""Classifies posts and stores the results."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from ..domain.models import AnalysisResult, Post
from .ports import AnalysisRepository, Classifier, PostRepository


class AnalysisService:
    def __init__(self, classifier: Classifier, analyses: AnalysisRepository) -> None:
        self._classifier = classifier
        self._analyses = analyses

    def analyze(self, posts: Sequence[Post], now: datetime) -> list[AnalysisResult]:
        results = []
        for post in posts:
            result = self._classifier.classify(post, now)
            self._analyses.save(result)
            results.append(result)
        return results

    def analyze_missing(self, posts: PostRepository, now: datetime, batch_size: int = 1000) -> int:
        """Classify stored posts without a result (e.g. after a crash between storing and analysing)."""
        analyzed = 0
        offset = 0
        while True:
            page = posts.find(limit=batch_size, offset=offset)
            if not page:
                return analyzed
            missing = [post for post in page if self._analyses.get(post.post_id) is None]
            analyzed += len(self.analyze(missing, now))
            offset += batch_size
