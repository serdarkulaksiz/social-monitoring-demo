"""Runs every detection pipeline over the current windows.

A pipeline pairs one way of grouping posts (complaints per service/issue, reputation
signals per signal type) with the rules that judge those groups. For each group the
highest level any rule reports wins.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ...domain.enums import IssueCategory, ServiceCategory, Severity
from .rules import DetectionRule, RuleContext, RuleOutcome
from .window_aggregator import GroupedWindowStats


@dataclass(frozen=True)
class DetectionSignal:
    service: ServiceCategory
    issue: IssueCategory
    level: Severity
    window_minutes: int
    complaints_count: int
    unique_users_count: int
    reason: str
    rule: str
    details: Mapping[str, Any] = field(default_factory=dict)
    first_post_at: datetime | None = None  # earliest post of the window that decided the level


class GroupAggregator(Protocol):
    def aggregate(self, now: datetime, windows: Sequence[int]) -> GroupedWindowStats: ...


@dataclass(frozen=True)
class DetectionPipeline:
    aggregator: GroupAggregator
    rules: Sequence[DetectionRule]
    windows: Sequence[int]

    def __post_init__(self) -> None:
        if not self.rules:
            raise ValueError("a detection pipeline needs at least one rule")
        if not self.windows:
            raise ValueError("a detection pipeline needs at least one window")


class DetectionEngine:
    def __init__(self, pipelines: Sequence[DetectionPipeline]) -> None:
        if not pipelines:
            raise ValueError("at least one detection pipeline is required")
        self._pipelines = list(pipelines)

    def detect(self, now: datetime) -> list[DetectionSignal]:
        """One signal per group that currently has posts; the highest level wins."""
        signals = []
        for pipeline in self._pipelines:
            windows = sorted(set(pipeline.windows))
            for key, stats_by_window in sorted(pipeline.aggregator.aggregate(now, windows).items()):
                context = RuleContext(key, now, stats_by_window)
                outcome: tuple[str, RuleOutcome] | None = None
                for rule in pipeline.rules:
                    candidate = rule.evaluate(context)
                    if candidate is not None and (outcome is None or candidate.level.is_higher_than(outcome[1].level)):
                        outcome = (rule.name, candidate)
                if outcome is None:
                    continue
                rule_name, result = outcome
                deciding_window = stats_by_window.get(result.window_minutes)
                signals.append(
                    DetectionSignal(
                        service=key.service,
                        issue=key.issue,
                        level=result.level,
                        window_minutes=result.window_minutes,
                        complaints_count=result.complaints_count,
                        unique_users_count=result.unique_users_count,
                        reason=result.reason,
                        rule=rule_name,
                        details=result.details,
                        first_post_at=deciding_window.first_post_at if deciding_window else None,
                    )
                )
        return signals
