"""Writes the incident audit trail.

Every state change that matters for a later review is recorded once, in order, with
who did it: the system (detection, auto-resolution) or a named person. Count updates
that do not change the level are not recorded; the incident snapshot keeps the peak.
Without a login the person's name is self-declared; it becomes the directory identity
once authentication is added.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from ..domain.enums import AuditEventType
from ..domain.models import Incident, IncidentAuditEvent
from .incident_service import IncidentEvent, IncidentEventType
from .ports import AuditLog


def _new_event_id() -> str:
    return f"evt-{uuid.uuid4().hex[:16]}"


class AuditService:
    def __init__(self, log: AuditLog, id_factory: Callable[[], str] = _new_event_id) -> None:
        self._log = log
        self._new_id = id_factory

    def record(
        self,
        incident_id: str,
        event_type: AuditEventType,
        occurred_at: datetime,
        actor: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
    ) -> IncidentAuditEvent:
        draft = IncidentAuditEvent(
            event_id=self._new_id(),
            incident_id=incident_id,
            event_type=event_type,
            occurred_at=occurred_at,
            actor=actor,
            summary=summary,
            details=dict(details or {}),
            previous_hash=self._log.head_hash(),
            record_hash="",
        )
        event = draft.model_copy(update={"record_hash": draft.computed_hash()})
        self._log.append(event)
        return event

    def record_incident_events(self, events: Sequence[IncidentEvent], now: datetime) -> None:
        for event in events:
            entry = self._entry_for(event)
            if entry is not None:
                event_type, summary, details = entry
                self.record(event.incident.incident_id, event_type, now, event.actor, summary, details)

    def timeline(self, incident_id: str) -> list[IncidentAuditEvent]:
        return self._log.events(incident_id)

    @staticmethod
    def _entry_for(event: IncidentEvent) -> tuple[AuditEventType, str, dict[str, Any]] | None:
        incident = event.incident
        counts = _counts(incident)
        if event.type is IncidentEventType.OPENED:
            return AuditEventType.OPENED, f"opened at {incident.severity.value}: {incident.detection_reason}", counts
        if event.type is IncidentEventType.ESCALATED:
            previous = event.previous_severity.value if event.previous_severity else "-"
            return (
                AuditEventType.ESCALATED,
                f"escalated {previous} -> {incident.severity.value}: {incident.detection_reason}",
                {**counts, "previous_severity": previous},
            )
        if event.type is IncidentEventType.UPDATED:
            if event.previous_level is None or event.previous_level is incident.current_level:
                return None  # count change only
            return (
                AuditEventType.LEVEL_CHANGED,
                f"current level {event.previous_level.value} -> {incident.current_level.value}",
                {**counts, "previous_level": event.previous_level.value},
            )
        if event.type is IncidentEventType.ACKNOWLEDGED:
            return AuditEventType.ACKNOWLEDGED, "acknowledged", {"note": event.note} if event.note else {}
        if event.type is IncidentEventType.RESOLVED:
            details: dict[str, Any] = {"reason": incident.resolution_reason or ""}
            if event.note:
                details["note"] = event.note
            return AuditEventType.RESOLVED, incident.resolution_reason or "resolved", details
        return None


def _counts(incident: Incident) -> dict[str, Any]:
    return {
        "severity": incident.severity.value,
        "current_level": incident.current_level.value,
        "unique_users": incident.unique_users_count,
        "complaints": incident.complaints_count,
        "window_minutes": incident.window_minutes,
        "rule": incident.detection_rule,
        "detection": incident.detection_details,
    }
