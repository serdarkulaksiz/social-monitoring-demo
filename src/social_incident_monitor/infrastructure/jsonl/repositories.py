"""JSONL implementations of the application repositories.

Every repository loads its file once at start-up into an in-memory index and then
serves reads from memory. Writes go to disk first; memory is only updated after the
write succeeded, so a failed write never leaves the index ahead of the file.

Append-only files: posts, analysis (latest result per post wins), engagement (newest
``observed_at`` per post wins), collection runs.
Versioned files: incidents and alerts. Each change appends a full snapshot with an
incremented ``version``; on load the highest version per id wins. ``compact`` rewrites
a versioned file atomically with only the latest snapshots.
Audit trail: incident_events.jsonl is append-only and hash-chained; it is never compacted.
"""

from __future__ import annotations

import bisect
import logging
import threading
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ...application.errors import StorageError
from ...application.ports import AuditVerification
from ...domain.enums import AlertStatus, AlertType, IncidentStatus, IssueCategory, ServiceCategory
from ...domain.models import (
    AUDIT_GENESIS_HASH,
    Alert,
    AnalysisResult,
    CollectorRun,
    EngagementSnapshot,
    Feedback,
    Incident,
    IncidentAuditEvent,
    Post,
)
from .jsonl_file import JsonlFile

logger = logging.getLogger(__name__)

POSTS_FILE = "posts.jsonl"
ANALYSIS_FILE = "analysis.jsonl"
ENGAGEMENT_FILE = "engagement.jsonl"
FEEDBACK_FILE = "feedback.jsonl"
INCIDENTS_FILE = "incidents.jsonl"
AUDIT_FILE = "incident_events.jsonl"
ALERTS_FILE = "alerts.jsonl"
COLLECTION_RUNS_FILE = "collection_runs.jsonl"


def _load[M: BaseModel](file: JsonlFile, model: type[M]) -> Iterable[M]:
    for index, record in enumerate(file.read(), start=1):
        try:
            yield model.model_validate(record)
        except ValidationError:
            file.mark_corrupt(f"record #{index} failed validation")


def _dump(entity: BaseModel) -> dict[str, Any]:
    return entity.model_dump(mode="json")


def _page[T](items: list[T], limit: int, offset: int) -> list[T]:
    return items[offset : offset + limit]


class JsonlPostRepository:
    def __init__(self, data_dir: Path) -> None:
        self._file = JsonlFile(data_dir / POSTS_FILE)
        self._lock = threading.RLock()
        self._posts: dict[str, Post] = {}
        self._by_created: list[tuple[datetime, str]] = []
        for post in _load(self._file, Post):
            if post.post_id not in self._posts:
                self._index(post)

    def add_if_absent(self, post: Post) -> bool:
        with self._lock:
            if post.post_id in self._posts:
                return False
            self._file.append([_dump(post)])
            self._index(post)
            return True

    def get(self, post_id: str) -> Post | None:
        return self._posts.get(post_id)

    def find(
        self, *, source: str | None = None, created_since: datetime | None = None, limit: int, offset: int = 0
    ) -> list[Post]:
        with self._lock:
            start = 0 if created_since is None else bisect.bisect_left(self._by_created, (created_since, ""))
            selected = (self._posts[post_id] for _, post_id in reversed(self._by_created[start:]))
            matching = [post for post in selected if source is None or post.source == source]
        return _page(matching, limit, offset)

    def find_created_between(self, start: datetime, end: datetime) -> list[Post]:
        with self._lock:
            low = bisect.bisect_right(self._by_created, (start, "￿"))
            high = bisect.bisect_right(self._by_created, (end, "￿"))
            return [self._posts[post_id] for _, post_id in self._by_created[low:high]]

    def count(self) -> int:
        return len(self._posts)

    def diagnostics(self) -> dict[str, int]:
        return {POSTS_FILE: self._file.corrupt_lines}

    def _index(self, post: Post) -> None:
        self._posts[post.post_id] = post
        bisect.insort(self._by_created, (post.created_at, post.post_id))


class JsonlAnalysisRepository:
    def __init__(self, data_dir: Path) -> None:
        self._file = JsonlFile(data_dir / ANALYSIS_FILE)
        self._lock = threading.RLock()
        self._results: dict[str, AnalysisResult] = {result.post_id: result for result in _load(self._file, AnalysisResult)}

    def save(self, result: AnalysisResult) -> None:
        with self._lock:
            self._file.append([_dump(result)])
            self._results[result.post_id] = result

    def get(self, post_id: str) -> AnalysisResult | None:
        return self._results.get(post_id)

    def find(
        self,
        *,
        is_complaint: bool | None = None,
        service_category: ServiceCategory | None = None,
        limit: int,
        offset: int = 0,
    ) -> list[AnalysisResult]:
        with self._lock:
            matching = [
                result
                for result in self._results.values()
                if (is_complaint is None or result.is_complaint == is_complaint)
                and (service_category is None or result.service_category == service_category)
            ]
        matching.sort(key=lambda result: result.analyzed_at, reverse=True)
        return _page(matching, limit, offset)

    def iter_all(self) -> Iterable[AnalysisResult]:
        with self._lock:
            return list(self._results.values())

    def diagnostics(self) -> dict[str, int]:
        return {ANALYSIS_FILE: self._file.corrupt_lines}


