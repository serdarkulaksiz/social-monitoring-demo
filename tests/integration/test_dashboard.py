"""Turkish dashboard: pages, escaping, security headers and the guarded write actions."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from social_incident_monitor.api.app import create_app
from social_incident_monitor.application.errors import ConfigurationError
from social_incident_monitor.config import Settings

from conftest import START, TEST_API_KEY, FakeClock, batch_payload, post_payload

ACTION = {"X-Dashboard-Action": "1"}


@pytest.fixture
def loopback_factory(clock: FakeClock) -> Iterator[Callable[[Settings], TestClient]]:
    """Clients that connect from 127.0.0.1, like a browser on the monitor's own computer."""
    opened: list[TestClient] = []

    def factory(settings: Settings) -> TestClient:
        client = TestClient(create_app(settings, clock=clock, run_background_evaluation=False), client=("127.0.0.1", 50000))
        client.__enter__()
        opened.append(client)
        return client

    yield factory
    for client in reversed(opened):
        client.__exit__(None, None, None)


def _incident_with_hostile_post(client: TestClient) -> str:
    created = START - timedelta(minutes=2)
    posts = [post_payload("Ziraat Mobil açılmıyor İstanbul'da", author=f"did:plc:u{n}", created_at=created, handle=f"u{n}.example.test") for n in range(11)]
    hostile = post_payload('Ziraat Mobil açılmıyor <script>alert("x")</script>', author="did:plc:evil", created_at=created, handle="evil.example.test")
    hostile["author_display_name"] = '<img src=x onerror=alert(1)>'
    hostile["url"] = "javascript:alert(1)"
    client.post("/api/v1/posts/batch", json=batch_payload([*posts, hostile]), headers={"X-API-Key": TEST_API_KEY})
    return client.get("/api/v1/incidents", headers={"X-API-Key": TEST_API_KEY}).json()["items"][0]["incident_id"]


def test_every_page_renders_in_turkish_with_strict_headers(client: TestClient) -> None:
    incident_id = _incident_with_hostile_post(client)
    del client.headers["X-API-Key"]  # pages need no key
    pages = {
        "/dashboard": "Aktif hizmet olayları",
        f"/dashboard/incidents/{incident_id}": "Zaman çizelgesi",
        f"/dashboard/incidents/{incident_id}/report": "Olay raporu",
        "/dashboard/review": "İnceleme kuyruğu",
        "/dashboard/review?mode=random": "Rastgele örnek",
        "/dashboard/quality": "Sınıflandırma kalitesi",
        "/dashboard/topics": "Yükselen konular",
        "/dashboard/reputation": "HASSAS",
    }
    for path, text in pages.items():
        response = client.get(path)
        assert response.status_code == 200, path
        assert text in response.text, path
        assert '<html lang="tr">' in response.text
        csp = response.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "style-src 'self'" in csp and "unsafe-inline" not in csp
        assert response.headers["x-content-type-options"] == "nosniff" and response.headers["referrer-policy"] == "no-referrer"
        assert "<script>" not in response.text.replace('<script src="/dashboard/static/dashboard.js" defer></script>', "")
        assert ' style="' not in response.text  # no inline styles: the CSP would block them
    assert "SENTETİK VERİ MODU" in client.get("/dashboard").text
    assert "Mobil Bankacılık" in client.get("/dashboard").text
    assert client.get("/dashboard/static/dashboard.css").status_code == 200
    assert client.get("/dashboard/static/dashboard.js").headers["x-content-type-options"] == "nosniff"


def test_post_text_and_names_are_escaped_and_unsafe_links_dropped(client: TestClient) -> None:
    incident_id = _incident_with_hostile_post(client)
    page = client.get(f"/dashboard/incidents/{incident_id}").text
    assert "&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;" in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert "<img src=x" not in page and 'href="javascript:' not in page
    assert "İstanbul" in page and "u1.example.test" in page and "<svg class=\"chart\"" in page


def test_markdown_report_download(client: TestClient) -> None:
    incident_id = _incident_with_hostile_post(client)
    response = client.get(f"/dashboard/incidents/{incident_id}/report.md")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/markdown")
    assert "attachment" in response.headers["content-disposition"] and "# Olay raporu" in response.text
    assert "<script>" not in response.text  # Markdown control characters in posts are escaped as well


