from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from social_incident_monitor.application.detection.engine import DetectionSignal
from social_incident_monitor.application.errors import LifecycleError, NotFoundError
from social_incident_monitor.application.incident_service import IncidentEventType, IncidentService
from social_incident_monitor.domain.enums import IncidentStatus, IssueCategory, ServiceCategory, Severity
from social_incident_monitor.infrastructure.jsonl.repositories import JsonlIncidentRepository

from conftest import START


def signal(
    level: Severity, users: int = 12, service: ServiceCategory = ServiceCategory.ATM, first_post_at: datetime | None = None
) -> DetectionSignal:
    return DetectionSignal(
        service, IssueCategory.ATM_FAILURE, level, 15, users + 2, users, f"{users} users", "absolute_threshold",
        details={"rule": "absolute_threshold"}, first_post_at=first_post_at,
    )


@pytest.fixture
def repository(tmp_path: Path) -> JsonlIncidentRepository:
    return JsonlIncidentRepository(tmp_path)


@pytest.fixture
def service(repository: JsonlIncidentRepository) -> IncidentService:
    ids = iter(f"inc-{n}" for n in range(1, 100))
    return IncidentService(repository, timedelta(minutes=30), id_factory=lambda: next(ids))


def test_warning_opens_incident(service: IncidentService) -> None:
    [event] = service.reconcile([signal(Severity.WARNING)], START)
    assert event.type is IncidentEventType.OPENED
    assert event.incident.status is IncidentStatus.OPEN
    assert event.incident.severity is Severity.WARNING


def test_normal_signal_opens_nothing(service: IncidentService, repository: JsonlIncidentRepository) -> None:
    assert service.reconcile([signal(Severity.NORMAL, users=3)], START) == []
    assert repository.list_active() == []


def test_continuing_problem_updates_instead_of_duplicating(service: IncidentService, repository: JsonlIncidentRepository) -> None:
    service.reconcile([signal(Severity.WARNING, users=12)], START)
    [event] = service.reconcile([signal(Severity.WARNING, users=15)], START + timedelta(minutes=1))
    assert event.type is IncidentEventType.UPDATED
    assert event.incident.unique_users_count == 15
    assert len(repository.find(limit=10)) == 1
    # identical numbers shortly after: no new version is written
    assert service.reconcile([signal(Severity.WARNING, users=15)], START + timedelta(minutes=2)) == []


def test_escalation_warning_to_critical(service: IncidentService) -> None:
    service.reconcile([signal(Severity.WARNING)], START)
    [event] = service.reconcile([signal(Severity.CRITICAL, users=30)], START + timedelta(minutes=1))
    assert event.type is IncidentEventType.ESCALATED
    assert event.previous_severity is Severity.WARNING
    assert event.incident.severity is Severity.CRITICAL
    assert event.incident.peak_unique_users_count == 30


def test_severity_never_de_escalates_while_active(service: IncidentService) -> None:
    service.reconcile([signal(Severity.CRITICAL, users=30)], START)
    [event] = service.reconcile([signal(Severity.WARNING, users=12)], START + timedelta(minutes=1))
    assert event.incident.severity is Severity.CRITICAL
    assert event.incident.current_level is Severity.WARNING


def test_auto_resolve_after_quiet_period_and_reopen_as_new(service: IncidentService) -> None:
    service.reconcile([signal(Severity.WARNING)], START)
    [quiet] = service.reconcile([], START + timedelta(minutes=10))
    assert quiet.type is IncidentEventType.UPDATED and quiet.incident.current_level is Severity.NORMAL
    [resolved] = service.reconcile([], START + timedelta(minutes=30))
    assert resolved.type is IncidentEventType.RESOLVED
    assert resolved.incident.resolved_at == START + timedelta(minutes=30)
    [reopened] = service.reconcile([signal(Severity.WARNING)], START + timedelta(minutes=40))
    assert reopened.type is IncidentEventType.OPENED
    assert reopened.incident.incident_id != resolved.incident.incident_id


def test_acknowledge_and_resolve_transitions(service: IncidentService) -> None:
    [opened] = service.reconcile([signal(Severity.WARNING)], START)
    incident_id = opened.incident.incident_id
    acknowledged = service.acknowledge(incident_id, START + timedelta(minutes=1), "Ayşe", "inceleniyor")
    assert acknowledged.incident.status is IncidentStatus.ACKNOWLEDGED
    assert acknowledged.incident.acknowledged_by == "Ayşe"
    assert (acknowledged.actor, acknowledged.note) == ("Ayşe", "inceleniyor")
    with pytest.raises(LifecycleError):
        service.acknowledge(incident_id, START + timedelta(minutes=2), "Ayşe")
    # acknowledged incidents are still updated, not duplicated
    [update] = service.reconcile([signal(Severity.CRITICAL, users=30)], START + timedelta(minutes=3))
    assert update.type is IncidentEventType.ESCALATED
    assert update.incident.status is IncidentStatus.ACKNOWLEDGED
    assert update.previous_level is Severity.WARNING
    assert update.incident.peak_at == START + timedelta(minutes=3)
    resolved = service.resolve(incident_id, START + timedelta(minutes=4), "Mehmet", "uygulama düzeltildi")
    assert resolved.incident.status is IncidentStatus.RESOLVED
    assert (resolved.incident.resolved_by, resolved.incident.resolution_note) == ("Mehmet", "uygulama düzeltildi")
    with pytest.raises(LifecycleError):
        service.resolve(incident_id, START + timedelta(minutes=5), "Mehmet")


def test_opened_incident_records_rule_and_first_complaint(service: IncidentService) -> None:
    first = START - timedelta(minutes=12)
    [opened] = service.reconcile([signal(Severity.WARNING, first_post_at=first)], START)
    incident = opened.incident
    assert incident.first_complaint_at == first and incident.peak_at == START
    assert incident.detection_rule == "absolute_threshold"
    assert incident.detection_details == {"rule": "absolute_threshold"}


def test_auto_resolution_is_attributed_to_the_system(service: IncidentService) -> None:
    service.reconcile([signal(Severity.WARNING)], START)
    [resolved] = service.reconcile([], START + timedelta(minutes=31))
    assert resolved.type is IncidentEventType.RESOLVED and resolved.actor == "system"
    assert resolved.incident.resolved_by == "system"


def test_unknown_incident(service: IncidentService) -> None:
    with pytest.raises(NotFoundError):
        service.acknowledge("missing", START, "Ayşe")
