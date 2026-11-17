from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from social_incident_monitor.application.audit_service import AuditService
from social_incident_monitor.application.errors import StorageError
from social_incident_monitor.application.incident_service import IncidentEvent, IncidentEventType
from social_incident_monitor.domain.enums import AuditEventType, IncidentStatus, IssueCategory, ServiceCategory, Severity
from social_incident_monitor.domain.models import AUDIT_GENESIS_HASH, Incident
from social_incident_monitor.infrastructure.jsonl.repositories import AUDIT_FILE, JsonlAuditLog

from conftest import START


def _incident(**changes: object) -> Incident:
    base = Incident(
        incident_id="inc-1",
        service_category=ServiceCategory.ATM,
        issue_category=IssueCategory.ATM_FAILURE,
        status=IncidentStatus.OPEN,
        severity=Severity.WARNING,
        current_level=Severity.WARNING,
        opened_at=START,
        updated_at=START,
        last_triggered_at=START,
        window_minutes=15,
        complaints_count=12,
        unique_users_count=11,
        peak_unique_users_count=11,
        detection_reason="11 unique users",
        detection_rule="absolute_threshold",
    )
    return base.model_copy(update=changes)


def _service(tmp_path: Path) -> tuple[AuditService, JsonlAuditLog]:
    log = JsonlAuditLog(tmp_path)
    ids = iter(f"evt-{n}" for n in range(1, 100))
    return AuditService(log, id_factory=lambda: next(ids)), log


def test_entries_form_a_verifiable_chain_that_survives_restart(tmp_path: Path) -> None:
    service, log = _service(tmp_path)
    first = service.record("inc-1", AuditEventType.OPENED, START, "system", "opened")
    second = service.record("inc-1", AuditEventType.NOTE_ADDED, START + timedelta(minutes=1), "Ayşe", "note added", {"note": "x"})
    assert first.previous_hash == AUDIT_GENESIS_HASH and second.previous_hash == first.record_hash
    assert log.verify().ok and log.verify().records == 2
    reloaded = JsonlAuditLog(tmp_path)
    assert reloaded.head_hash() == second.record_hash
    assert [event.event_id for event in reloaded.events("inc-1")] == ["evt-1", "evt-2"]
    assert reloaded.integrity_problems == []


def test_editing_an_entry_is_detected(tmp_path: Path) -> None:
    service, log = _service(tmp_path)
    service.record("inc-1", AuditEventType.ACKNOWLEDGED, START, "Ayşe", "acknowledged")
    service.record("inc-1", AuditEventType.RESOLVED, START + timedelta(minutes=5), "Ayşe", "resolved manually")
    path = tmp_path / AUDIT_FILE
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["actor"] = "Başkası"  # rewrite who acknowledged
    lines[0] = json.dumps(record, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = log.verify()
    assert not result.ok and "content does not match its hash" in result.problems[0]
    assert JsonlAuditLog(tmp_path).integrity_problems  # also reported on start-up


def test_removing_an_entry_is_detected(tmp_path: Path) -> None:
    service, log = _service(tmp_path)
    for minute in range(3):
        service.record("inc-1", AuditEventType.NOTE_ADDED, START + timedelta(minutes=minute), "Ayşe", "note added")
    path = tmp_path / AUDIT_FILE
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    result = log.verify()
    assert not result.ok and "does not link to the previous entry" in result.problems[0]


def test_append_refuses_events_that_do_not_link_to_the_head(tmp_path: Path) -> None:
    service, log = _service(tmp_path)
    event = service.record("inc-1", AuditEventType.OPENED, START, "system", "opened")
    stale = event.model_copy(update={"event_id": "evt-x"})
    stale = stale.model_copy(update={"record_hash": stale.computed_hash()})
    with pytest.raises(StorageError, match="chain conflict"):
        log.append(stale)  # links to the genesis hash, but the head has moved on
    forged = event.model_copy(update={"previous_hash": log.head_hash(), "summary": "changed"})
    with pytest.raises(StorageError, match="does not match"):
        log.append(forged)


def test_incident_events_map_to_audit_entries(tmp_path: Path) -> None:
    service, log = _service(tmp_path)
    opened = _incident()
    escalated = _incident(severity=Severity.CRITICAL, current_level=Severity.CRITICAL, unique_users_count=30)
    count_only = _incident(severity=Severity.CRITICAL, current_level=Severity.CRITICAL, unique_users_count=31)
    lowered = _incident(severity=Severity.CRITICAL, current_level=Severity.WARNING, unique_users_count=12)
    service.record_incident_events(
        [
            IncidentEvent(IncidentEventType.OPENED, opened),
            IncidentEvent(IncidentEventType.ESCALATED, escalated, previous_severity=Severity.WARNING, previous_level=Severity.WARNING),
            IncidentEvent(IncidentEventType.UPDATED, count_only, previous_level=Severity.CRITICAL),
            IncidentEvent(IncidentEventType.UPDATED, lowered, previous_level=Severity.CRITICAL),
            IncidentEvent(IncidentEventType.ACKNOWLEDGED, lowered, actor="Ayşe", note="ekip çalışıyor"),
        ],
        START,
    )
    entries = log.events("inc-1")
    assert [entry.event_type for entry in entries] == [
        AuditEventType.OPENED,
        AuditEventType.ESCALATED,
        AuditEventType.LEVEL_CHANGED,
        AuditEventType.ACKNOWLEDGED,
    ]
    assert entries[1].details["previous_severity"] == "WARNING" and entries[1].details["unique_users"] == 30
    assert entries[2].details["previous_level"] == "CRITICAL"
    assert (entries[3].actor, entries[3].details["note"]) == ("Ayşe", "ekip çalışıyor")
    assert all(entry.actor == "system" for entry in entries[:3])
