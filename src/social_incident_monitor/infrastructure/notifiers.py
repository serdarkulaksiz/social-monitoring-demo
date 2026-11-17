"""Notification providers.

``LogNotifier`` is the only provider in the POC. E-mail, Microsoft Teams or other
providers are added by implementing the ``Notifier`` protocol and registering the
instance in ``container.build_notifiers``; the alert service does not change.
"""

from __future__ import annotations

import logging

from ..domain.models import Alert

_alert_logger = logging.getLogger("social_incident_monitor.alerts")


class LogNotifier:
    name = "log"

    def notify(self, alert: Alert) -> None:
        _alert_logger.warning(
            "ALERT %s type=%s severity=%s service=%s issue=%s complaints=%d unique_users=%d incident=%s reason=%s",
            alert.alarm_id,
            alert.alert_type.value,
            alert.severity.value,
            alert.service,
            alert.issue_category,
            alert.complaints_count,
            alert.unique_users_count,
            alert.incident_id or "-",
            alert.detection_reason,
        )
