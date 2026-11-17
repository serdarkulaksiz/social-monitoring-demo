from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from social_incident_monitor.application.ingestion_service import IncomingPost, IngestionService
from social_incident_monitor.infrastructure.jsonl.repositories import JsonlPostRepository

from conftest import START


def incoming(index: int, source: str = "synthetic", post_id: str | None = None, minutes: int = 0) -> IncomingPost:
    created = START + timedelta(minutes=minutes)
    return IncomingPost(index, source, post_id or f"p-{index}", "author-0001", "Ziraat ATM paramı vermedi", created, created, None, "1.0")


def test_duplicates_within_and_across_batches(tmp_path: Path) -> None:
    service = IngestionService(JsonlPostRepository(tmp_path), accept_synthetic=True, max_clock_skew=timedelta(minutes=5))
    first = service.ingest("b1", [incoming(0, post_id="a"), incoming(1, post_id="a"), incoming(2, post_id="b")], START)
    assert (len(first.accepted), first.duplicates) == (2, 1)
    second = service.ingest("b2", [incoming(0, post_id="a")], START)
    assert (len(second.accepted), second.duplicates) == (0, 1)


def test_same_id_on_different_sources_is_not_a_duplicate(tmp_path: Path) -> None:
    service = IngestionService(JsonlPostRepository(tmp_path), accept_synthetic=False, max_clock_skew=timedelta(minutes=5))
    outcome = service.ingest("b", [incoming(0, source="bluesky", post_id="x"), incoming(1, source="other", post_id="x")], START)
    assert len(outcome.accepted) == 2


def test_live_and_synthetic_data_are_never_mixed(tmp_path: Path) -> None:
    live = IngestionService(JsonlPostRepository(tmp_path / "live"), accept_synthetic=False, max_clock_skew=timedelta(0))
    rejected = live.ingest("b", [incoming(0, source="synthetic")], START).rejected
    assert rejected and "synthetic" in rejected[0].reason
    demo = IngestionService(JsonlPostRepository(tmp_path / "demo"), accept_synthetic=True, max_clock_skew=timedelta(0))
    rejected = demo.ingest("b", [incoming(0, source="bluesky")], START).rejected
    assert rejected and "synthetic mode" in rejected[0].reason


def test_future_posts_are_rejected(tmp_path: Path) -> None:
    service = IngestionService(JsonlPostRepository(tmp_path), accept_synthetic=True, max_clock_skew=timedelta(minutes=5))
    outcome = service.ingest("b", [incoming(0, minutes=4), incoming(1, minutes=6)], START)
    assert len(outcome.accepted) == 1
    assert [r.index for r in outcome.rejected] == [1]
