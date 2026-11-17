"""Contracts the application layer depends on.

Each protocol is intentionally small and owned by the application layer, so a storage
or classifier adapter can be swapped (JSONL today, a database later) without touching
business logic.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..domain.enums import AlertStatus, AlertType, IncidentStatus, IssueCategory, ServiceCategory
from ..domain.models import (
    Alert,
    AnalysisResult,
    CollectorRun,
    EngagementSnapshot,
    Feedback,
    Incident,
    IncidentAuditEvent,
    Post,
)


class Clock(Protocol):
    def now(self) -> datetime:
        """Current timezone-aware UTC time."""
        ...


class PostRepository(Protocol):
    def add_if_absent(self, post: Post) -> bool:
        """Store the post unless one with the same post_id exists. Returns True if stored."""
        ...

    def get(self, post_id: str) -> Post | None: ...

    def find(
        self, *, source: str | None = None, created_since: datetime | None = None, limit: int, offset: int = 0
    ) -> list[Post]:
        """Posts ordered newest first by created_at."""
        ...

    def find_created_between(self, start: datetime, end: datetime) -> list[Post]:
        """Posts with start < created_at <= end."""
        ...

    def count(self) -> int: ...


class EngagementRepository(Protocol):
    def save(self, snapshot: EngagementSnapshot) -> bool:
        """Store the snapshot if it is newer than the current one. Returns True if stored."""
        ...

    def get(self, post_id: str) -> EngagementSnapshot | None:
        """Newest snapshot of the post."""
        ...


class AnalysisRepository(Protocol):
    def save(self, result: AnalysisResult) -> None: ...

    def get(self, post_id: str) -> AnalysisResult | None: ...

    def find(
        self,
        *,
        is_complaint: bool | None = None,
        service_category: ServiceCategory | None = None,
        limit: int,
        offset: int = 0,
    ) -> list[AnalysisResult]:
        """Results ordered newest first by analyzed_at."""
        ...

    def iter_all(self) -> Iterable[AnalysisResult]: ...


class FeedbackRepository(Protocol):
    def save(self, feedback: Feedback) -> None: ...

    def get(self, post_id: str) -> Feedback | None:
        """Latest label of the post."""
        ...

    def find(self, *, limit: int, offset: int = 0) -> list[Feedback]:
        """Latest label per post, newest first."""
        ...

    def iter_latest(self) -> Iterable[Feedback]:
        """Latest label per post."""
        ...


class IncidentRepository(Protocol):
    def save(self, incident: Incident) -> None:
        """Persist a new incident or a new version of an existing one."""
        ...

    def get(self, incident_id: str) -> Incident | None: ...

    def find_active(self, service: ServiceCategory, issue: IssueCategory) -> Incident | None: ...

    def list_active(self) -> list[Incident]: ...

    def find(self, *, status: IncidentStatus | None = None, limit: int, offset: int = 0) -> list[Incident]:
        """Incidents ordered by updated_at, newest first."""
        ...


@dataclass(frozen=True)
class AuditVerification:
    ok: bool
    records: int
    problems: list[str]


class AuditLog(Protocol):
    """Append-only, hash-chained record of everything that happened to incidents."""

    def head_hash(self) -> str:
        """``record_hash`` of the last entry (the genesis hash when empty)."""
        ...

    def append(self, event: IncidentAuditEvent) -> None:
        """Store the event; it must link to the current head."""
        ...

    def events(self, incident_id: str) -> list[IncidentAuditEvent]:
        """Entries of one incident in the order they were written."""
        ...

    def verify(self) -> AuditVerification:
        """Re-read the stored log and check every link of the chain."""
        ...


class AlertRepository(Protocol):
    def save(self, alert: Alert) -> None: ...

    def get(self, alarm_id: str) -> Alert | None: ...

    def find(
        self,
        *,
        alert_type: AlertType | None = None,
        status: AlertStatus | None = None,
        incident_id: str | None = None,
        limit: int,
        offset: int = 0,
    ) -> list[Alert]:
        """Alerts ordered by detected_at, newest first."""
        ...


class CollectorRunRepository(Protocol):
    def add(self, run: CollectorRun) -> bool:
        """Store the run unless the run_id already exists. Returns True if stored."""
        ...

    def find(self, *, source: str | None = None, limit: int, offset: int = 0) -> list[CollectorRun]:
        """Runs ordered by arrival (received_at), newest first."""
        ...

    def sources(self) -> set[str]: ...


class StorageDiagnostics(Protocol):
    def diagnostics(self) -> dict[str, int]:
        """Counters such as skipped corrupt lines, keyed by storage name."""
        ...


class Classifier(Protocol):
    classifier_type: str

    def classify(self, post: Post, analyzed_at: datetime) -> AnalysisResult: ...


class TermSource(Protocol):
    """Splits post text into topic terms (see ``classification.terms``)."""

    def terms(self, text: str) -> set[str]: ...

    def display(self, term: str) -> str:
        """Readable form of a term."""
        ...


class Notifier(Protocol):
    name: str

    def notify(self, alert: Alert) -> None: ...
