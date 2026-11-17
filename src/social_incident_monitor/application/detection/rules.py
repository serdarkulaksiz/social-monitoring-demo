"""Detection rules.

A rule turns the window statistics of one (service, issue) group into an alarm level.
Strategies are separate ``DetectionRule`` implementations (fixed thresholds here,
the baseline rule in ``baseline.py``); the engine does not change when one is added.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ...domain.enums import Severity
from .window_aggregator import GroupKey, WindowStats


@dataclass(frozen=True)
class RuleContext:
    """Everything a rule may look at for one group: which group, when, and its window statistics."""

    key: GroupKey
    now: datetime
    stats_by_window: Mapping[int, WindowStats]


@dataclass(frozen=True)
class RuleOutcome:
    level: Severity
    window_minutes: int
    complaints_count: int
    unique_users_count: int
    reason: str  # English one-line explanation (API, logs)
    details: Mapping[str, Any] = field(default_factory=dict)  # structured explanation, rendered by the UI


class DetectionRule(Protocol):
    name: str

    def evaluate(self, context: RuleContext) -> RuleOutcome | None:
        """Return an outcome, or None when the rule has nothing to say about the group."""
        ...


@dataclass(frozen=True)
class WindowThreshold:
    window_minutes: int
    warning_unique_users: int
    critical_unique_users: int

    def __post_init__(self) -> None:
        if self.window_minutes <= 0:
            raise ValueError("window_minutes must be positive")
        if not 0 < self.warning_unique_users <= self.critical_unique_users:
            raise ValueError("thresholds must satisfy 0 < warning <= critical")

    def level_for(self, unique_users: int) -> Severity:
        if unique_users >= self.critical_unique_users:
            return Severity.CRITICAL
        if unique_users >= self.warning_unique_users:
            return Severity.WARNING
        return Severity.NORMAL


class AbsoluteThresholdRule:
    """Compares distinct complaining users in a window against fixed thresholds.

    The configured numbers are starting values, not statistically derived limits.
    """

    name = "absolute_threshold"

    def __init__(self, thresholds: list[WindowThreshold]) -> None:
        self._thresholds = sorted(thresholds, key=lambda item: item.window_minutes)

    @property
    def windows(self) -> list[int]:
        return [threshold.window_minutes for threshold in self._thresholds]

    def evaluate(self, context: RuleContext) -> RuleOutcome | None:
        best: RuleOutcome | None = None
        for threshold in self._thresholds:
            stats = context.stats_by_window.get(threshold.window_minutes)
            if stats is None:
                continue
            level = threshold.level_for(stats.unique_users_count)
            if best is None or level.is_higher_than(best.level):
                best = RuleOutcome(
                    level=level,
                    window_minutes=threshold.window_minutes,
                    complaints_count=stats.complaints_count,
                    unique_users_count=stats.unique_users_count,
                    reason=self._reason(threshold, stats, level),
                    details={
                        "rule": self.name,
                        "window_minutes": threshold.window_minutes,
                        "unique_users": stats.unique_users_count,
                        "complaints": stats.complaints_count,
                        "repeat_complaints": stats.repeat_complaints_count,
                        "warning_threshold": threshold.warning_unique_users,
                        "critical_threshold": threshold.critical_unique_users,
                    },
                )
        return best

    @staticmethod
    def _reason(threshold: WindowThreshold, stats: WindowStats, level: Severity) -> str:
        if level is Severity.CRITICAL:
            limit = f">= {threshold.critical_unique_users} (CRITICAL threshold)"
        elif level is Severity.WARNING:
            limit = f">= {threshold.warning_unique_users} (WARNING threshold)"
        else:
            limit = f"< {threshold.warning_unique_users} (below WARNING threshold)"
        return (
            f"{stats.unique_users_count} unique users {limit} in the last {threshold.window_minutes} minutes; "
            f"{stats.complaints_count} complaints, {stats.repeat_complaints_count} repeated by the same users"
        )