class JsonlFeedbackRepository:
    """Analyst labels; every label is kept on disk, the latest one per post is current."""

    def __init__(self, data_dir: Path) -> None:
        self._file = JsonlFile(data_dir / FEEDBACK_FILE)
        self._lock = threading.RLock()
        self._latest: dict[str, Feedback] = {}
        for feedback in _load(self._file, Feedback):
            self._latest.pop(feedback.post_id, None)  # keep insertion order = order of the latest label
            self._latest[feedback.post_id] = feedback

    def save(self, feedback: Feedback) -> None:
        with self._lock:
            self._file.append([_dump(feedback)])
            self._latest.pop(feedback.post_id, None)
            self._latest[feedback.post_id] = feedback

    def get(self, post_id: str) -> Feedback | None:
        return self._latest.get(post_id)

    def find(self, *, limit: int, offset: int = 0) -> list[Feedback]:
        with self._lock:
            newest_first = list(reversed(self._latest.values()))
        return _page(newest_first, limit, offset)

    def iter_latest(self) -> Iterable[Feedback]:
        with self._lock:
            return list(self._latest.values())

    def diagnostics(self) -> dict[str, int]:
        return {FEEDBACK_FILE: self._file.corrupt_lines}


class JsonlEngagementRepository:
    def __init__(self, data_dir: Path) -> None:
        self._file = JsonlFile(data_dir / ENGAGEMENT_FILE)
        self._lock = threading.RLock()
        self._latest: dict[str, EngagementSnapshot] = {}
        for snapshot in _load(self._file, EngagementSnapshot):
            if self._is_newer(snapshot):
                self._latest[snapshot.post_id] = snapshot

    def save(self, snapshot: EngagementSnapshot) -> bool:
        with self._lock:
            if not self._is_newer(snapshot):
                return False
            self._file.append([_dump(snapshot)])
            self._latest[snapshot.post_id] = snapshot
            return True

    def get(self, post_id: str) -> EngagementSnapshot | None:
        return self._latest.get(post_id)

    def diagnostics(self) -> dict[str, int]:
        return {ENGAGEMENT_FILE: self._file.corrupt_lines}

    def _is_newer(self, snapshot: EngagementSnapshot) -> bool:
        current = self._latest.get(snapshot.post_id)
        return current is None or snapshot.observed_at > current.observed_at


class _VersionedStore[E: BaseModel]:
    """Latest-version-wins store for entities that change over time."""

    def __init__(self, file: JsonlFile, model: type[E], key: Callable[[E], str]) -> None:
        self._file = file
        self._key = key
        self._lock = threading.RLock()
        self._latest: dict[str, E] = {}
        for entity in _load(file, model):
            current = self._latest.get(key(entity))
            if current is None or entity.version >= current.version:  # type: ignore[attr-defined]
                self._latest[key(entity)] = entity

    def save(self, entity: E) -> None:
        with self._lock:
            current = self._latest.get(self._key(entity))
            expected = 1 if current is None else current.version + 1  # type: ignore[attr-defined]
            if entity.version != expected:  # type: ignore[attr-defined]
                raise StorageError(
                    f"version conflict for {self._key(entity)}: expected {expected}, got {entity.version}"  # type: ignore[attr-defined]
                )
            self._file.append([_dump(entity)])
            self._latest[self._key(entity)] = entity

    def get(self, key: str) -> E | None:
        return self._latest.get(key)

    def values(self) -> list[E]:
        with self._lock:
            return list(self._latest.values())

    def compact(self) -> None:
        with self._lock:
            self._file.replace_all(_dump(entity) for entity in self._latest.values())

    @property
    def corrupt_lines(self) -> int:
        return self._file.corrupt_lines


class JsonlIncidentRepository:
    def __init__(self, data_dir: Path) -> None:
        self._store = _VersionedStore(JsonlFile(data_dir / INCIDENTS_FILE), Incident, lambda item: item.incident_id)

    def save(self, incident: Incident) -> None:
        self._store.save(incident)

    def get(self, incident_id: str) -> Incident | None:
        return self._store.get(incident_id)

    def find_active(self, service: ServiceCategory, issue: IssueCategory) -> Incident | None:
        for incident in self.list_active():
            if incident.service_category == service and incident.issue_category == issue:
                return incident
        return None

    def list_active(self) -> list[Incident]:
        return [incident for incident in self._store.values() if incident.status.is_active]

    def find(self, *, status: IncidentStatus | None = None, limit: int, offset: int = 0) -> list[Incident]:
        matching = [incident for incident in self._store.values() if status is None or incident.status == status]
        matching.sort(key=lambda incident: incident.updated_at, reverse=True)
        return _page(matching, limit, offset)

    def compact(self) -> None:
        self._store.compact()

    def diagnostics(self) -> dict[str, int]:
        return {INCIDENTS_FILE: self._store.corrupt_lines}


