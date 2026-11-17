from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from social_incident_monitor.application.errors import StorageError
from social_incident_monitor.domain.enums import IncidentStatus, IssueCategory, ServiceCategory, Severity
from social_incident_monitor.domain.models import EngagementSnapshot, Incident
from social_incident_monitor.infrastructure.jsonl.repositories import (
    JsonlEngagementRepository,
    JsonlIncidentRepository,
    JsonlPostRepository,
)

from conftest import START, make_post


def _incident(version: int = 1, status: IncidentStatus = IncidentStatus.OPEN) -> Incident:
    return Incident(
        incident_id="inc-1",
        service_category=ServiceCategory.ATM,
        issue_category=IssueCategory.ATM_FAILURE,
        status=status,
        severity=Severity.WARNING,
        current_level=Severity.WARNING,
        opened_at=START,
        updated_at=START,
        last_triggered_at=START,
        window_minutes=15,
        complaints_count=10,
        unique_users_count=10,
        peak_unique_users_count=10,
        detection_reason="test",
        version=version,
    )


def test_posts_are_deduplicated_and_survive_restart(tmp_path: Path) -> None:
    repository = JsonlPostRepository(tmp_path)
    post = make_post("Ziraat ATM paramı vermedi")
    assert repository.add_if_absent(post) is True
    assert repository.add_if_absent(post) is False

    reloaded = JsonlPostRepository(tmp_path)
    assert reloaded.count() == 1
    assert reloaded.add_if_absent(post) is False
    assert reloaded.get(post.post_id) == post


def test_posts_window_query_uses_created_at(tmp_path: Path) -> None:
    repository = JsonlPostRepository(tmp_path)
    for number in range(5):
        repository.add_if_absent(make_post("x", created_at=START + timedelta(minutes=number), number=number))
    found = repository.find_created_between(START + timedelta(minutes=1), START + timedelta(minutes=3))
    assert [post.source_post_id for post in found] == ["p-2", "p-3"]
    newest = repository.find(limit=2)
    assert [post.source_post_id for post in newest] == ["p-4", "p-3"]


def test_failed_write_does_not_update_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = JsonlPostRepository(tmp_path)

    def failing_append(records: object) -> None:
        raise StorageError("disk full")

    monkeypatch.setattr(repository._file, "append", failing_append)  # noqa: SLF001
    with pytest.raises(StorageError):
        repository.add_if_absent(make_post("x"))
    assert repository.count() == 0


def test_incident_versions_latest_wins_after_restart(tmp_path: Path) -> None:
    repository = JsonlIncidentRepository(tmp_path)
    repository.save(_incident())
    repository.save(_incident(version=2, status=IncidentStatus.RESOLVED))
    reloaded = JsonlIncidentRepository(tmp_path)
    incident = reloaded.get("inc-1")
    assert incident is not None and incident.version == 2
    assert reloaded.list_active() == []


def test_incident_version_conflict_is_rejected(tmp_path: Path) -> None:
    repository = JsonlIncidentRepository(tmp_path)
    repository.save(_incident())
    with pytest.raises(StorageError, match="version conflict"):
        repository.save(_incident(version=1))


def test_compaction_keeps_latest_snapshot_only(tmp_path: Path) -> None:
    repository = JsonlIncidentRepository(tmp_path)
    repository.save(_incident())
    repository.save(_incident(version=2))
    repository.compact()
    lines = (tmp_path / "incidents.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert JsonlIncidentRepository(tmp_path).get("inc-1").version == 2  # type: ignore[union-attr]


def test_schema_invalid_record_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "posts.jsonl").write_text('{"post_id": "only-an-id"}\n', encoding="utf-8")
    repository = JsonlPostRepository(tmp_path)
    assert repository.count() == 0
    assert repository.diagnostics() == {"posts.jsonl": 1}


def _snapshot(minutes: int, reposts: int) -> EngagementSnapshot:
    return EngagementSnapshot(
        post_id="p", observed_at=START + timedelta(minutes=minutes), like_count=1, repost_count=reposts, reply_count=0, quote_count=0
    )


def test_engagement_newest_snapshot_wins_and_survives_restart(tmp_path: Path) -> None:
    repository = JsonlEngagementRepository(tmp_path)
    assert repository.save(_snapshot(10, 5)) is True
    assert repository.save(_snapshot(5, 99)) is False  # older observation is ignored
    assert repository.save(_snapshot(10, 7)) is False  # same observation time: not newer
    assert repository.save(_snapshot(20, 9)) is True
    reloaded = JsonlEngagementRepository(tmp_path)
    current = reloaded.get("p")
    assert current is not None and current.repost_count == 9 and current.amplification == 9
