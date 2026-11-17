from __future__ import annotations

import json
from pathlib import Path

import pytest

from social_incident_monitor.classification.model import dataset
from social_incident_monitor.classification.model.dataset import DatasetError

from conftest import label_rows

CSV = (
    "Metin;Tarih;Kaynak;Banka İlgili;Şikayet;Servis;Sorun;Not\n"
    "Ziraat mobil açılmıyor;14.11.2026 09:30;sorgu1;evet;evet;mobil;erişim;\n"
    "Ziraat hesabımdan haberim olmadan para çekilmiş;2026-11-14 10:00;genis;Evet;Evet;Belirsiz;Dolandırıcılık;önemli\n"
    "Ziraat şubesi çok hızlıydı;;genis;evet;hayır;Müşteri Hizmetleri;;\n"
    "Ziraat Türkiye Kupası maçı;;karisan;hayır;;;;\n"
    "Etiketsiz bir gönderi;;sorgu2;;;;;\n"
    "Ziraat mobil açılmıyor;;sorgu1;evet;evet;mobil;erişim;\n"
)


def write(tmp_path: Path, content: str, name: str = "posts.csv") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_csv_with_turkish_headers_and_values(tmp_path: Path) -> None:
    report = dataset.import_file(write(tmp_path, CSV))
    assert len(report.rows) == 4 and report.unlabelled == 1 and report.duplicates == 1
    first, fraud, branch, sports = report.rows
    assert first["label"] == {"is_bank_related": True, "is_complaint": True, "service_category": "mobile_banking",
                              "issue_category": "application_unavailable", "risk_signals": []}
    assert first["created_at"] == "2026-11-14T09:30:00+00:00" and first["sample"] == "sorgu1"
    assert first["post_id"] == dataset.post_id_for("Ziraat mobil açılmıyor") and first["label_schema"] == 1
    assert fraud["label"]["issue_category"] == "fraud_unauthorized" and fraud["note"] == "önemli"
    assert branch["label"]["service_category"] == "customer_service" and branch["label"]["issue_category"] == "none"
    assert sports["label"]["is_bank_related"] is False and sports["sample"] == "karisan"


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("Ziraat mobil;;;evet;evet;mobil;;", "needs both 'servis' and 'sorun'"),
        ("Ziraat mobil;;;evet;belki;mobil;erişim;", "evet or hayır"),
        ("Ziraat mobil;;;evet;evet;uzay;erişim;", "unknown servis"),
        ("Ziraat mobil;;;hayır;evet;mobil;erişim;", "must be about the bank"),
        ("Ziraat mobil;;;evet;;mobil;;", "'sikayet' is empty"),
        (";;;evet;evet;mobil;erişim;", "'metin' is empty"),
        ("Ziraat mobil;dün;;evet;hayır;;;", "cannot read date"),
    ],
)
def test_bad_rows_are_reported_with_their_row_number(tmp_path: Path, line: str, message: str) -> None:
    path = write(tmp_path, "metin;tarih;kaynak;banka_ilgili;sikayet;servis;sorun;not\n" + line + "\n")
    with pytest.raises(DatasetError, match=message) as error:
        dataset.import_file(path)
    assert "row 2" in str(error.value)


def test_json_and_export_rows_are_accepted(tmp_path: Path) -> None:
    records = [{"metin": "Ziraat ATM kartımı yuttu", "banka_ilgili": "evet", "sikayet": "evet", "servis": "atm", "sorun": "atm"}]
    assert dataset.import_file(write(tmp_path, json.dumps(records), "posts.json")).rows[0]["label"]["issue_category"] == "atm_failure"
    exported = label_rows(1)[:2]
    path = write(tmp_path, "".join(json.dumps(row) + "\n" for row in exported), "labels.jsonl")
    assert [row["post_id"] for row in dataset.import_file(path).rows] == ["row-0", "row-1"]


def test_split_is_deterministic_stratified_and_disjoint(tmp_path: Path) -> None:
    rows = label_rows(10)
    train, test = dataset.split(rows, 0.3)
    again_train, again_test = dataset.split(list(reversed(rows)), 0.3)
    assert [row["post_id"] for row in test] == [row["post_id"] for row in again_test]
    assert len(train) + len(test) == len(rows) and dataset.overlap(train, test) == 0
    complaints = sum(1 for row in rows if row["label"]["is_complaint"])
    assert sum(1 for row in test if row["label"]["is_complaint"]) == round(complaints * 0.3)
    assert {row["sample"] for row in test} == {"sorgu1", "genis", "karisan"}
    assert dataset.overlap(train, test + train[:2]) == 2
    with pytest.raises(DatasetError):
        dataset.split(rows, 1.5)


def test_rows_round_trip_with_checksum(tmp_path: Path) -> None:
    rows = label_rows(1)
    path = tmp_path / "rows.jsonl"
    checksum = dataset.write_rows(path, rows)
    assert dataset.file_hash(path) == checksum and dataset.read_rows(path)[0]["post_id"] == "row-0"
    path.write_text('{"post_id": "x"}\n', encoding="utf-8")
    with pytest.raises(DatasetError, match="line 1"):
        dataset.read_rows(path)
