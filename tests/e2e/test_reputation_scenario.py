"""Reputation signal end to end: rumours spread, the signal escalates with reach, and stays separate."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from conftest import START, FakeClock, batch_payload, post_payload


def _engagement(client: TestClient, clock: FakeClock, source_post_id: str, reposts: int) -> dict:  # type: ignore[type-arg]
    clock.advance(seconds=10)
    batch = {
        "schema_version": "1.1",
        "batch_id": f"e-{source_post_id}-{reposts}",
        "updates": [
            {
                "source": "synthetic",
                "source_post_id": source_post_id,
                "observed_at": clock.now().isoformat(),
                "engagement": {"like_count": reposts * 2, "repost_count": reposts, "reply_count": 0, "quote_count": 0},
            }
        ],
    }
    return client.post("/api/v1/posts/engagement", json=batch).json()


def test_reputation_signal_escalates_with_reach_and_is_kept_apart(client: TestClient, clock: FakeClock) -> None:
    created = START - timedelta(minutes=5)
    rumours = [
        post_payload("Ziraat Bankası batıyor mu? Herkes konuşuyor", author="did:plc:r1", created_at=created, source_post_id="synthetic-r1", handle="r1.example.test", reposts=0),
        post_payload("Duydum ki Ziraat batacak", author="did:plc:r2", created_at=created, source_post_id="synthetic-r2", handle="r2.example.test", reposts=0),
    ]
    client.post("/api/v1/posts/batch", json=batch_payload(rumours))
    assert client.get("/api/v1/incidents").json()["count"] == 0  # two people, little reach: below WARNING

    assert _engagement(client, clock, "synthetic-r1", 60)["applied"] == 1  # the post starts spreading
    [incident] = client.get("/api/v1/incidents").json()["items"]
    assert (incident["service_category"], incident["issue_category"], incident["severity"]) == ("reputation", "solvency_rumor", "WARNING")
    assert incident["detection_rule"] == "reputation" and incident["detection_details"]["amplification"] == 60
    [alert] = client.get("/api/v1/alerts").json()["items"]
    assert alert["alert_type"] == "REPUTATION_SIGNAL"
    assert client.get("/api/v1/alerts", params={"alert_type": "SERVICE_INCIDENT"}).json()["count"] == 0

    _engagement(client, clock, "synthetic-r2", 550)  # reach crosses the CRITICAL limit
    escalated = client.get(f"/api/v1/incidents/{incident['incident_id']}").json()
    assert escalated["severity"] == "CRITICAL" and escalated["detection_details"]["amplification"] == 610
    assert client.get("/api/v1/alerts", params={"alert_type": "REPUTATION_SIGNAL"}).json()["count"] == 2

    evidence = client.get(f"/api/v1/incidents/{incident['incident_id']}/evidence").json()
    assert evidence["unique_users"] == 2
    assert {item["post"]["author_handle"] for item in evidence["posts"]} == {"r1.example.test", "r2.example.test"}

    # an analyst decides the widely shared post is not a rumour: the signal falls back
    widest = next(item["post"]["post_id"] for item in evidence["posts"] if item["post"]["author_handle"] == "r2.example.test")
    response = client.post(f"/api/v1/analysis/{widest}/feedback", json={"verdict": "confirm", "risk_signals": [], "actor": "Risk Ekibi"})
    assert response.status_code == 201
    lowered = client.get(f"/api/v1/incidents/{incident['incident_id']}").json()
    assert lowered["current_level"] == "WARNING" and lowered["detection_details"]["amplification"] == 60
    bad = client.post(f"/api/v1/analysis/{widest}/feedback", json={"verdict": "confirm", "risk_signals": ["panic"], "actor": "x"})
    assert bad.status_code == 422
