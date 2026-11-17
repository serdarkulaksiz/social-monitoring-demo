from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from social_incident_monitor.application.collector_health_service import CollectorHealthService
from social_incident_monitor.domain.enums import CollectorHealthStatus, CollectorRunStatus
from social_incident_monitor.domain.models import CollectorRun
from social_incident_monitor.infrastructure.jsonl.repositories import JsonlCollectorRunRepository

from conftest import START


def run(number: int, status: CollectorRunStatus, minutes: int, fetched: int = 0) -> CollectorRun:
    at = START + timedelta(minutes=minutes)
    return CollectorRun(
        run_id=f"run-{number}", source="bluesky", started_at=at, finished_at=at, received_at=at, status=status,
        fetched_count=fetched, new_count=fetched, sent_count=fetched, pending_count=0,
        error_message=None if status is CollectorRunStatus.SUCCESS else "HTTP 503 from source",
    )


def service(tmp_path: Path, *runs: CollectorRun) -> CollectorHealthService:
    repository = JsonlCollectorRunRepository(tmp_path)
    for item in runs:
        repository.add(item)
    return CollectorHealthService(repository, timedelta(minutes=15), failure_threshold=3)


def test_no_reports_is_unknown_not_an_alarm(tmp_path: Path) -> None:
    assert service(tmp_path).evaluate(START) == []


def test_successful_run_with_zero_posts_is_healthy(tmp_path: Path) -> None:
    [health] = service(tmp_path, run(1, CollectorRunStatus.SUCCESS, 0, fetched=0)).evaluate(START + timedelta(minutes=1))
    assert health.status is CollectorHealthStatus.HEALTHY
    assert health.alert_condition is None


def test_consecutive_failures_raise_failing_condition(tmp_path: Path) -> None:
    runs = [run(1, CollectorRunStatus.SUCCESS, 0)] + [run(n, CollectorRunStatus.FAILED, n) for n in range(2, 5)]
    [health] = service(tmp_path, *runs).evaluate(START + timedelta(minutes=5))
    assert health.status is CollectorHealthStatus.DEGRADED
    assert health.consecutive_failures == 3
    assert health.alert_condition == "collector_failing"
    assert health.last_error == "HTTP 503 from source"


def test_single_failure_is_degraded_without_alert(tmp_path: Path) -> None:
    [health] = service(tmp_path, run(1, CollectorRunStatus.SUCCESS, 0), run(2, CollectorRunStatus.FAILED, 1)).evaluate(
        START + timedelta(minutes=2)
    )
    assert health.status is CollectorHealthStatus.DEGRADED and health.alert_condition is None


def test_no_recent_success_is_stale(tmp_path: Path) -> None:
    [health] = service(tmp_path, run(1, CollectorRunStatus.SUCCESS, 0)).evaluate(START + timedelta(minutes=16))
    assert health.status is CollectorHealthStatus.STALE
    assert health.alert_condition == "collector_stale"
