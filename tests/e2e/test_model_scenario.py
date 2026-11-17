"""The whole text-model loop from the command line, then the server using the model."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from social_incident_monitor import __main__ as cli
from social_incident_monitor.config import Settings
from social_incident_monitor.container import build_classifier

from conftest import TEST_API_KEY, batch_payload, label_rows, post_payload

HEADER = ["metin", "kaynak", "banka_ilgili", "sikayet", "servis", "sorun"]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data_dir: Path) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MONITOR_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("MONITOR_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MONITOR_BUNDLED_TEXT_MODEL", "false")  # compare with rules only, not the shipped model
    return tmp_path


def _csv(path: Path) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:  # like a spreadsheet export: quoted where needed
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for row in label_rows(10):
            label = row["label"]
            writer.writerow([
                row["text"], row["sample"], "evet" if label["is_bank_related"] else "hayır",
                "evet" if label["is_complaint"] else "hayır", label["service_category"], label["issue_category"],
            ])
    return path


def test_import_split_train_evaluate_promote_and_rollback(env: Path, data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = env / "datasets"
    folder.mkdir()
    source = _csv(folder / "posts.csv")
    assert cli.main(["import-dataset", "--file", str(source)]) == 0
    labels = folder / "posts.labels.jsonl"
    assert "Imported 120 labelled posts (50 complaints)" in capsys.readouterr().out

    assert cli.main(["split-dataset", "--labels", str(labels), "--test-share", "0.3"]) == 0
    train, test = folder / "split" / "train.jsonl", folder / "split" / "test.jsonl"
    manifest = json.loads((folder / "split" / "split.json").read_text(encoding="utf-8"))
    assert manifest["train"]["rows"] + manifest["test"]["rows"] == 120

    assert cli.main(["train-model", "--train", str(train), "--test", str(train)]) == cli.EXIT_CONFIG_ERROR  # overlap
    capsys.readouterr()
    assert cli.main(["train-model", "--train", str(train), "--test", str(test), "--algorithm", "logreg"]) == 0
    output = capsys.readouterr().out
    assert "Kurallar" in output and "Kural + model" in output
    [candidate] = (data_dir / "models").glob("model-logreg-*.json")
    stored = json.loads(candidate.read_text(encoding="utf-8"))
    assert set(stored["metadata"]["test_metrics"]) == {"rules", "model", "hybrid"}

    assert cli.main(["evaluate", "--labels", str(test), "--model", str(candidate)]) == 0
    assert "complaint F1 by source" in capsys.readouterr().out

    assert cli.main(["promote-model", "--candidate", str(candidate), "--test", str(test)]) == 0
    active = data_dir / "models" / "active.json"
    assert active.read_bytes() == candidate.read_bytes()
    assert "compared with rules only" in capsys.readouterr().out

    # a model that learnt nothing useful (every post labelled "not a complaint" except a few) is refused
    weak_rows = [dict(row, label=dict(row["label"], is_complaint=index % 7 == 0)) for index, row in enumerate(json.loads(line) for line in train.read_text(encoding="utf-8").splitlines())]
    weak_train = folder / "weak.jsonl"
    weak_train.write_text("".join(json.dumps(row) + "\n" for row in weak_rows), encoding="utf-8")
    assert cli.main(["train-model", "--train", str(weak_train), "--test", str(test), "--algorithm", "nb", "--out", str(folder / "weak")]) == 0
    [weak] = (folder / "weak").glob("model-nb-*.json")
    assert cli.main(["promote-model", "--candidate", str(weak), "--test", str(test)]) == 5
    assert active.read_bytes() == candidate.read_bytes()

    assert cli.main(["promote-model", "--rollback"]) == 0
    assert not active.exists()  # back to rules only
    assert cli.main(["promote-model", "--rollback"]) == 5  # nothing left to undo


def test_server_classifies_with_the_model_in_shadow_mode(
    env: Path, data_dir: Path, make_settings: Callable[..., Settings], client_factory: Callable[[Settings], TestClient]
) -> None:
    from social_incident_monitor.classification.model import training
    from social_incident_monitor.classification.rule_based import RuleSet

    training.train(label_rows(10), RuleSet.load(), "logreg").save(data_dir / "models" / "active.json")
    settings = make_settings(classifier="hybrid", text_model_mode="shadow")
    assert build_classifier(settings).model is not None  # type: ignore[attr-defined]
    client = client_factory(settings)
    text = "Ziraat belanızı versin, tarihi eser uygulamanızı geliştirin artık"
    client.post("/api/v1/posts/batch", json=batch_payload([post_payload(text)]))
    [post] = client.get("/api/v1/posts").json()["items"]
    [analysis] = client.get("/api/v1/analysis").json()["items"]
    assert analysis["is_complaint"] is False  # shadow: the rules' answer stands
    assert analysis["text_model"]["path"] == "model_rescue" and analysis["text_model"]["mode"] == "shadow"
    queue = client.get("/api/v1/review/queue", params={"mode": "disagreement"}).json()
    assert [item["post"]["post_id"] for item in queue] == [post["post_id"]] and "model şikâyet diyor" in queue[0]["reason"]
    page = client.get("/dashboard/review", params={"mode": "disagreement"})
    assert page.status_code == 200 and "model (gölge)" in page.text and "model farklı karar verirdi" in page.text
    quality = client.get("/dashboard/quality")
    assert "Kuralların kaçırdığı, modelin şikâyet bulduğu" in quality.text


def test_missing_or_damaged_model_falls_back_to_rules(data_dir: Path, make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(classifier="hybrid")
    assert build_classifier(settings).model is None  # type: ignore[attr-defined]
    path = data_dir / "models" / "active.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ broken", encoding="utf-8")
    assert build_classifier(settings).model is None  # type: ignore[attr-defined]


def test_bundled_model_is_used_until_one_is_promoted(data_dir: Path, make_settings: Callable[..., Settings]) -> None:
    from social_incident_monitor.config import BUNDLED_TEXT_MODEL

    settings = make_settings(classifier="hybrid", bundled_text_model=True)
    assert settings.text_model_mode == "active"
    assert settings.resolved_text_model_path == BUNDLED_TEXT_MODEL
    model = build_classifier(settings).model  # type: ignore[attr-defined]
    assert model is not None and model.algorithm == "logreg"  # the shipped file loads and its hash checks out
    active = data_dir / "models" / "active.json"
    active.parent.mkdir(parents=True)
    active.write_text("{}", encoding="utf-8")
    assert settings.resolved_text_model_path == active  # a promoted model wins
    assert make_settings(classifier="hybrid", bundled_text_model=True, text_model_path=data_dir / "x.json").resolved_text_model_path == data_dir / "x.json"
