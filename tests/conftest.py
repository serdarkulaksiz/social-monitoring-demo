"""Shared test fixtures. All runtime data lives in pytest's tmp_path (outside the repository)."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from social_incident_monitor.api.app import create_app
from social_incident_monitor.config import Settings
from social_incident_monitor.domain.models import Post, make_post_id

TEST_API_KEY = "test-api-key-0123456789abcdef"
START = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, start: datetime = START) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> datetime:
        self.current += timedelta(**delta)
        return self.current


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def make_settings(data_dir: Path) -> Callable[..., Settings]:
    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "api_key": TEST_API_KEY,
            "data_dir": data_dir,
            "accept_synthetic": True,
            "bundled_text_model": False,  # tests choose their model explicitly
            "allowed_hosts": ["testserver", "127.0.0.1", "localhost"],  # TestClient sends Host: testserver
        }
        values.update(overrides)
        return Settings.model_validate(values)

    return factory


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings()


@pytest.fixture
def client_factory(clock: FakeClock) -> Iterator[Callable[[Settings], TestClient]]:
    """Builds started TestClients (lifespan running); all are closed at teardown."""
    opened: list[TestClient] = []

    def factory(settings: Settings) -> TestClient:
        client = TestClient(create_app(settings, clock=clock, run_background_evaluation=False))
        client.__enter__()
        client.headers["X-API-Key"] = TEST_API_KEY
        opened.append(client)
        return client

    yield factory
    for client in reversed(opened):
        client.__exit__(None, None, None)


@pytest.fixture
def client(client_factory: Callable[[Settings], TestClient], settings: Settings) -> TestClient:
    return client_factory(settings)


_counter = itertools.count(1)


def post_payload(
    text: str,
    *,
    author: str | None = None,
    created_at: datetime | None = None,
    source: str = "synthetic",
    source_post_id: str | None = None,
    handle: str | None = None,
    reposts: int | None = None,
) -> dict[str, Any]:
    number = next(_counter)
    created = created_at or START
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "source": source,
        "source_post_id": source_post_id or f"synthetic-{number:06d}",
        "author_id": author or f"author-{number:08d}",
        "text": text,
        "created_at": created.isoformat(),
        "collected_at": created.isoformat(),
        "url": None,
    }
    if handle is not None or reposts is not None:
        payload["schema_version"] = "1.1"
        payload["author_handle"] = handle
        if reposts is not None:
            payload["engagement"] = {"like_count": 0, "repost_count": reposts, "reply_count": 0, "quote_count": 0}
    return payload


def batch_payload(posts: list[dict[str, Any]], batch_id: str | None = None) -> dict[str, Any]:
    return {"schema_version": "1.0", "batch_id": batch_id or f"batch-{next(_counter)}", "posts": posts}


def make_post(text: str, *, author: str = "author-00000001", created_at: datetime = START, number: int = 1) -> Post:
    source_post_id = f"p-{number}"
    return Post(
        post_id=make_post_id("synthetic", source_post_id),
        source="synthetic",
        source_post_id=source_post_id,
        author_id=author,
        text=text,
        created_at=created_at,
        collected_at=created_at,
        ingested_at=created_at,
    )


# --- synthetic labelled posts for the text model (never real posts) ------------------

_COMPLAINTS = [
    ("Ziraat mobil yine acilmiyo yaa", "mobile_banking", "application_unavailable"),
    ("Ziraat uygulaması tarihi eser, geliştirin artık", "mobile_banking", "other"),
    ("Ziraat belanızı versin hesap numaramı bulamadım", "mobile_banking", "other"),
    ("Ziraat mobil dönüp duruyor girişte takıldım", "mobile_banking", "login_failure"),
    ("Ziraat havalem saatlerdir ortada yok", "fast_eft_transfer", "transaction_delay"),
]
_NOT_COMPLAINTS = [
    ("Ziraat mobil çok hızlandı teşekkürler", "mobile_banking"),
    ("Ziraat şubesine gittim işlemim hızlı bitti", "customer_service"),
    ("Ziraat Bankası kredi faizleri ne kadar", "unknown"),
    ("Ziraat uygulamasında artık sorun yok", "mobile_banking"),
]
_NOT_BANK = ["Ziraat Türkiye Kupası maçı bu akşam", "Ziraat fakültesi tercih ettim", "Ziraat mühendisi olarak tarım yapıyorum"]
_FILLERS = ["", " bugün", " yine", " 👏", " !!", " valla", " ya"]


def label_rows(copies: int = 12) -> list[dict[str, Any]]:
    """Label rows (export-labels format) with small wording changes so each text is unique."""
    rows: list[dict[str, Any]] = []

    def add(text: str, bank: bool, complaint: bool, service: str, issue: str, sample: str) -> None:
        rows.append({
            "label_schema": 1,
            "post_id": f"row-{len(rows)}",
            "text": text,
            "label": {"is_bank_related": bank, "is_complaint": complaint, "service_category": service,
                      "issue_category": issue, "risk_signals": []},
            "sample": sample,
        })

    for copy in range(copies):
        filler = _FILLERS[copy % len(_FILLERS)] + f" {copy}"
        for text, service, issue in _COMPLAINTS:
            add(text + filler, True, True, service, issue, "sorgu1")
        for text, service in _NOT_COMPLAINTS:
            add(text + filler, True, False, service, "none", "genis")
        for text in _NOT_BANK:
            add(text + filler, False, False, "unknown", "none", "karisan")
    return rows
