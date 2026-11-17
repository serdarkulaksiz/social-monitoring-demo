"""Analyst feedback end to end: a false alarm is corrected and the incident follows the labels."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import social_incident_monitor.__main__ as cli

from conftest import START, TEST_API_KEY, FakeClock, batch_payload, post_payload


def _post_ids(client: TestClient) -> list[str]:
    return [item["post_id"] for item in client.get("/api/v1/posts", params={"limit": 500}).json()["items"]]


def test_marking_false_alarms_lowers_counts_and_is_audited(client: TestClient, clock: FakeClock) -> None:
    created = START - timedelta(minutes=1)
    posts = [post_payload("Ziraat Mobil açılmıyor", author=f"did:plc:u{n:04d}", created_at=created) for n in range(12)]
    client.post("/api/v1/posts/batch", json=batch_payload(posts))
    [incident] = client.get("/api/v1/incidents").json()["items"]
    assert incident["current_level"] == "WARNING" and incident["unique_users_count"] == 12

    for post_id in _post_ids(client)[:3]:
        response = client.post(f"/api/v1/analysis/{post_id}/feedback", json={"verdict": "not_complaint", "actor": "Ayşe", "note": "şaka"})
        assert response.status_code == 201 and response.json()["predicted_is_complaint"] is True

    updated = client.get(f"/api/v1/incidents/{incident['incident_id']}").json()
    assert updated["unique_users_count"] == 9 and updated["current_level"] == "NORMAL"  # below the WARNING threshold of 10
    timeline = [entry["event_type"] for entry in client.get(f"/api/v1/incidents/{incident['incident_id']}/timeline").json()]
    assert timeline == ["OPENED", "FEEDBACK_APPLIED", "FEEDBACK_APPLIED", "FEEDBACK_APPLIED", "LEVEL_CHANGED"]
    assert client.get("/api/v1/stats").json()["complaints"] == 9
    assert len(client.get("/api/v1/analysis", params={"is_complaint": "true"}).json()["items"]) == 9

    quality = client.get("/api/v1/quality").json()
    assert quality["labelled"] == 3 and quality["complaint"]["false_positive"] == 3 and quality["complaint"]["precision"] == 0.0
    assert [item["verdict"] for item in client.get("/api/v1/feedback").json()["items"]] == ["not_complaint"] * 3

    clock.advance(minutes=31)
    client.post("/api/v1/posts/batch", json=batch_payload([post_payload("Bugün hava güzel", created_at=clock.now())]))
    assert client.get(f"/api/v1/incidents/{incident['incident_id']}").json()["status"] == "RESOLVED"


def test_feedback_validation(client: TestClient) -> None:
    client.post("/api/v1/posts/batch", json=batch_payload([post_payload("Ziraat ATM paramı vermedi")]))
    [post_id] = _post_ids(client)
    assert client.post(f"/api/v1/analysis/{post_id}/feedback", json={"verdict": "maybe", "actor": "A"}).status_code == 422
    assert client.post(f"/api/v1/analysis/{post_id}/feedback", json={"verdict": "confirm"}).status_code == 422  # actor required
    assert client.post("/api/v1/analysis/missing/feedback", json={"verdict": "confirm", "actor": "A"}).status_code == 404
    corrected = client.post(
        f"/api/v1/analysis/{post_id}/feedback",
        json={"verdict": "complaint", "service_category": "card", "issue_category": "card_failure", "actor": "Ayşe"},
    ).json()
    assert (corrected["service_category"], corrected["issue_category"]) == ("card", "card_failure")


def test_review_queue_modes(client: TestClient) -> None:
    texts = ["Ziraat ATM paramı vermedi", "Ziraat Bankası faiz oranları nedir?", "Ziraat uygulaması sorun çıkarıyor mu?", "Kahve içtim"]
    client.post("/api/v1/posts/batch", json=batch_payload([post_payload(text, created_at=START - timedelta(minutes=1)) for text in texts]))
    random_items = client.get("/api/v1/review/queue", params={"mode": "random"}).json()
    assert len(random_items) == 3  # only posts that mention the bank
    assert all(item["reason"] == "rastgele örnek" for item in random_items)
    uncertain = client.get("/api/v1/review/queue", params={"mode": "uncertain"}).json()
    assert uncertain and all(item["reason"] for item in uncertain)
    assert client.get("/api/v1/review/queue", params={"mode": "incidents"}).json() == []
    first = random_items[0]["post"]["post_id"]
    client.post(f"/api/v1/analysis/{first}/feedback", json={"verdict": "confirm", "actor": "Ayşe"})
    assert first not in {item["post"]["post_id"] for item in client.get("/api/v1/review/queue", params={"mode": "random"}).json()}


def test_export_evaluate_and_reanalyze_commands(
    client: TestClient, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    texts = ["Ziraat ATM paramı vermedi", "Ziraat Mobil açılmıyor", "Ziraat Bankası faiz oranları nedir?"]
    client.post("/api/v1/posts/batch", json=batch_payload([post_payload(text) for text in texts]))
    ids = _post_ids(client)
    client.post(f"/api/v1/analysis/{ids[0]}/feedback", json={"verdict": "confirm", "actor": "Ayşe"})
    client.post(f"/api/v1/analysis/{ids[1]}/feedback", json={"verdict": "not_complaint", "actor": "Ayşe"})
    client.__exit__(None, None, None)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MONITOR_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("MONITOR_DATA_DIR", str(data_dir))
    export = tmp_path / "labels.jsonl"
    assert cli.main(["export-labels", "--out", str(export)]) == 0
    rows = [json.loads(line) for line in export.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and {row["verdict"] for row in rows} == {"confirm", "not_complaint"}
    assert all(row["label_schema"] == 1 and row["sample"] == "other" for row in rows)
    assert all(row["text"] and "actor" not in row for row in rows)

    capsys.readouterr()
    assert cli.main(["evaluate", "--labels", str(export)]) == 0
    output = capsys.readouterr().out
    assert "on 2 labelled posts" in output and "precision=" in output
    assert cli.main(["evaluate", "--labels", str(export), "--sample", "review_random"]) == 0
    assert "on 0 labelled posts (sample: review_random)" in capsys.readouterr().out

    assert cli.main(["reanalyze", "--since", "3650d"]) == 0  # the command uses the real clock; test posts are dated 2026-01-01
    assert "Reanalyzed 3 posts" in capsys.readouterr().out
    assert cli.main(["reanalyze", "--since", "yesterday"]) == cli.EXIT_CONFIG_ERROR


def test_complaint_label_without_categories_is_rejected_and_samples_are_reported(client: TestClient) -> None:
    client.post("/api/v1/posts/batch", json=batch_payload([post_payload("Ziraat ATM paramı vermedi"), post_payload("Ziraat Mobil açılmıyor")]))
    first, second = _post_ids(client)
    missing = client.post(f"/api/v1/analysis/{first}/feedback", json={"verdict": "complaint", "service_category": "atm", "actor": "Ayşe"})
    assert missing.status_code == 422
    client.post(f"/api/v1/analysis/{first}/feedback", json={"verdict": "confirm", "actor": "Ayşe", "sample": "review_random"})
    client.post(f"/api/v1/analysis/{second}/feedback", json={"verdict": "not_complaint", "actor": "Ayşe", "sample": "incident_evidence"})
    everything = client.get("/api/v1/quality").json()
    assert everything["labelled"] == 2 and everything["samples"] == {"review_random": 1, "incident_evidence": 1}
    unbiased = client.get("/api/v1/quality", params={"sample": "review_random"}).json()
    assert unbiased["labelled"] == 1 and unbiased["sample"] == "review_random"
    assert client.post(f"/api/v1/analysis/{first}/feedback", json={"verdict": "confirm", "actor": "A", "sample": "somewhere"}).status_code == 422
