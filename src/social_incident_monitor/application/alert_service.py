"""Alert creation, de-duplication and fan-out to notification providers.

Kept separate from incident detection: detection decides *whether* something is wrong,
this service decides *whether to notify* and records the alert.

* A service-incident alert is raised when an incident opens and again only when its
  severity escalates. Count updates never create new alerts.
* At most one alert exists per (incident, severity); re-processing is idempotent.
* Collector-health alerts are a separate alert type and never represent a banking
  service failure. Reputation incidents raise REPUTATION_SIGNAL alerts, kept apart
  from service incidents.
* A failing notifier is logged and does not prevent the alert from being stored.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime

from ..domain.enums import AlertStatus, AlertType, ServiceCategory, Severity
from ..domain.models import Alert, Incident
from .incident_service import IncidentEvent, IncidentEventType
from .ports import AlertRepository, Notifier

logger = logging.getLogger(__name__)

_ALERT_LOOKUP_LIMIT = 1000


def _new_alarm_id() -> str:
    return f"alm-{uuid.uuid4().hex[:16]}"


class AlertService:
    def __init__(
        self,
        repository: AlertRepository,
        notifiers: Sequence[Notifier],
        id_factory: Callable[[], str] = _new_alarm_id,
    ) -> None:
        self._repository = repository
        self._notifiers = list(notifiers)
        self._new_id = id_factory

    def handle_incident_events(self, events: Sequence[IncidentEvent], now: datetime) -> list[Alert]:
        created: list[Alert] = []
        for event in events:
            if event.type in (IncidentEventType.OPENED, IncidentEventType.ESCALATED):
                alert = self._raise_for_incident(event.incident, now)
                if alert is not None:
                    created.append(alert)
            elif event.type is IncidentEventType.ACKNOWLEDGED:
                self._set_incident_alert_status(event.incident.incident_id, AlertStatus.ACKNOWLEDGED, now)
            elif event.type is IncidentEventType.RESOLVED:
                self._set_incident_alert_status(event.incident.incident_id, AlertStatus.RESOLVED, now)
        return created

    def sync_collector_health(self, source: str, condition: str | None, reason: str, now: datetime) -> Alert | None:
        """Keep exactly one active health alert per source while a condition holds.

        ``condition`` is e.g. ``collector_stale`` or ``collector_failing``; ``None`` means healthy.
        Returns the newly created alert, if any.
        """
        service = f"collector:{source}"
        active = [
            alert
            for alert in self._repository.find(
                alert_type=AlertType.COLLECTOR_HEALTH, status=AlertStatus.ACTIVE, limit=_ALERT_LOOKUP_LIMIT
            )
            if alert.service == service
        ]
        for alert in active:
            if alert.issue_category != condition:
                self._repository.save(self._with_status(alert, AlertStatus.RESOLVED, now))
        if condition is None or any(alert.issue_category == condition for alert in active):
            return None
        alert = Alert(
            alarm_id=self._new_id(),
            alert_type=AlertType.COLLECTOR_HEALTH,
            incident_id=None,
            service=service,
            issue_category=condition,
            severity=Severity.WARNING,
            detected_at=now,
            complaints_count=0,
            unique_users_count=0,
            detection_reason=reason,
            status=AlertStatus.ACTIVE,
            updated_at=now,
        )
        self._store_and_notify(alert)
        return alert

    def _raise_for_incident(self, incident: Incident, now: datetime) -> Alert | None:
        existing = self._repository.find(incident_id=incident.incident_id, limit=_ALERT_LOOKUP_LIMIT)
        if any(alert.severity is incident.severity for alert in existing):
            return None
        alert_type = (
            AlertType.REPUTATION_SIGNAL if incident.service_category is ServiceCategory.REPUTATION else AlertType.SERVICE_INCIDENT
        )
        alert = Alert(
            alarm_id=self._new_id(),
            alert_type=alert_type,
            incident_id=incident.incident_id,
            service=incident.service_category.value,
            issue_category=incident.issue_category.value,
            severity=incident.severity,
            detected_at=now,
            complaints_count=incident.complaints_count,
            unique_users_count=incident.unique_users_count,
            detection_reason=incident.detection_reason,
            status=AlertStatus.ACTIVE,
            updated_at=now,
        )
        self._store_and_notify(alert)
        return alert

    def _set_incident_alert_status(self, incident_id: str, status: AlertStatus, now: datetime) -> None:
        for alert in self._repository.find(incident_id=incident_id, limit=_ALERT_LOOKUP_LIMIT):
            if alert.status is not AlertStatus.RESOLVED and alert.status is not status:
                self._repository.save(self._with_status(alert, status, now))

    def _store_and_notify(self, alert: Alert) -> None:
        self._repository.save(alert)
        for notifier in self._notifiers:
            try:
                notifier.notify(alert)
            except Exception:  # a broken provider must not block other providers or storage
                logger.exception("notifier %s failed for alert %s", notifier.name, alert.alarm_id)

    @staticmethod
    def _with_status(alert: Alert, status: AlertStatus, now: datetime) -> Alert:
        return alert.model_copy(update={"status": status, "updated_at": now, "version": alert.version + 1})
