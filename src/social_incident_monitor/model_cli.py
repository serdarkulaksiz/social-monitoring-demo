"""Command line steps of the text model: import → split → train → evaluate → promote (or roll back).

Typical loop (see docs/MODEL.md):

    import-dataset --file posts.csv --out ../datasets/posts.labels.jsonl
    split-dataset  --labels ../datasets/posts.labels.jsonl --test-share 0.3
    train-model    --train ../datasets/split/train.jsonl --test ../datasets/split/test.jsonl --algorithm logreg
    evaluate       --labels ../datasets/split/test.jsonl --model <models>/model-logreg-….json
    promote-model  --candidate <models>/model-logreg-….json --test ../datasets/split/test.jsonl

The data files contain real posts: keep them outside the repository.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .application.errors import ConfigurationError
from .classification.hybrid import HybridSettings
from .classification.model import dataset, training
from .classification.model.text_model import ModelError, TextModel
from .classification.rule_based import RuleSet
from .config import Settings

EXIT_GATE_FAILED = 5
METHOD_NAMES = {"rules": "Kurallar", "model": "Model", "hybrid": "Kural + model"}


def import_dataset(source: Path | None, out: Path | None) -> int:
    if source is None:
        raise ConfigurationError("import-dataset needs --file FILE (.csv, .json or .jsonl)")
    target = out or source.with_name(source.stem + ".labels.jsonl")
    report = _dataset_call(dataset.import_file, source)
    dataset.write_rows(target, report.rows)
    complaints = sum(1 for row in report.rows if row["label"]["is_complaint"])
    print(f"Imported {len(report.rows)} labelled posts ({complaints} complaints) to {target}")
    if report.unlabelled or report.duplicates:
        print(f"  skipped: {report.unlabelled} without labels, {report.duplicates} duplicate texts")
    return 0


def split_dataset(labels: Path | None, out: Path | None, test_share: float) -> int:
    if labels is None:
        raise ConfigurationError("split-dataset needs --labels FILE")
    rows = _dataset_call(dataset.read_rows, labels)
    train, test = _dataset_call(dataset.split, rows, test_share)
    folder = out or labels.parent / "split"
    train_hash = dataset.write_rows(folder / "train.jsonl", train)
    test_hash = dataset.write_rows(folder / "test.jsonl", test)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "source": str(labels),
        "test_share": test_share,
        "train": {"rows": len(train), "sha256": train_hash},
        "test": {"rows": len(test), "sha256": test_hash},
    }
    (folder / "split.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Split {len(rows)} posts: {len(train)} train, {len(test)} test (locked; never train on it) in {folder}")
    return 0


def train_model(settings: Settings, train_path: Path | None, test_path: Path | None, algorithm: str, out: Path | None) -> int:
    if train_path is None or test_path is None:
        raise ConfigurationError("train-model needs --train FILE and --test FILE")
    if algorithm not in ("nb", "logreg"):
        raise ConfigurationError("--algorithm must be nb or logreg")
    train_rows = _dataset_call(dataset.read_rows, train_path)
    test_rows = _dataset_call(dataset.read_rows, test_path)
    shared = dataset.overlap(train_rows, test_rows)
    if shared:
        raise ConfigurationError(f"{shared} training posts are also in the test set; split again or remove them")
    rule_set = RuleSet.load(settings.classification_rules_path)
    try:
        model = training.train(
            train_rows, rule_set, algorithm,  # type: ignore[arg-type]
            metadata={"train_sha256": dataset.file_hash(train_path), "test_sha256": dataset.file_hash(test_path)},
        )
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    folder = out or settings.active_text_model_path.parent
    target = folder / f"model-{model.version}.json"
    model.save(target)
    stored = TextModel.load(target)  # measure exactly what was written (weights are rounded on save)
    scores = [training.evaluate(test_rows, rule_set, method, stored, _hybrid(settings)) for method in ("rules", "model", "hybrid")]
    stored.metadata["test_metrics"] = {score.method: score.to_dict() for score in scores}
    stored.save(target)
    print(f"Trained {algorithm} model {model.version} on {len(train_rows)} posts → {target}")
    _print_scores(scores, len(test_rows))
    return 0


def evaluate(settings: Settings, labels: Path | None, rules: Path | None, sample: str | None, model_path: Path | None, mode: str | None) -> int:
    if labels is None:
        raise ConfigurationError("evaluate needs --labels FILE (written by export-labels or import-dataset)")
    rule_set = RuleSet.load(rules or settings.classification_rules_path)
    rows = _dataset_call(dataset.read_rows, labels)
    if sample is not None:
        rows = [row for row in rows if row.get("sample", "other") == sample]
    model = _load_model(model_path) if model_path else None
    methods = _methods(mode or ("all" if model else "rules"))
    if model is None and methods != ["rules"]:
        raise ConfigurationError("--mode model/hybrid/all needs --model FILE")
    scope = f" (sample: {sample})" if sample else ""
    scores = [training.evaluate(rows, rule_set, method, model, _hybrid(settings)) for method in methods]
    if methods == ["rules"]:
        score = scores[0]
        matrix = score.complaint
        print(f"Rules {rule_set.version} on {score.rows} labelled posts{scope}")
        print(f"  complaints: TP={matrix.true_positive} FP={matrix.false_positive} FN={matrix.false_negative} TN={matrix.true_negative}")
        print(f"  precision={_ratio(matrix.precision)} recall={_ratio(matrix.recall)} f1={_ratio(matrix.f1)} accuracy={_ratio(matrix.accuracy)}")
        print(f"  category accuracy (service and issue) among caught complaints: {_ratio(score.category_accuracy)}")
        return 0
    print(f"Rules {rule_set.version}, model {model.version if model else '-'} on {len(rows)} labelled posts{scope}")
    _print_scores(scores, len(rows))
    return 0


def promote_model(settings: Settings, candidate: Path | None, test_path: Path | None, rollback: bool, tolerance: float) -> int:
    active = settings.active_text_model_path
    current = settings.resolved_text_model_path  # the promoted model, else the bundled one
    folder = active.parent
    archive = folder / "archive"
    history = folder / "history.jsonl"
    if rollback:
        return _rollback(active, archive, history, fallback="the bundled model" if current != active else "rules only")
    if candidate is None or test_path is None:
        raise ConfigurationError("promote-model needs --candidate FILE and --test FILE (or --rollback)")
    new_model = _load_model(candidate)
    test_rows = _dataset_call(dataset.read_rows, test_path)
    expected = new_model.metadata.get("test_sha256")
    if expected and expected != dataset.file_hash(test_path):
        print("Warning: this is not the test set the candidate was measured on during training.", file=sys.stderr)
    rule_set = RuleSet.load(settings.classification_rules_path)
    current_model = _load_model(current) if current.exists() else None
    hybrid = HybridSettings(mode="active", rescue_p=settings.text_model_rescue_p, veto_p=settings.text_model_veto_p)
    new_scores = training.evaluate(test_rows, rule_set, "hybrid", new_model, hybrid)
    current_scores = (
        training.evaluate(test_rows, rule_set, "hybrid", current_model, hybrid)
        if current_model
        else training.evaluate(test_rows, rule_set, "rules")
    )
    passed, reason = training.gate(new_scores, current_scores, tolerance)
    against = f"model {current_model.version}" if current_model else "rules only"
    if not passed:
        print(f"Not promoted: {reason} (compared with {against}).")
        return EXIT_GATE_FAILED
    archive.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate, archive / f"model-{new_model.version}.json")
    if current_model is not None:
        shutil.copy2(current, archive / f"model-{current_model.version}.json")
    tmp = active.with_suffix(".json.tmp")
    shutil.copy2(candidate, tmp)
    tmp.replace(active)
    _append(history, {
        "at": datetime.now(UTC).isoformat(), "action": "promote", "version": new_model.version,
        "previous": current_model.version if current_model else None, "reason": reason,
    })
    print(f"Promoted {new_model.version} to {active} ({reason}; compared with {against}).")
    print("Restart the monitor to use it. Run reanalyze to classify recent posts again.")
    return 0


def _rollback(active: Path, archive: Path, history: Path, fallback: str) -> int:
    entries = _history(history)
    last = next((entry for entry in reversed(entries) if entry.get("action") == "promote"), None)
    if last is None:
        print("Nothing to roll back: no promotion recorded.")
        return EXIT_GATE_FAILED
    previous = last.get("previous")
    if previous:
        source = archive / f"model-{previous}.json"
        _load_model(source)
        tmp = active.with_suffix(".json.tmp")
        shutil.copy2(source, tmp)
        tmp.replace(active)
    elif active.exists():
        active.unlink()
    _append(history, {"at": datetime.now(UTC).isoformat(), "action": "rollback", "version": previous, "undone": last.get("version")})
    print(f"Rolled back {last.get('version')}; now using {previous or fallback}. Restart the monitor.")
    return 0


def _history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # a rollback undoes the promotion before it: drop undone pairs
    result: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("action") == "rollback" and result:
            result.pop()
        else:
            result.append(entry)
    return result


def _append(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _methods(mode: str) -> list[str]:
    if mode == "all":
        return ["rules", "model", "hybrid"]
    if mode in ("rules", "model", "hybrid"):
        return [mode]
    raise ConfigurationError("--mode must be rules, model, hybrid or all")


def _hybrid(settings: Settings) -> HybridSettings:
    return HybridSettings(mode="active", rescue_p=settings.text_model_rescue_p, veto_p=settings.text_model_veto_p)


def _load_model(path: Path) -> TextModel:
    try:
        return TextModel.load(path)
    except ModelError as exc:
        raise ConfigurationError(str(exc)) from exc


def _dataset_call(function: Any, *args: Any) -> Any:
    try:
        return function(*args)
    except dataset.DatasetError as exc:
        raise ConfigurationError(str(exc)) from exc


def _print_scores(scores: list[training.Scores], rows: int) -> None:
    print(f"  {'':<15}{'şikâyet P':>10}{'R':>7}{'F1':>7}{'yanlış alarm':>14}{'kaçan':>7}{'banka F1':>10}{'kategori':>10}")
    for score in scores:
        matrix = score.complaint
        print(
            f"  {METHOD_NAMES[score.method]:<15}{_ratio(matrix.precision):>10}{_ratio(matrix.recall):>7}{_ratio(matrix.f1):>7}"
            f"{matrix.false_positive:>14}{matrix.false_negative:>7}{_ratio(score.bank.f1):>10}{_ratio(score.category_accuracy):>10}"
        )
    samples = sorted({name for score in scores for name in score.by_sample})
    if len(samples) > 1:
        print("  complaint F1 by source:")
        for name in samples:
            cells = "  ".join(f"{METHOD_NAMES[s.method]}={_ratio(s.by_sample[name].f1)}" for s in scores if name in s.by_sample)
            size = scores[0].by_sample[name].total if name in scores[0].by_sample else 0
            print(f"    {name} ({size}): {cells}")
    print(f"  ({rows} posts; P = precision, R = recall; 'yanlış alarm' = false positives, 'kaçan' = missed complaints)")


def _ratio(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"
