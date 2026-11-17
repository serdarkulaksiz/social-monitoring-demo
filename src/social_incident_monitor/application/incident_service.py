"""Incident lifecycle management.

Rules:
* At most one active (OPEN or ACKNOWLEDGED) incident exists per (service, issue).
  While the problem continues, that incident is updated instead of opening a new one.
* ``severity`` is the highest level reached while active; it escalates WARNING → CRITICAL
  but never de-escalates. ``current_level`` reflects the latest evaluation.
* An active incident is resolved automatically once no WARNING-or-higher signal has
  been seen for ``resolve_after``. Manual acknowledge/resolve are also supported and
  record who did it (``actor``) and an optional note.
* A problem that returns after resolution opens a new incident.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from ..domain.enums import IncidentStatus, Severity
from ..domain.models import Incident
from .detection.engine import DetectionSignal
from .errors import LifecycleError, NotFoundError
from .ports import IncidentRepository

SYSTEM_ACTOR = "system"


class IncidentEventType(StrEnum):
    OPENED = "OPENED"
    ESCALATED = "ESCALATED"
    UPDATED = "UPDATED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True)
class IncidentEvent:
    type: IncidentEventType
    incident: Incident
    previous_severity: Severity | None = None
    previous_level: Severity | None = None  # current_level before this change
    actor: str = SYSTEM_ACTOR
    note: str | None = None


def _new_incident_id() -> str:
    return f"inc-{uuid.uuid4().hex[:16]}"


class IncidentService:
    def __init__(
        self,
        repository: IncidentRepository,
        resolve_after: timedelta,
        id_factory: Callable[[], str] = _new_incident_id,
    ) -> None:
        if resolve_after <= timedelta(0):
            raise ValueError("resolve_after must be positive")
        self._repository = repository
        self._resolve_after = resolve_after
        # While a problem persists with unchanged numbers, last_triggered_at is still
        # persisted periodically so auto-resolution timing survives a restart.
        self._heartbeat = resolve_after / 6
        self._new_id = id_factory

    def reconcile(self, signals: Sequence[DetectionSignal], now: datetime) -> list[IncidentEvent]:
        events: list[IncidentEvent] = []
        by_key = {(signal.service, signal.issue): signal for signal in signals}
        triggered = {key for key, signal in by_key.items() if signal.level is not Severity.NORMAL}

        for key in sorted(triggered):
            event = self._apply_trigger(by_key[key], now)
            if event is not None:
                events.append(event)

        for incident in self._repository.list_active():
            key = (incident.service_category, incident.issue_category)
            if key in triggered:
                continue
            event = self._apply_quiet(incident, by_key.get(key), now)
            if event is not None:
                events.append(event)
        return events

    def acknowledge(self, incident_id: str, now: datetime, actor: str, note: str | None = None) -> IncidentEvent:
        incident = self.get(incident_id)
        if incident.status is not IncidentStatus.OPEN:
            raise LifecycleError(f"incident {incident_id} is {incident.status.value}; only OPEN incidents can be acknowledged")
        updated = self._next_version(
            incident, status=IncidentStatus.ACKNOWLEDGED, acknowledged_at=now, acknowledged_by=actor, updated_at=now
        )
        self._repository.save(updated)
        return IncidentEvent(IncidentEventType.ACKNOWLEDGED, updated, actor=actor, note=note)

    def resolve(self, incident_id: str, now: datetime, actor: str, note: str | None = None) -> IncidentEvent:
        incident = self.get(incident_id)
        if not incident.status.is_active:
            raise LifecycleError(f"incident {incident_id} is already RESOLVED")
        return self._resolve(incident, now, "resolved manually", actor, note)

    def list_active(self) -> list[Incident]:
        return self._repository.list_active()

    def get(self, incident_id: str) -> Incident:
        incident = self._repository.get(incident_id)
        if incident is None:
            raise NotFoundError(f"incident {incident_id} not found")
        return incident

    def _apply_trigger(self, signal: DetectionSignal, now: datetime) -> IncidentEvent | None:
        active = self._repository.find_active(signal.service, signal.issue)
        if active is None:
            incident = Incident(
                incident_id=self._new_id(),
                service_category=signal.service,
                issue_category=signal.issue,
                status=IncidentStatus.OPEN,
                severity=signal.level,
                current_level=signal.level,
                opened_at=now,
                updated_at=now,
                last_triggered_at=now,
                window_minutes=signal.window_minutes,
                complaints_count=signal.complaints_count,
                unique_users_count=signal.unique_users_count,
                peak_unique_users_count=signal.unique_users_count,
                detection_reason=signal.reason,
                detection_rule=signal.rule,
                detection_details=dict(signal.details),
                first_complaint_at=signal.first_post_at,
                peak_at=now,
            )
            self._repository.save(incident)
            return IncidentEvent(IncidentEventType.OPENED, incident)

        escalated = signal.level.is_higher_than(active.severity)
        numbers_changed = (
            signal.complaints_count != active.complaints_count
            or signal.unique_users_count != active.unique_users_count
            or signal.level is not active.current_level
            or signal.window_minutes != active.window_minutes
        )
        heartbeat_due = now - active.last_triggered_at >= self._heartbeat
        if not (escalated or numbers_changed or heartbeat_due):
            return None
        new_peak = signal.unique_users_count > active.peak_unique_users_count
        updated = self._next_version(
            active,
            severity=signal.level if escalated else active.severity,
            current_level=signal.level,
            updated_at=now,
            last_triggered_at=now,
            window_minutes=signal.window_minutes,
            complaints_count=signal.complaints_count,
            unique_users_count=signal.unique_users_count,
            peak_unique_users_count=max(active.peak_unique_users_count, signal.unique_users_count),
            peak_at=now if new_peak else active.peak_at,
            detection_reason=signal.reason,
            detection_rule=signal.rule,
            detection_details=dict(signal.details),
        )
        self._repository.save(updated)
        if escalated:
            return IncidentEvent(
                IncidentEventType.ESCALATED, updated, previous_severity=active.severity, previous_level=active.current_level
            )
        return IncidentEvent(IncidentEventType.UPDATED, updated, previous_level=active.current_level)

    def _apply_quiet(self, incident: Incident, signal: DetectionSignal | None, now: datetime) -> IncidentEvent | None:
        if now - incident.last_triggered_at >= self._resolve_after:
            minutes = int(self._resolve_after.total_seconds() // 60)
            return self._resolve(incident, now, f"auto-resolved: below WARNING threshold for {minutes} minutes", SYSTEM_ACTOR)
        complaints = signal.complaints_count if signal else 0
        users = signal.unique_users_count if signal else 0
        if (
            incident.current_level is Severity.NORMAL
            and incident.complaints_count == complaints
            and incident.unique_users_count == users
        ):
            return None
        updated = self._next_version(
            incident,
            current_level=Severity.NORMAL,
            complaints_count=complaints,
            unique_users_count=users,
            updated_at=now,
        )
        self._repository.save(updated)
        return IncidentEvent(IncidentEventType.UPDATED, updated, previous_level=incident.current_level)

    def _resolve(self, incident: Incident, now: datetime, reason: str, actor: str, note: str | None = None) -> IncidentEvent:
        updated = self._next_version(
            incident,
            status=IncidentStatus.RESOLVED,
            current_level=Severity.NORMAL,
            resolved_at=now,
            resolution_reason=reason,
            resolved_by=actor,
            resolution_note=note,
            updated_at=now,
        )
        self._repository.save(updated)
        return IncidentEvent(IncidentEventType.RESOLVED, updated, previous_level=incident.current_level, actor=actor, note=note)

    @staticmethod
    def _next_version(incident: Incident, **changes: object) -> Incident:
        return incident.model_copy(update={**changes, "version": incident.version + 1})
