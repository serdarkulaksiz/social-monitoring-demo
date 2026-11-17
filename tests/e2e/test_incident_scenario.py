"""End-to-end scenario over the public HTTP API with synthetic data.

Synthetic posts (as a collector would send them) → storage → classification →
rising complaint density → incident → CRITICAL alert → verified via API.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import social_incident_monitor.__main__ as cli
from social_incident_monitor.config import Settings

from conftest import START, TEST_API_KEY, FakeClock, batch_payload, post_payload

NOISE = [
    "Ziraat Türkiye Kupası maçı başladı.",
    "Ziraat mühendisleri tarım projesi geliştirdi.",
    "Ziraat Bankası faiz oranları nedir?",
    "Ziraat Bankası voleybol takımı kazandı.",
]


def complaints(count: int, *, first_user: int = 0, minutes_ago: float = 1, text: str = "Ziraat Mobil açılmıyor") -> list[dict]:  # type: ignore[type-arg]
    created = START - timedelta(minutes=minutes_ago)
    return [
        post_payload(text, author=f"did:plc:user{first_user + n:05d}", created_at=created, handle=f"user{first_user + n:05d}.example.test")
        for n in range(count)
    ]


def test_full_scenario_warning_then_critical(client: TestClient, clock: FakeClock, data_dir: Path) -> None:
    # 1-4: noise only → stored and classified, nothing raised
    response = client.post("/api/v1/posts/batch", json=batch_payload([post_payload(text) for text in NOISE]))
    assert response.json()["accepted"] == 4
    assert client.get("/api/v1/alerts").json()["count"] == 0
    assert client.get("/api/v1/incidents").json()["count"] == 0

    # 5: density rises to WARNING (12 distinct users) in mobile banking
    body = client.post("/api/v1/posts/batch", json=batch_payload(complaints(12))).json()
    assert body["incidents_opened"] == 1 and body["alerts_created"] == 1
    [incident] = client.get("/api/v1/incidents").json()["items"]
    assert (incident["severity"], incident["status"]) == ("WARNING", "OPEN")
    assert (incident["service_category"], incident["issue_category"]) == ("mobile_banking", "application_unavailable")

    # duplicates and repeats by the same users do not change anything
    body = client.post("/api/v1/posts/batch", json=batch_payload(complaints(5))).json()
    assert body["alerts_created"] == 0

    # 6-7: escalation to CRITICAL (30 distinct users) updates the same incident and raises a CRITICAL alert
    clock.advance(minutes=1)
    body = client.post("/api/v1/posts/batch", json=batch_payload(complaints(18, first_user=12))).json()
    assert body["incidents_opened"] == 0 and body["alerts_created"] == 1
    incidents = client.get("/api/v1/incidents").json()["items"]
    assert len(incidents) == 1 and incidents[0]["incident_id"] == incident["incident_id"]
    assert incidents[0]["severity"] == "CRITICAL" and incidents[0]["unique_users_count"] == 30

    # 8: alert verified through the API
    critical = client.get("/api/v1/alerts", params={"status": "ACTIVE"}).json()["items"][0]
    assert critical["severity"] == "CRITICAL"
    assert critical["incident_id"] == incident["incident_id"]
    assert critical["alert_type"] == "SERVICE_INCIDENT"
    assert critical["unique_users_count"] == 30
    assert "CRITICAL threshold" in critical["detection_reason"]
    assert client.get("/api/v1/alerts").json()["count"] == 2

    stats = client.get("/api/v1/stats").json()
    assert stats["open_incidents"] == 1 and stats["complaints"] == 35
    assert "Mobil Bankacılık" in client.get("/dashboard").text

    # files written as JSONL
    assert sum(1 for _ in (data_dir / "alerts.jsonl").open(encoding="utf-8")) == 2


def test_restart_keeps_state_and_does_not_duplicate_incident(
    client: TestClient, client_factory: Callable[[Settings], TestClient], settings: Settings, clock: FakeClock
) -> None:
    payload = batch_payload(complaints(26))
    client.post("/api/v1/posts/batch", json=payload)
    assert client.get("/api/v1/alerts").json()["count"] == 1
    client.__exit__(None, None, None)  # stop the first instance (releases the lock)

    clock.advance(minutes=1)
    restarted = client_factory(settings)
    incidents = restarted.get("/api/v1/incidents").json()["items"]
    assert len(incidents) == 1 and incidents[0]["severity"] == "CRITICAL"
    # the collector re-sends the same batch after the restart: no duplicate posts, incidents or alerts
    body = restarted.post("/api/v1/posts/batch", json=payload).json()
    assert (body["accepted"], body["duplicates"]) == (0, 26)
    assert restarted.get("/api/v1/stats").json()["total_posts"] == 26
    assert restarted.get("/api/v1/incidents").json()["count"] == 1
    assert restarted.get("/api/v1/alerts").json()["count"] == 1


def test_no_complaints_no_alerts(client: TestClient) -> None:
    payload = [post_payload(text, author=f"user-{n}") for n in range(30) for text in NOISE[:1]]
    client.post("/api/v1/posts/batch", json=batch_payload(payload))
    assert client.get("/api/v1/incidents").json()["count"] == 0
    assert client.get("/api/v1/alerts").json()["count"] == 0


def test_incident_resolves_when_complaints_stop(client: TestClient, clock: FakeClock) -> None:
    client.post("/api/v1/posts/batch", json=batch_payload(complaints(12)))
    clock.advance(minutes=31)
    client.post("/api/v1/posts/batch", json=batch_payload([post_payload(NOISE[0], created_at=clock.now())]))
    [incident] = client.get("/api/v1/incidents").json()["items"]
    assert incident["status"] == "RESOLVED" and incident["resolved_at"] is not None
    assert client.get("/api/v1/alerts").json()["items"][0]["status"] == "RESOLVED"


def test_acknowledge_then_resolve_via_api(client: TestClient) -> None:
    client.post("/api/v1/posts/batch", json=batch_payload(complaints(12)))
    incident_id = client.get("/api/v1/incidents").json()["items"][0]["incident_id"]
    assert client.post(f"/api/v1/incidents/{incident_id}/acknowledge").json()["status"] == "ACKNOWLEDGED"
    assert client.post(f"/api/v1/incidents/{incident_id}/acknowledge").status_code == 409
    assert client.post(f"/api/v1/incidents/{incident_id}/resolve").json()["status"] == "RESOLVED"
    assert client.get("/api/v1/alerts").json()["items"][0]["status"] == "RESOLVED"


def test_collector_outage_raises_health_alert_not_incident(client: TestClient, clock: FakeClock) -> None:
    run = {
        "schema_version": "1.0", "run_id": "run-1", "source": "bluesky", "collector_version": "0.1.0",
        "started_at": START.isoformat(), "finished_at": START.isoformat(), "status": "success", "queries": [],
        "fetched_count": 0, "new_count": 0, "sent_count": 0, "pending_count": 0, "error_message": None,
    }
    assert client.post("/api/v1/collector-runs", json=run).status_code == 201
    health = client.get("/api/v1/stats").json()["collector_health"][0]
    assert health["status"] == "HEALTHY"  # zero posts is not a failure

    # three failing runs (source unreachable) → collector_failing alert
    for number in range(2, 5):
        clock.advance(minutes=1)
        failed = dict(run, run_id=f"run-{number}", status="failed", error_message="ConnectError: source unreachable")
        client.post("/api/v1/collector-runs", json=failed)
    alerts = client.get("/api/v1/alerts", params={"alert_type": "COLLECTOR_HEALTH"}).json()["items"]
    assert [alert["issue_category"] for alert in alerts] == ["collector_failing"]

    # collector stops reporting entirely → stale (the periodic evaluation runs on the next request)
    clock.advance(minutes=20)
    client.post("/api/v1/posts/batch", json=batch_payload([post_payload(NOISE[0], created_at=clock.now())]))
    active = client.get("/api/v1/alerts", params={"alert_type": "COLLECTOR_HEALTH", "status": "ACTIVE"}).json()["items"]
    assert [alert["issue_category"] for alert in active] == ["collector_stale"]
    assert client.get("/api/v1/stats").json()["collector_health"][0]["status"] == "STALE"
    assert client.get("/api/v1/incidents").json()["count"] == 0  # never a banking incident

    # recovery resolves health alerts
    client.post("/api/v1/collector-runs", json=dict(run, run_id="run-9", finished_at=clock.now().isoformat()))
    assert client.get("/api/v1/alerts", params={"alert_type": "COLLECTOR_HEALTH", "status": "ACTIVE"}).json()["count"] == 0


def test_audit_trail_timeline_evidence_report_and_metrics(
    client: TestClient, clock: FakeClock, settings: Settings, monkeypatch: pytest.MonkeyPatch, data_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client.post("/api/v1/posts/batch", json=batch_payload(complaints(12)))
    incident_id = client.get("/api/v1/incidents").json()["items"][0]["incident_id"]
    clock.advance(minutes=1)
    client.post("/api/v1/posts/batch", json=batch_payload(complaints(18, first_user=12)))
    clock.advance(minutes=4)
    acknowledged = client.post(f"/api/v1/incidents/{incident_id}/acknowledge", json={"actor": " Ayşe  Yılmaz ", "note": "ekip bakıyor"})
    assert acknowledged.json()["acknowledged_by"] == "Ayşe Yılmaz"
    note = client.post(f"/api/v1/incidents/{incident_id}/notes", json={"actor": "Ayşe Yılmaz", "text": "Mobil ekip bilgilendirildi"})
    assert note.status_code == 201 and note.json()["event_type"] == "NOTE_ADDED"
    clock.advance(minutes=20)
    resolved = client.post(f"/api/v1/incidents/{incident_id}/resolve", json={"actor": "Mehmet", "note": "sunucu yeniden başlatıldı"}).json()
    assert (resolved["resolved_by"], resolved["resolution_note"]) == ("Mehmet", "sunucu yeniden başlatıldı")

    timeline = client.get(f"/api/v1/incidents/{incident_id}/timeline").json()
    assert [entry["event_type"] for entry in timeline] == ["OPENED", "ESCALATED", "ACKNOWLEDGED", "NOTE_ADDED", "RESOLVED"]
    assert [entry["actor"] for entry in timeline] == ["system", "system", "Ayşe Yılmaz", "Ayşe Yılmaz", "Mehmet"]
    assert timeline[1]["previous_hash"] == timeline[0]["record_hash"]

    evidence = client.get(f"/api/v1/incidents/{incident_id}/evidence", params={"limit": 5}).json()
    assert (evidence["total_posts"], evidence["unique_users"], len(evidence["posts"])) == (30, 30, 5)
    assert evidence["posts"][0]["post"]["author_handle"].endswith(".example.test")
    assert evidence["posts"][0]["analysis"]["is_complaint"] is True

    report = client.get(f"/api/v1/incidents/{incident_id}/report").json()
    assert report["audit_trail_verified"] is True and report["disclaimer"]
    assert report["timings"] == {
        "time_to_detect_seconds": 60.0,  # complaints were posted one minute before the incident opened
        "time_to_acknowledge_seconds": 300.0,
        "time_to_resolve_seconds": 1500.0,
    }
    markdown = client.get(f"/api/v1/incidents/{incident_id}/report", params={"format": "markdown"})
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert "attachment" in markdown.headers["content-disposition"]
    assert "# Olay raporu: Mobil Bankacılık" in markdown.text and "Mehmet" in markdown.text
    assert "regülatöre bildirilip" in markdown.text and "doğrulandı" in markdown.text

    metrics = client.get("/api/v1/incidents/metrics", params={"days": 7}).json()
    assert (metrics["incidents"], metrics["acknowledged"], metrics["resolved"]) == (1, 1, 1)
    assert metrics["median_time_to_resolve_seconds"] == 1500.0

    assert client.post(f"/api/v1/incidents/{incident_id}/notes", json={"actor": "", "text": "x"}).status_code == 422
    assert client.post("/api/v1/incidents/missing/notes", json={"actor": "Ayşe", "text": "x"}).status_code == 404

    # verify-audit runs on the stopped instance: intact chain → 0, edited entry → 4
    client.__exit__(None, None, None)
    monkeypatch.chdir(data_dir.parent)
    monkeypatch.setenv("MONITOR_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("MONITOR_DATA_DIR", str(data_dir))
    assert cli.main(["verify-audit"]) == 0
    assert "chain intact" in capsys.readouterr().out
    path = data_dir / "incident_events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[2])
    tampered["actor"] = "Başka biri"
    lines[2] = json.dumps(tampered, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert cli.main(["verify-audit"]) == cli.EXIT_AUDIT_FAILED


def test_baseline_opens_incident_at_low_volume(client: TestClient) -> None:
    # two weeks of quiet history: one ATM complaint per day
    history = [
        post_payload("Ziraat ATM paramı vermedi", author=f"did:plc:past{day:03d}", created_at=START - timedelta(days=day, hours=3))
        for day in range(1, 15)
    ]
    client.post("/api/v1/posts/batch", json=batch_payload(history))
    assert client.get("/api/v1/incidents").json()["count"] == 0
    assert client.get("/api/v1/stats").json()["baseline"]["ready"] is True

    # five different people within three minutes: far below the fixed threshold of 10, far above normal
    burst = [
        post_payload("Ziraat ATM paramı vermedi", author=f"did:plc:now{n:03d}", created_at=START - timedelta(minutes=3) + timedelta(seconds=30 * n))
        for n in range(5)
    ]
    body = client.post("/api/v1/posts/batch", json=batch_payload(burst)).json()
    assert body["incidents_opened"] == 1
    [incident] = client.get("/api/v1/incidents").json()["items"]
    assert (incident["severity"], incident["detection_rule"]) == ("WARNING", "baseline")
    assert incident["detection_details"]["velocity"] is True and incident["detection_details"]["unique_users"] == 5
    assert "expected at this hour" in incident["detection_reason"]
