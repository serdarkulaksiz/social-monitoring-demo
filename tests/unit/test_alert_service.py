from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from social_incident_monitor.application.alert_service import AlertService
from social_incident_monitor.application.detection.engine import DetectionSignal
from social_incident_monitor.application.incident_service import IncidentService
from social_incident_monitor.domain.enums import AlertStatus, AlertType, IssueCategory, ServiceCategory, Severity
from social_incident_monitor.domain.models import Alert
from social_incident_monitor.infrastructure.jsonl.repositories import JsonlAlertRepository, JsonlIncidentRepository

from conftest import START


class RecordingNotifier:
    name = "recording"

    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def notify(self, alert: Alert) -> None:
        self.alerts.append(alert)


class BrokenNotifier:
    name = "broken"

    def notify(self, alert: Alert) -> None:
        raise RuntimeError("provider down")


def signal(level: Severity, users: int) -> DetectionSignal:
    return DetectionSignal(ServiceCategory.ATM, IssueCategory.ATM_FAILURE, level, 15, users, users, "reason", "absolute_threshold")


@pytest.fixture
def parts(tmp_path: Path) -> tuple[IncidentService, AlertService, JsonlAlertRepository, RecordingNotifier]:
    alerts = JsonlAlertRepository(tmp_path)
    notifier = RecordingNotifier()
    incidents = IncidentService(JsonlIncidentRepository(tmp_path), timedelta(minutes=30))
    return incidents, AlertService(alerts, [BrokenNotifier(), notifier]), alerts, notifier


def test_alert_on_open_and_on_escalation_only(parts) -> None:  # type: ignore[no-untyped-def]
    incidents, service, repository, notifier = parts
    created = service.handle_incident_events(incidents.reconcile([signal(Severity.WARNING, 12)], START), START)
    assert [alert.severity for alert in created] == [Severity.WARNING]

    later = START + timedelta(minutes=1)
    assert service.handle_incident_events(incidents.reconcile([signal(Severity.WARNING, 14)], later), later) == []

    later = START + timedelta(minutes=2)
    escalated = service.handle_incident_events(incidents.reconcile([signal(Severity.CRITICAL, 30)], later), later)
    assert [alert.severity for alert in escalated] == [Severity.CRITICAL]
    assert [alert.severity for alert in notifier.alerts] == [Severity.WARNING, Severity.CRITICAL]
    assert len(repository.find(limit=10)) == 2


def test_alert_fields(parts) -> None:  # type: ignore[no-untyped-def]
    incidents, service, _, _ = parts
    [alert] = service.handle_incident_events(incidents.reconcile([signal(Severity.CRITICAL, 30)], START), START)
    assert alert.alert_type is AlertType.SERVICE_INCIDENT
    assert alert.incident_id is not None
    assert alert.service == "atm" and alert.issue_category == "atm_failure"
    assert alert.unique_users_count == 30 and alert.complaints_count == 30
    assert alert.status is AlertStatus.ACTIVE and alert.detected_at == START


def test_reprocessing_same_event_is_idempotent(parts) -> None:  # type: ignore[no-untyped-def]
    incidents, service, repository, _ = parts
    events = incidents.reconcile([signal(Severity.WARNING, 12)], START)
    service.handle_incident_events(events, START)
    assert service.handle_incident_events(events, START) == []
    assert len(repository.find(limit=10)) == 1


def test_resolution_resolves_alerts(parts) -> None:  # type: ignore[no-untyped-def]
    incidents, service, repository, _ = parts
    [alert] = service.handle_incident_events(incidents.reconcile([signal(Severity.WARNING, 12)], START), START)
    later = START + timedelta(minutes=31)
    service.handle_incident_events(incidents.reconcile([], later), later)
    assert repository.get(alert.alarm_id).status is AlertStatus.RESOLVED  # type: ignore[union-attr]


def test_collector_health_alert_lifecycle(parts) -> None:  # type: ignore[no-untyped-def]
    _, service, repository, notifier = parts
    first = service.sync_collector_health("bluesky", "collector_stale", "no success", START)
    assert first is not None and first.alert_type is AlertType.COLLECTOR_HEALTH and first.incident_id is None
    assert service.sync_collector_health("bluesky", "collector_stale", "no success", START) is None  # no duplicate
    service.sync_collector_health("bluesky", None, "healthy", START + timedelta(minutes=1))
    assert repository.get(first.alarm_id).status is AlertStatus.RESOLVED  # type: ignore[union-attr]
    assert len(notifier.alerts) == 1
