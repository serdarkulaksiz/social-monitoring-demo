"""REST API → application → JSONL storage, through FastAPI's TestClient."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from social_incident_monitor.config import Settings
from social_incident_monitor.infrastructure.file_lock import LockUnavailableError

from conftest import START as START_TIME
from conftest import TEST_API_KEY, FakeClock, batch_payload, post_payload

CONTRACT_EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "posts-batch.v1.json"


def test_health_needs_no_key(client: TestClient) -> None:
    del client.headers["X-API-Key"]
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0", "contract_version": "1.1"}


@pytest.mark.parametrize("path", ["/api/v1/posts", "/api/v1/analysis", "/api/v1/incidents", "/api/v1/alerts", "/api/v1/stats", "/api/v1/collector-runs"])
@pytest.mark.parametrize("key", [None, "wrong-key-wrong-key-wrong-key"])
def test_api_requires_valid_key(client: TestClient, path: str, key: str | None) -> None:
    headers = {"X-API-Key": key} if key else {}
    del client.headers["X-API-Key"]
    response = client.get(path, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert TEST_API_KEY not in response.text


def test_contract_example_batch_is_accepted_and_stored(client: TestClient, data_dir: Path) -> None:
    payload = json.loads(CONTRACT_EXAMPLE.read_text(encoding="utf-8"))
    response = client.post("/api/v1/posts/batch", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert (body["received"], body["accepted"], body["duplicates"], body["rejected"]) == (2, 2, 0, [])
    lines = (data_dir / "posts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert len((data_dir / "analysis.jsonl").read_text(encoding="utf-8").splitlines()) == 2

    resent = client.post("/api/v1/posts/batch", json=payload).json()
    assert (resent["accepted"], resent["duplicates"]) == (0, 2)
    assert len((data_dir / "posts.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_invalid_items_are_rejected_individually(client: TestClient) -> None:
    good = post_payload("Ziraat Mobil açılmıyor")
    naive_time = post_payload("Ziraat ATM paramı vermedi")
    naive_time["created_at"] = "2026-01-01T09:00:00"
    extra_field = post_payload("x")
    extra_field["avatar"] = "https://example.invalid/a.jpg"
    response = client.post("/api/v1/posts/batch", json=batch_payload([good, naive_time, extra_field, {"garbage": True}]))
    body = response.json()
    assert response.status_code == 200
    assert body["accepted"] == 1
    assert [item["index"] for item in body["rejected"]] == [1, 2, 3]
    assert "created_at" in body["rejected"][0]["reason"]


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": "2.0", "batch_id": "b", "posts": [{}]},
        {"schema_version": "1.0", "batch_id": "b", "posts": []},
        {"schema_version": "1.0", "posts": [{}]},
        {"schema_version": "1.0", "batch_id": "b", "posts": [{}] * 501},
    ],
)
def test_invalid_envelope_returns_422(client: TestClient, payload: dict) -> None:  # type: ignore[type-arg]
    response = client.post("/api/v1/posts/batch", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_malformed_json_returns_422(client: TestClient) -> None:
    response = client.post("/api/v1/posts/batch", content=b"{not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 422


def test_list_endpoints_and_filters(client: TestClient) -> None:
    client.post(
        "/api/v1/posts/batch",
        json=batch_payload([post_payload("Ziraat ATM paramı vermedi"), post_payload("Ziraat Bankası faiz oranları nedir?")]),
    )
    assert client.get("/api/v1/posts").json()["count"] == 2
    complaints = client.get("/api/v1/analysis", params={"is_complaint": "true"}).json()
    assert complaints["count"] == 1 and complaints["items"][0]["service_category"] == "atm"
    assert client.get("/api/v1/analysis", params={"service": "atm"}).json()["count"] == 1
    assert client.get("/api/v1/posts", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/analysis", params={"service": "spaceship"}).status_code == 422
    stats = client.get("/api/v1/stats").json()
    assert (stats["total_posts"], stats["complaints"], stats["synthetic_mode"]) == (2, 1, True)
    assert [window["window_minutes"] for window in stats["windows"]] == [5, 15, 30]


def test_incident_endpoints_errors(client: TestClient) -> None:
    assert client.get("/api/v1/incidents/missing").status_code == 404
    response = client.post("/api/v1/incidents/missing/acknowledge")
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"


def test_collector_run_reports_are_idempotent(client: TestClient) -> None:
    report = json.loads((CONTRACT_EXAMPLE.parent / "collector-run.v1.json").read_text(encoding="utf-8"))
    first = client.post("/api/v1/collector-runs", json=report)
    assert first.status_code == 201 and first.json() == {"run_id": report["run_id"], "stored": True}
    second = client.post("/api/v1/collector-runs", json=report)
    assert second.status_code == 200 and second.json()["stored"] is False
    runs = client.get("/api/v1/collector-runs").json()
    assert runs["count"] == 1 and runs["items"][0]["status"] == "partial"
    bad = dict(report, status="exploded")
    assert client.post("/api/v1/collector-runs", json=bad).status_code == 422


def test_storage_failure_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def disk_full(self: object, *args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "open", disk_full)
    response = client.post("/api/v1/posts/batch", json=batch_payload([post_payload("Ziraat ATM paramı vermedi")]))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"


def test_second_instance_on_same_data_dir_is_refused(
    client: TestClient, client_factory: Callable[[Settings], TestClient], settings: Settings
) -> None:
    with pytest.raises(LockUnavailableError):
        client_factory(settings)


def test_dashboard_can_be_disabled(client_factory: Callable[[Settings], TestClient], make_settings: Callable[..., Settings]) -> None:
    client = client_factory(make_settings(dashboard_enabled=False))
    assert client.get("/dashboard").status_code == 404


def test_author_fields_are_stored_and_listed_unmasked(client: TestClient) -> None:
    payload = post_payload("Ziraat Mobil açılmıyor", author="did:plc:demo0000000000000000001")
    payload.update(
        {
            "schema_version": "1.1",
            "author_handle": "demo-user.example.test",
            "author_display_name": "Demo Kullanıcı",
            "engagement": {"like_count": 2, "repost_count": 1, "reply_count": 0, "quote_count": 0},
            "query_id": "ziraat_mobil",
        }
    )
    assert client.post("/api/v1/posts/batch", json=batch_payload([payload])).json()["accepted"] == 1
    [listed] = client.get("/api/v1/posts").json()["items"]
    assert listed["author_id"] == "did:plc:demo0000000000000000001"
    assert listed["author_handle"] == "demo-user.example.test"
    assert listed["author_display_name"] == "Demo Kullanıcı"
    assert listed["engagement"]["repost_count"] == 1
    assert listed["query_id"] == "ziraat_mobil"


def test_post_without_schema_version_takes_the_batch_version(client: TestClient, data_dir: Path) -> None:
    unversioned = post_payload("Ziraat Mobil açılmıyor")
    del unversioned["schema_version"]
    queued = post_payload("Ziraat ATM kartımı yuttu")  # an older post still queued, written with its own version
    queued["schema_version"] = "1.0"
    batch = batch_payload([unversioned, queued]) | {"schema_version": "1.1"}
    assert client.post("/api/v1/posts/batch", json=batch).json()["accepted"] == 2
    stored = [json.loads(line) for line in (data_dir / "posts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {row["source_post_id"]: row["schema_version"] for row in stored} == {
        unversioned["source_post_id"]: "1.1",
        queued["source_post_id"]: "1.0",
    }


def test_engagement_updates_are_applied_idempotently(client: TestClient, clock: FakeClock) -> None:
    stored = post_payload("Ziraat ATM paramı vermedi")
    client.post("/api/v1/posts/batch", json=batch_payload([stored]))

    def update(source_post_id: str, minutes: int, reposts: int) -> dict:  # type: ignore[type-arg]
        return {
            "source": "synthetic",
            "source_post_id": source_post_id,
            "observed_at": (clock.now() + timedelta(minutes=minutes)).isoformat(),
            "engagement": {"like_count": 10, "repost_count": reposts, "reply_count": 1, "quote_count": 0},
        }

    clock.advance(minutes=30)
    batch = {
        "schema_version": "1.1",
        "batch_id": "e-1",
        "updates": [update(stored["source_post_id"], 0, 25), update("synthetic-not-stored", 0, 3), {"source": "synthetic"}],
    }
    body = client.post("/api/v1/posts/engagement", json=batch).json()
    assert (body["received"], body["applied"], body["stale"], body["unknown"]) == (3, 1, 0, 1)
    assert [item["index"] for item in body["rejected"]] == [2]
    again = client.post("/api/v1/posts/engagement", json=batch).json()
    assert (again["applied"], again["stale"]) == (0, 1)
    future = client.post(
        "/api/v1/posts/engagement",
        json={"schema_version": "1.1", "batch_id": "e-2", "updates": [update(stored["source_post_id"], 60, 99)]},
    ).json()
    assert future["applied"] == 0 and "future" in future["rejected"][0]["reason"]
    [listed] = client.get("/api/v1/posts").json()["items"]
    assert listed["engagement"]["repost_count"] == 25


def test_engagement_envelope_requires_contract_1_1(client: TestClient) -> None:
    response = client.post("/api/v1/posts/engagement", json={"schema_version": "1.0", "batch_id": "e", "updates": [{}]})
    assert response.status_code == 422


def test_topics_and_locations_endpoints(client: TestClient) -> None:
    created = START_TIME - timedelta(minutes=5)
    posts = [
        post_payload("Ziraat karekod ile para çekme hata veriyor İstanbul'da", author=f"did:plc:k{n}", created_at=created)
        for n in range(4)
    ] + [post_payload("Ziraat ATM Ankara'da paramı vermedi", author="did:plc:a1", created_at=created)]
    client.post("/api/v1/posts/batch", json=batch_payload(posts))
    [topic] = client.get("/api/v1/topics/emerging").json()
    assert "karekod" in " ".join(topic["terms"]) and topic["unique_users"] == 4 and len(topic["examples"]) == 4
    assert " " in topic["label"].split(", ")[0]  # a word pair leads the label
    complaints = client.get("/api/v1/locations").json()
    assert [(item["key"], item["name"], item["unique_users"]) for item in complaints][:1] in ([("istanbul", "İstanbul", 4)], [("ankara", "Ankara", 1)])
    every_post = {item["key"]: item["unique_users"] for item in client.get("/api/v1/locations", params={"complaints_only": "false"}).json()}
    assert every_post == {"istanbul": 4, "ankara": 1}
