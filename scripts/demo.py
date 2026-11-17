"""Demo: start the monitor with synthetic posts and open the dashboard.

    python scripts/demo.py                       # uses ../social-data-collector when it is installed there
    python scripts/demo.py --direct              # posts the demo data straight to the API
    python scripts/demo.py --port 8010 --no-browser   # default port: the first free one from 8000

What happens:

1. A separate demo data folder (``data/demo``) is emptied and the monitor starts on it in
   synthetic mode, classifying with rules + the bundled text model (active).
2. The posts in ``examples/demo_posts.jsonl`` (all invented) are delivered: by the
   collector (``run-once`` with its synthetic source) when the collector folder is found,
   otherwise straight to ``POST /api/v1/posts/batch``. Their times are relative to now.
3. A short summary is printed and the dashboard opens in the browser. Ctrl+C stops it.

Your own ``.env`` and ``data`` are not used or changed; the API key is new on every run.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path

MONITOR_ROOT = Path(__file__).resolve().parent.parent
DEMO_FILE = MONITOR_ROOT / "examples" / "demo_posts.jsonl"
DEMO_QUERIES = MONITOR_ROOT / "examples" / "demo_queries.json"  # one broad query, so the collector takes every demo post
DEMO_DATA = MONITOR_ROOT / "data" / "demo"


def venv_python(project: Path) -> str:
    for candidate in (project / ".venv" / "Scripts" / "python.exe", project / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def find_collector(explicit: Path | None) -> Path | None:
    """The collector next to this project: a git clone (social-data-collector) or a GitHub ZIP (social-data-collector-main)."""
    candidates = [explicit] if explicit else sorted(MONITOR_ROOT.parent.glob("social-data-collector*"))
    for folder in candidates:
        if folder and (folder / "src" / "social_data_collector").is_dir() and venv_python(folder) != sys.executable:
            return folder.resolve()  # it needs its own virtual environment (its dependencies are not the monitor's)
    return None


def request_json(url: str, api_key: str | None = None, body: object | None = None) -> dict:  # type: ignore[type-arg]
    headers = {"X-API-Key": api_key} if api_key else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (local URL built by this script)
        return json.loads(response.read().decode("utf-8"))


def child_env(src: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("MONITOR_", "COLLECTOR_"))}
    env.update({"PYTHONPATH": str(src), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"})
    return env


def wait_for_health(base_url: str, process: subprocess.Popen[bytes], timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"monitor exited early with code {process.returncode}")
        try:
            if request_json(f"{base_url}/health")["status"] == "ok":
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.5)
    raise RuntimeError("monitor did not start in time")


def deliver_with_collector(collector_dir: Path, base_url: str, api_key: str) -> bool:
    workdir = DEMO_DATA / "collector"
    workdir.mkdir(parents=True, exist_ok=True)
    env = child_env(collector_dir / "src") | {
        "COLLECTOR_MONITOR_API_KEY": api_key,
        "COLLECTOR_MONITOR_BASE_URL": base_url,
        "COLLECTOR_DATA_DIR": str(workdir / "data"),
        "COLLECTOR_SOURCES": "synthetic",
        "COLLECTOR_SYNTHETIC_FILE": str(DEMO_FILE),
        "COLLECTOR_QUERIES_FILE": str(DEMO_QUERIES),
        "COLLECTOR_LOG_LEVEL": "WARNING",
    }
    completed = subprocess.run(
        [venv_python(collector_dir), "-B", "-m", "social_data_collector", "run-once"], cwd=workdir, env=env, check=False
    )
    return completed.returncode == 0


def deliver_directly(base_url: str, api_key: str) -> bool:
    now = datetime.now(UTC)
    posts = []
    for line in DEMO_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        posts.append({
            "source": "synthetic",
            "source_post_id": f"synthetic-{item['id']}",
            "author_id": item["author"],
            "author_handle": item.get("handle"),
            "author_display_name": item.get("display_name"),
            "text": item["text"],
            "created_at": (now - timedelta(minutes=float(item.get("minutes_ago", 0)))).isoformat(),
            "collected_at": now.isoformat(),
            "query_id": "demo",
            "engagement": {
                "like_count": int(item.get("likes", 0)),
                "repost_count": int(item.get("reposts", 0)),
                "reply_count": int(item.get("replies", 0)),
                "quote_count": int(item.get("quotes", 0)),
            },
        })
    body = {"schema_version": "1.1", "batch_id": f"demo-{now:%Y%m%dT%H%M%S}", "posts": posts}
    result = request_json(f"{base_url}/api/v1/posts/batch", api_key, body)
    return not result.get("rejected")


def summary(base_url: str, api_key: str) -> None:
    stats = request_json(f"{base_url}/api/v1/stats", api_key)
    analyses = request_json(f"{base_url}/api/v1/analysis?limit=500", api_key)["items"]
    incidents = request_json(f"{base_url}/api/v1/incidents", api_key)["items"]
    alerts = request_json(f"{base_url}/api/v1/alerts", api_key)["items"]
    rescued = sum(1 for a in analyses if (a.get("text_model") or {}).get("path") == "model_rescue" and a["is_complaint"])
    print()
    print("=== Demo hazır ===")
    print(f"Gönderi: {stats['total_posts']}   Şikâyet: {stats['complaints']}   "
          f"Kuralların kaçırıp modelin bulduğu şikâyet: {rescued}")
    for incident in incidents:
        print(f"Olay: {incident['severity']:<8} {incident['service_category']} / {incident['issue_category']} "
              f"({incident['unique_users_count']} kişi)")
    for alert in alerts:
        if alert["alert_type"] == "REPUTATION_SIGNAL":
            print(f"İtibar sinyali: {alert['severity']}")
    print()
    print(f"Dashboard: {base_url}/dashboard")
    print("Durdurmak için bu pencerede Ctrl+C.")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--port", type=int, default=None, help="default: the first free port from 8000")
    parser.add_argument("--collector-dir", type=Path, default=None, help="default: ../social-data-collector*")
    parser.add_argument("--direct", action="store_true", help="post straight to the API, without the collector")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--check", action="store_true", help="stop after the summary (for testing the demo)")
    args = parser.parse_args()

    collector_dir = None if args.direct else find_collector(args.collector_dir)
    if args.port is None:
        port = next((p for p in range(8000, 8020) if port_is_free(p)), None)
        if port is None:
            print("8000-8019 arasında boş port yok. --port ile başka bir port verin.", file=sys.stderr)
            return 1
    elif port_is_free(args.port):
        port = args.port
    else:
        print(f"{args.port} portu kullanılıyor (açık kalmış eski bir demo olabilir). Başka bir port deneyin.", file=sys.stderr)
        return 1
    try:
        if DEMO_DATA.exists():
            shutil.rmtree(DEMO_DATA)  # always the demo's own folder, never your data
        DEMO_DATA.mkdir(parents=True)
    except OSError as exc:
        print(f"Demo klasörü temizlenemedi ({exc}). Açık kalmış eski demo pencerelerini kapatıp tekrar deneyin.", file=sys.stderr)
        return 1

    api_key = secrets.token_urlsafe(32)
    base_url = f"http://127.0.0.1:{port}"
    env = child_env(MONITOR_ROOT / "src") | {
        "MONITOR_API_KEY": api_key,
        "MONITOR_PORT": str(port),
        "MONITOR_DATA_DIR": str(DEMO_DATA / "monitor"),
        "MONITOR_ACCEPT_SYNTHETIC": "true",
        "MONITOR_CLASSIFIER": "hybrid",
        "MONITOR_TEXT_MODEL_MODE": "active",
        "MONITOR_EVALUATION_INTERVAL_SECONDS": "10",
        "MONITOR_LOG_LEVEL": "WARNING",
    }
    print(f"Monitor başlıyor ({base_url}) ...")
    monitor = subprocess.Popen([venv_python(MONITOR_ROOT), "-B", "-m", "social_incident_monitor", "serve"], cwd=DEMO_DATA, env=env)
    try:
        wait_for_health(base_url, monitor)
        if collector_dir is not None:
            print(f"Gönderiler collector ile gönderiliyor ({collector_dir.name}) ...")
            delivered = deliver_with_collector(collector_dir, base_url, api_key)
        else:
            print("Gönderiler doğrudan API'ye gönderiliyor ...")
            delivered = deliver_directly(base_url, api_key)
        if not delivered:
            print("Gönderiler iletilemedi; yukarıdaki hataya bakın.", file=sys.stderr)
            return 1
        summary(base_url, api_key)
        if args.check:
            return 0
        if not args.no_browser:
            webbrowser.open(f"{base_url}/dashboard")
        while monitor.poll() is None:
            time.sleep(1)
        return monitor.returncode or 0
    except KeyboardInterrupt:
        print("\nDemo durduruluyor ...")
        return 0
    except (RuntimeError, urllib.error.URLError) as exc:
        print(f"Demo başlatılamadı: {exc}", file=sys.stderr)
        return 1
    finally:
        if monitor.poll() is None:
            monitor.terminate()
            try:
                monitor.wait(timeout=15)
            except subprocess.TimeoutExpired:
                monitor.kill()


if __name__ == "__main__":
    sys.exit(main())
