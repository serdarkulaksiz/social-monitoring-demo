"""Data-collection health, derived from collector run reports.

"Zero complaints" and "collection failed" are different states: a successful run that
found no posts is HEALTHY. Collection problems are reported as collector health and are
never treated as a banking service incident.

Status rules per source (first match wins):
* UNKNOWN  - no run report received yet.
* STALE    - no successful run within ``stale_after`` (collector stopped, unreachable,
             or cannot deliver reports). Raises a ``collector_stale`` health alert.
* DEGRADED - the latest run failed or was partial. When ``failure_threshold``
             consecutive runs failed, a ``collector_failing`` health alert is raised.
* HEALTHY  - otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..domain.enums import CollectorHealthStatus, CollectorRunStatus
from ..domain.models import CollectorRun
from .ports import CollectorRunRepository

_HISTORY_LIMIT = 200
CONDITION_STALE = "collector_stale"
CONDITION_FAILING = "collector_failing"


@dataclass(frozen=True)
class CollectorHealth:
    source: str
    status: CollectorHealthStatus
    alert_condition: str | None
    detail: str
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_fetched_count: int = 0
    last_sent_count: int = 0
    pending_count: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None


class CollectorHealthService:
    def __init__(self, runs: CollectorRunRepository, stale_after: timedelta | None, failure_threshold: int) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self._runs = runs
        self._stale_after = stale_after
        self._failure_threshold = failure_threshold

    def evaluate(self, now: datetime) -> list[CollectorHealth]:
        return [self._evaluate_source(source, now) for source in sorted(self._runs.sources())]

    def _evaluate_source(self, source: str, now: datetime) -> CollectorHealth:
        history = self._runs.find(source=source, limit=_HISTORY_LIMIT)
        if not history:
            return CollectorHealth(source, CollectorHealthStatus.UNKNOWN, None, "no run reports received")
        latest = history[0]
        last_success = next((run for run in history if run.status is not CollectorRunStatus.FAILED), None)
        last_failure = next((run for run in history if run.status is not CollectorRunStatus.SUCCESS), None)
        failures = 0
        for run in history:
            if run.status is not CollectorRunStatus.FAILED:
                break
            failures += 1
        status, condition, detail = self._classify(latest, last_success, failures, now)
        return CollectorHealth(
            source=source,
            status=status,
            alert_condition=condition,
            detail=detail,
            last_run_at=latest.finished_at,
            last_run_status=latest.status.value,
            last_success_at=last_success.finished_at if last_success else None,
            last_failure_at=last_failure.finished_at if last_failure else None,
            last_fetched_count=latest.fetched_count,
            last_sent_count=latest.sent_count,
            pending_count=latest.pending_count,
            consecutive_failures=failures,
            last_error=latest.error_message,
        )

    def _classify(
        self, latest: CollectorRun, last_success: CollectorRun | None, failures: int, now: datetime
    ) -> tuple[CollectorHealthStatus, str | None, str]:
        if self._stale_after is not None:
            # received_at uses the monitor's clock, so collector clock skew cannot hide staleness.
            reference = last_success.received_at if last_success else None
            if reference is None or now - reference > self._stale_after:
                minutes = int(self._stale_after.total_seconds() // 60)
                return CollectorHealthStatus.STALE, CONDITION_STALE, f"no successful collection in the last {minutes} minutes"
        if latest.status is CollectorRunStatus.FAILED:
            condition = CONDITION_FAILING if failures >= self._failure_threshold else None
            return CollectorHealthStatus.DEGRADED, condition, f"{failures} consecutive failed run(s)"
        if latest.status is CollectorRunStatus.PARTIAL:
            return CollectorHealthStatus.DEGRADED, None, "latest run completed partially"
        return CollectorHealthStatus.HEALTHY, None, "latest run succeeded"
