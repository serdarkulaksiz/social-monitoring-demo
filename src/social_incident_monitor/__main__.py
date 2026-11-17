"""Command line entry point.

    python -m social_incident_monitor serve          [--env-file PATH]
    python -m social_incident_monitor check-config   [--env-file PATH]
    python -m social_incident_monitor compact        [--env-file PATH]
    python -m social_incident_monitor verify-audit   [--env-file PATH]
    python -m social_incident_monitor export-labels  [--env-file PATH] [--out FILE]
    python -m social_incident_monitor evaluate       [--env-file PATH] --labels FILE [--rules FILE] [--sample NAME]
                                                     [--model FILE] [--mode rules|model|hybrid|all]
    python -m social_incident_monitor reanalyze      [--env-file PATH] [--since 14d]
    python -m social_incident_monitor import-dataset --file FILE.csv [--out FILE.jsonl]
    python -m social_incident_monitor split-dataset  --labels FILE [--test-share 0.3] [--out DIR]
    python -m social_incident_monitor train-model    [--env-file PATH] --train FILE --test FILE [--algorithm nb|logreg] [--out DIR]
    python -m social_incident_monitor promote-model  [--env-file PATH] --candidate FILE --test FILE [--tolerance 0.0] | --rollback

Maintenance commands (compact, verify-audit, export-labels, reanalyze) open the data
directory exclusively, so they run while the server is stopped. ``evaluate`` only reads
the given labels file and does not touch the data directory. ``import-dataset`` and
``split-dataset`` need no configuration; the text model commands are in ``model_cli``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import model_cli
from .application.errors import ConfigurationError, MonitorError
from .config import Settings
from .infrastructure.file_lock import LockUnavailableError
from .infrastructure.logging_setup import configure_logging

EXIT_CONFIG_ERROR = 2
EXIT_LOCKED = 3
EXIT_AUDIT_FAILED = 4
COMMANDS = [
    "serve", "check-config", "compact", "verify-audit", "export-labels", "evaluate", "reanalyze",
    "import-dataset", "split-dataset", "train-model", "promote-model",
]
NO_SETTINGS = {"import-dataset", "split-dataset"}
LABEL_SCHEMA_VERSION = 1  # same as classification.model.dataset.LABEL_SCHEMA_VERSION


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="social_incident_monitor", description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--env-file", type=Path, default=None, help="path to a .env file (default: ./.env if present)")
    parser.add_argument("--out", type=Path, default=None, help="export-labels: output file (default: <data_dir>/exports/)")
    parser.add_argument("--labels", type=Path, default=None, help="evaluate: labels file written by export-labels")
    parser.add_argument("--rules", type=Path, default=None, help="evaluate: rules file to test (default: configured rules)")
    parser.add_argument("--sample", default=None, help="evaluate: only labels given in this place, e.g. review_random")
    parser.add_argument("--since", default="14d", help="reanalyze: how far back, e.g. 14d or 48h (default 14d)")
    parser.add_argument("--model", type=Path, default=None, help="evaluate: text model file to measure with the rules")
    parser.add_argument("--mode", default=None, help="evaluate: rules, model, hybrid or all (default: all with --model)")
    parser.add_argument("--file", type=Path, default=None, help="import-dataset: CSV/JSON/JSONL file of labelled posts")
    parser.add_argument("--test-share", type=float, default=0.3, help="split-dataset: share of posts kept for testing")
    parser.add_argument("--train", type=Path, default=None, help="train-model: training rows (split-dataset train.jsonl)")
    parser.add_argument("--test", type=Path, default=None, help="train-model/promote-model: locked test rows")
    parser.add_argument("--algorithm", default="logreg", help="train-model: nb or logreg (default logreg)")
    parser.add_argument("--candidate", type=Path, default=None, help="promote-model: model file to put in use")
    parser.add_argument("--tolerance", type=float, default=0.0, help="promote-model: allowed drop in F1/recall")
    parser.add_argument("--rollback", action="store_true", help="promote-model: go back to the previous model")
    args = parser.parse_args(argv)

    if args.command in NO_SETTINGS:
        try:
            if args.command == "import-dataset":
                return model_cli.import_dataset(args.file, args.out)
            return model_cli.split_dataset(args.labels, args.out, args.test_share)
        except MonitorError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return EXIT_CONFIG_ERROR

    try:
        settings = Settings.from_environment(args.env_file)
    except MonitorError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    configure_logging(settings.log_level, [settings.api_key.get_secret_value()])

    try:
        if args.command == "check-config":
            return _check_config(settings)
        if args.command == "compact":
            return _compact(settings)
        if args.command == "verify-audit":
            return _verify_audit(settings)
        if args.command == "export-labels":
            return _export_labels(settings, args.out)
        if args.command == "evaluate":
            return model_cli.evaluate(settings, args.labels, args.rules, args.sample, args.model, args.mode)
        if args.command == "train-model":
            return model_cli.train_model(settings, args.train, args.test, args.algorithm, args.out)
        if args.command == "promote-model":
            return model_cli.promote_model(settings, args.candidate, args.test, args.rollback, args.tolerance)
        if args.command == "reanalyze":
            return _reanalyze(settings, _parse_since(args.since))
        return _serve(settings)
    except LockUnavailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_LOCKED
    except MonitorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR


def _check_config(settings: Settings) -> int:
    from .container import build_classifier

    build_classifier(settings)  # validates the classification rules
    print(json.dumps(settings.describe(), indent=2, ensure_ascii=False))
    print("Configuration OK.")
    return 0


def _compact(settings: Settings) -> int:
    """Rewrite versioned files (incidents, alerts) with only their latest snapshots."""
    from .container import build_container

    container = build_container(settings)
    try:
        container.incident_repository.compact()
        container.alert_repository.compact()
    finally:
        container.lock.release()
    print("Compaction finished.")
    return 0


def _verify_audit(settings: Settings) -> int:
    """Check every link of the incident audit trail (incident_events.jsonl)."""
    from .container import build_container

    container = build_container(settings)
    try:
        result = container.audit_log.verify()
    finally:
        container.lock.release()
    if result.ok:
        print(f"Audit trail OK: {result.records} entries, chain intact.")
        return 0
    print(f"Audit trail FAILED: {result.records} entries checked.", file=sys.stderr)
    for problem in result.problems:
        print(f"  - {problem}", file=sys.stderr)
    return EXIT_AUDIT_FAILED


def _export_labels(settings: Settings, out: Path | None) -> int:
    """Write the latest analyst label of every post, with the post text, as JSONL.

    The file contains real posts: keep it inside the data directory or another protected
    location and never commit it.
    """
    from .container import build_container

    container = build_container(settings)
    try:
        target = out or container.settings.data_dir.resolve() / "exports" / f"labels-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.jsonl"
        rows = []
        for label in container.feedback_repository.iter_latest():
            post = container.post_repository.get(label.post_id)
            if post is None:
                continue
            rows.append(
                {
                    "label_schema": LABEL_SCHEMA_VERSION,
                    "post_id": label.post_id,
                    "text": post.text,
                    "created_at": post.created_at.isoformat(),
                    "label": {
                        "is_bank_related": label.is_bank_related,
                        "is_complaint": label.is_complaint,
                        "service_category": label.service_category.value,
                        "issue_category": label.issue_category.value,
                        "risk_signals": list(label.risk_signals),
                    },
                    "predicted": {
                        "is_complaint": label.predicted_is_complaint,
                        "service_category": label.predicted_service.value,
                        "issue_category": label.predicted_issue.value,
                        "risk_signals": list(label.predicted_risk_signals),
                    },
                    "verdict": label.verdict,
                    "sample": label.sample,
                    "classifier_version": label.classifier_version,
                    "labelled_at": label.created_at.isoformat(),
                }
            )
    finally:
        container.lock.release()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"Error: cannot write {target}: {exc.strerror or exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    print(f"Exported {len(rows)} labelled posts to {target}")
    return 0


def _reanalyze(settings: Settings, since: timedelta) -> int:
    """Classify stored posts again with the current rules (the newest result per post wins)."""
    from .container import build_container

    container = build_container(settings)
    try:
        now = container.clock.now()
        posts = container.post_repository.find_created_between(now - since, now)
        container.analysis_service.analyze(posts, now)
    finally:
        container.lock.release()
    print(f"Reanalyzed {len(posts)} posts created in the last {since}.")
    return 0


def _parse_since(value: str) -> timedelta:
    match = re.fullmatch(r"(\d+)([dh])", value.strip())
    if not match:
        raise ConfigurationError(f"--since must look like 14d or 48h, got {value!r}")
    amount = int(match.group(1))
    return timedelta(days=amount) if match.group(2) == "d" else timedelta(hours=amount)


def _serve(settings: Settings) -> int:
    import uvicorn

    from .api.app import create_app

    app = create_app(settings)
    # JSONL storage supports exactly one writer process: never run more than one worker.
    uvicorn.run(app, host=settings.host, port=settings.port, workers=1, log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
