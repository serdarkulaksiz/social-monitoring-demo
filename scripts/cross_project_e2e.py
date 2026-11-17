"""Cross-project end-to-end check: real monitor process + real collector process.

Starts social-incident-monitor in synthetic mode on a free local port with a temporary
data directory, runs ``social_data_collector run-once`` with the synthetic source, then
verifies over HTTP that a CRITICAL service-incident alert, a separate reputation signal
and an emerging topic were produced. The projects
interact only through the REST API; neither imports the other.

Usage (each project keeps its own virtual environment):

    python scripts/cross_project_e2e.py --collector-dir ../social-data-collector

All runtime data is written to a temporary directory outside both repositories.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

MONITOR_ROOT = Path(__file__).resolve().parent.parent


def venv_python(project: Path) -> str:
    for candidate in (project / ".venv" / "Scripts" / "python.exe", project / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def get_json(url: str, api_key: str | None = None) -> dict:  # type: ignore[type-arg]
    request = urllib.request.Request(url, headers={"X-API-Key": api_key} if api_key else {})
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 (local URL built by this script)
        return json.loads(response.read().decode("utf-8"))


def wait_for_health(base_url: str, process: subprocess.Popen[bytes], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"monitor exited early with code {process.returncode}")
        try:
            if get_json(f"{base_url}/health")["status"] == "ok":
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.3)
    raise RuntimeError("monitor did not become healthy in time")


def base_env(src: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("MONITOR_", "COLLECTOR_"))}
    env.update({"PYTHONPATH": str(src), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"})
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--collector-dir", type=Path, required=True)
    args = parser.parse_args()
    collector_dir = args.collector_dir.resolve()
    synthetic_file = collector_dir / "examples" / "synthetic_posts.jsonl"
    if not synthetic_file.is_file():
        print(f"synthetic data not found: {synthetic_file}", file=sys.stderr)
        return 2

    api_key = secrets.token_urlsafe(32)
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="social-e2e-") as temp:
        workdir = Path(temp)
        monitor_env = base_env(MONITOR_ROOT / "src") | {
            "MONITOR_API_KEY": api_key,
            "MONITOR_PORT": str(port),
            "MONITOR_DATA_DIR": str(workdir / "monitor-data"),
            "MONITOR_ACCEPT_SYNTHETIC": "true",
        }
        monitor = subprocess.Popen(
            [venv_python(MONITOR_ROOT), "-B", "-m", "social_incident_monitor", "serve"], cwd=workdir, env=monitor_env
        )
        try:
            wait_for_health(base_url, monitor)
            print(f"[e2e] monitor healthy at {base_url}")
            collector_env = base_env(collector_dir / "src") | {
                "COLLECTOR_MONITOR_API_KEY": api_key,
                "COLLECTOR_MONITOR_BASE_URL": base_url,
                "COLLECTOR_DATA_DIR": str(workdir / "collector-data"),
                "COLLECTOR_SOURCES": "synthetic",
                "COLLECTOR_SYNTHETIC_FILE": str(synthetic_file),
            }
            completed = subprocess.run(
                [venv_python(collector_dir), "-B", "-m", "social_data_collector", "run-once"],
                cwd=workdir,
                env=collector_env,
                check=False,
            )
            print(f"[e2e] collector run-once exit code: {completed.returncode}")

            stats = get_json(f"{base_url}/api/v1/stats", api_key)
            alerts = get_json(f"{base_url}/api/v1/alerts", api_key)["items"]
            incidents = get_json(f"{base_url}/api/v1/incidents", api_key)["items"]
            runs = get_json(f"{base_url}/api/v1/collector-runs", api_key)["items"]
            posts = get_json(f"{base_url}/api/v1/posts?limit=500", api_key)["items"]
            topics = get_json(f"{base_url}/api/v1/topics/emerging", api_key)
            print(f"[e2e] posts={stats['total_posts']} complaints={stats['complaints']} incidents={len(incidents)} alerts={len(alerts)} runs={len(runs)}")
            for alert in alerts:
                print(f"[e2e] alert {alert['severity']} {alert['service']}/{alert['issue_category']} users={alert['unique_users_count']}")

            checks = {
                "collector exited successfully": completed.returncode == 0,
                "posts stored": stats["total_posts"] > 0,
                "complaints detected": stats["complaints"] > 0,
                "CRITICAL service alert": any(a["severity"] == "CRITICAL" and a["alert_type"] == "SERVICE_INCIDENT" for a in alerts),
                "one incident per service/issue": len({(i["service_category"], i["issue_category"]) for i in incidents}) == len(incidents),
                "collector run reported": len(runs) == 1 and runs[0]["status"] == "success",
                "collector health HEALTHY": [h["status"] for h in stats["collector_health"]] == ["HEALTHY"],
                "author handle stored unmasked": bool(posts) and all(p["author_handle"] and p["author_id"] for p in posts),
                "engagement counters stored": any(p["engagement"] and p["engagement"]["like_count"] > 0 for p in posts),
                "reputation signal raised separately": any(a["alert_type"] == "REPUTATION_SIGNAL" for a in alerts),
                "emerging topic found": any("karekod" in " ".join(t["terms"]) for t in topics),
            }
        finally:
            monitor.terminate()
            try:
                monitor.wait(timeout=15)
            except subprocess.TimeoutExpired:
                monitor.kill()

    for name, passed in checks.items():
        print(f"[e2e] {'PASS' if passed else 'FAIL'}: {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