class JsonlAlertRepository:
    def __init__(self, data_dir: Path) -> None:
        self._store = _VersionedStore(JsonlFile(data_dir / ALERTS_FILE), Alert, lambda item: item.alarm_id)

    def save(self, alert: Alert) -> None:
        self._store.save(alert)

    def get(self, alarm_id: str) -> Alert | None:
        return self._store.get(alarm_id)

    def find(
        self,
        *,
        alert_type: AlertType | None = None,
        status: AlertStatus | None = None,
        incident_id: str | None = None,
        limit: int,
        offset: int = 0,
    ) -> list[Alert]:
        matching = [
            alert
            for alert in self._store.values()
            if (alert_type is None or alert.alert_type == alert_type)
            and (status is None or alert.status == status)
            and (incident_id is None or alert.incident_id == incident_id)
        ]
        matching.sort(key=lambda alert: alert.detected_at, reverse=True)
        return _page(matching, limit, offset)

    def compact(self) -> None:
        self._store.compact()

    def diagnostics(self) -> dict[str, int]:
        return {ALERTS_FILE: self._store.corrupt_lines}


class JsonlAuditLog:
    """Hash-chained audit trail. A broken chain is reported (logged, ``integrity_problems``)
    but does not stop the monitor: the evidence of tampering must stay readable."""

    def __init__(self, data_dir: Path) -> None:
        self._file = JsonlFile(data_dir / AUDIT_FILE)
        self._lock = threading.RLock()
        self._by_incident: dict[str, list[IncidentAuditEvent]] = {}
        self._head = AUDIT_GENESIS_HASH
        verification = self._scan(index=True)
        self.integrity_problems = verification.problems
        if not verification.ok:
            logger.error(
                "audit trail %s failed verification: %s (run 'verify-audit' for details)", AUDIT_FILE, verification.problems[0]
            )

    def head_hash(self) -> str:
        return self._head

    def append(self, event: IncidentAuditEvent) -> None:
        with self._lock:
            if event.previous_hash != self._head:
                raise StorageError("audit chain conflict: the event does not link to the latest entry")
            if event.record_hash != event.computed_hash():
                raise StorageError("audit event hash does not match its content")
            self._file.append([_dump(event)])
            self._head = event.record_hash
            self._by_incident.setdefault(event.incident_id, []).append(event)

    def events(self, incident_id: str) -> list[IncidentAuditEvent]:
        with self._lock:
            return list(self._by_incident.get(incident_id, []))

    def verify(self) -> AuditVerification:
        with self._lock:
            return self._scan(index=False)

    def diagnostics(self) -> dict[str, int]:
        return {AUDIT_FILE: self._file.corrupt_lines}

    def _scan(self, *, index: bool) -> AuditVerification:
        file = JsonlFile(self._file.path) if not index else self._file
        problems: list[str] = []
        previous = AUDIT_GENESIS_HASH
        records = 0
        for number, record in enumerate(file.read(), start=1):
            records += 1
            try:
                event = IncidentAuditEvent.model_validate(record)
            except ValidationError:
                problems.append(f"entry {number}: not a valid audit event")
                continue
            if event.previous_hash != previous:
                problems.append(f"entry {number} ({event.event_id}): does not link to the previous entry")
            if event.record_hash != event.computed_hash():
                problems.append(f"entry {number} ({event.event_id}): content does not match its hash")
            previous = event.record_hash
            if index:
                self._by_incident.setdefault(event.incident_id, []).append(event)
        if file.corrupt_lines:
            problems.append(f"{file.corrupt_lines} unreadable line(s) in {AUDIT_FILE}")
        if index:
            self._head = previous
        return AuditVerification(ok=not problems, records=records, problems=problems)


class JsonlCollectorRunRepository:
    def __init__(self, data_dir: Path) -> None:
        self._file = JsonlFile(data_dir / COLLECTION_RUNS_FILE)
        self._lock = threading.RLock()
        self._runs: dict[str, CollectorRun] = {}
        for run in _load(self._file, CollectorRun):
            self._runs.setdefault(run.run_id, run)

    def add(self, run: CollectorRun) -> bool:
        with self._lock:
            if run.run_id in self._runs:
                return False
            self._file.append([_dump(run)])
            self._runs[run.run_id] = run
            return True

    def find(self, *, source: str | None = None, limit: int, offset: int = 0) -> list[CollectorRun]:
        with self._lock:
            matching = [run for run in self._runs.values() if source is None or run.source == source]
        matching.sort(key=lambda run: (run.received_at, run.finished_at), reverse=True)
        return _page(matching, limit, offset)

    def sources(self) -> set[str]:
        with self._lock:
            return {run.source for run in self._runs.values()}

    def diagnostics(self) -> dict[str, int]:
        return {COLLECTION_RUNS_FILE: self._file.corrupt_lines}