def test_actions_from_this_computer_with_the_action_header(
    loopback_factory: Callable[[Settings], TestClient], settings: Settings
) -> None:
    client = loopback_factory(settings)
    incident_id = _incident_with_hostile_post(client)
    url = f"/dashboard/actions/incidents/{incident_id}/acknowledge"
    assert client.post(url, json={"actor": "Ayşe"}).status_code == 403  # header missing
    assert client.post(url, json={"actor": "Ayşe"}, headers={**ACTION, "Origin": "https://evil.example"}).status_code == 403
    assert client.post(url, json={"actor": "Ayşe"}, headers={**ACTION, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    response = client.post(url, json={"actor": "Ayşe", "note": "bakıyoruz"}, headers={**ACTION, "Origin": "http://testserver"})
    assert response.status_code == 200 and response.json() == {"ok": True, "status": "ACKNOWLEDGED"}
    assert client.post(f"/dashboard/actions/incidents/{incident_id}/notes", json={"actor": "Ayşe", "text": "not"}, headers=ACTION).json()["ok"]

    evidence = client.get(f"/api/v1/incidents/{incident_id}/evidence", headers={"X-API-Key": TEST_API_KEY}).json()
    post_id = evidence["posts"][0]["post"]["post_id"]
    feedback_url = f"/dashboard/actions/posts/{post_id}/feedback"
    labelled = client.post(feedback_url, json={"verdict": "not_complaint", "actor": "Ayşe", "sample": "incident_evidence"}, headers=ACTION)
    assert labelled.status_code == 200 and labelled.json()["ok"]
    assert client.post(feedback_url, json={"verdict": "complaint", "actor": "Ayşe"}, headers=ACTION).status_code == 422
    timeline = client.get(f"/api/v1/incidents/{incident_id}/timeline", headers={"X-API-Key": TEST_API_KEY}).json()
    assert [entry["event_type"] for entry in timeline][-3:] == ["ACKNOWLEDGED", "NOTE_ADDED", "FEEDBACK_APPLIED"]


def test_actions_are_refused_from_other_computers_in_local_mode(client: TestClient) -> None:
    incident_id = _incident_with_hostile_post(client)
    response = client.post(f"/dashboard/actions/incidents/{incident_id}/acknowledge", json={"actor": "Ayşe"}, headers=ACTION)
    assert response.status_code == 403 and "this computer" in response.json()["error"]["message"]


@pytest.mark.parametrize(("mode", "key", "status"), [("api_key", None, 401), ("api_key", TEST_API_KEY, 200), ("off", TEST_API_KEY, 403)])
def test_action_modes(
    loopback_factory: Callable[[Settings], TestClient], make_settings: Callable[..., Settings], mode: str, key: str | None, status: int
) -> None:
    client = loopback_factory(make_settings(dashboard_actions=mode))
    incident_id = _incident_with_hostile_post(client)
    headers: dict[str, Any] = dict(ACTION)
    if key:
        headers["X-API-Key"] = key
    response = client.post(f"/dashboard/actions/incidents/{incident_id}/acknowledge", json={"actor": "Ayşe"}, headers=headers)
    assert response.status_code == status
    page = client.get(f"/dashboard/incidents/{incident_id}").text
    assert ('data-action="acknowledge"' in page) is (mode != "off" and status != 200)


def test_unknown_host_names_are_refused(client: TestClient) -> None:
    assert client.get("/dashboard", headers={"Host": "rebind.example"}).status_code == 400
    assert client.get("/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def test_non_loopback_bind_requires_allowed_hosts() -> None:
    with pytest.raises(ConfigurationError, match="MONITOR_ALLOWED_HOSTS"):
        Settings.from_environment(environ={"MONITOR_API_KEY": TEST_API_KEY, "MONITOR_HOST": "0.0.0.0"})
    settings = Settings.from_environment(
        environ={"MONITOR_API_KEY": TEST_API_KEY, "MONITOR_HOST": "0.0.0.0", "MONITOR_ALLOWED_HOSTS": "monitor.bank.internal, 10.0.0.5"}
    )
    assert settings.trusted_hosts == ["monitor.bank.internal", "10.0.0.5"]
    assert Settings.from_environment(environ={"MONITOR_API_KEY": TEST_API_KEY}).trusted_hosts == ["127.0.0.1", "localhost"]


def test_root_redirects_to_the_dashboard(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/dashboard"
