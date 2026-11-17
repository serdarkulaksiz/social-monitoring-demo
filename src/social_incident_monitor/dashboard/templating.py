"""Jinja2 environment for the dashboard. Autoescaping is on for every template: post texts,
user names and notes come from outside and are always rendered as text."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from ..classification.locations import LocationMatcher
from . import labels_tr as tr

_LOCATIONS = LocationMatcher.load()


def _time(value: datetime | None, pattern: str = "%Y-%m-%d %H:%M") -> str:
    return value.astimezone(UTC).strftime(pattern) if value else "-"


def _safe_url(value: str | None) -> str | None:
    """Only plain web links are rendered; anything else (e.g. ``javascript:``) is dropped."""
    if value and value.startswith(("https://", "http://")):
        return value
    return None


def _profile_url(author_id: str, source: str) -> str | None:
    if source == "bluesky" and author_id.startswith("did:"):
        return "https://bsky.app/profile/" + quote(author_id, safe=":")
    return None


def _percent(value: float | None) -> str:
    return "-" if value is None else f"%{value * 100:.0f}"


def _number(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}".replace(".", ",")


def build_environment() -> Environment:
    environment = Environment(
        loader=PackageLoader("social_incident_monitor.dashboard", "templates"),
        autoescape=select_autoescape(["html"], default=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    filters: dict[str, Any] = {
        "tr_time": _time,
        "service": lambda value: tr.label(tr.SERVICE, value),
        "issue": lambda value: tr.label(tr.ISSUE, value),
        "severity": lambda value: tr.label(tr.SEVERITY, value),
        "complaint_severity": lambda value: tr.label(tr.COMPLAINT_SEVERITY, value),
        "incident_status": lambda value: tr.label(tr.INCIDENT_STATUS, value),
        "alert_status": lambda value: tr.label(tr.ALERT_STATUS, value),
        "alert_type": lambda value: tr.label(tr.ALERT_TYPE, value),
        "health": lambda value: tr.label(tr.COLLECTOR_HEALTH, value),
        "audit_event": lambda value: tr.label(tr.AUDIT_EVENT, value),
        "actor": tr.actor,
        "duration": tr.duration,
        "detection": tr.detection_text,
        "probability": tr.probability,
        "percent": _percent,
        "number": _number,
        "safe_url": _safe_url,
        "province": _LOCATIONS.name,
        "risk": lambda value: tr.label(tr.ISSUE, value),
        "verdict": lambda value: tr.label(tr.VERDICT, value),
        "sample": lambda value: tr.label(tr.SAMPLE, value),
        "condition": lambda value: tr.label(tr.COLLECTOR_CONDITION, value),
    }
    environment.filters.update(filters)
    environment.globals["profile_url"] = _profile_url
    return environment
