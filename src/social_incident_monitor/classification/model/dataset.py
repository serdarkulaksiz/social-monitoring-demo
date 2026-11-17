"""Labelled datasets: importing a hand-made CSV/JSON file and splitting it into train/test.

Every labelled post, wherever it comes from, ends up in one row format: the one
``export-labels`` writes (``label_schema`` 1). So the model is trained the same way from
an imported spreadsheet as from analysts' dashboard labels.

Import columns (header names are matched without case or Turkish letters):

==============  =====================================================  ===========
column          meaning                                                required
==============  =====================================================  ===========
metin           post text                                              yes
tarih           when it was posted (``2026-11-14 09:30``, ``14.11.2026 09:30``)  no
kaynak          where it was found (``sorgu1`` … ``sorgu4``, ``genis``, ``karisan``)  no
banka_ilgili    evet / hayır                                           for a label
sikayet         evet / hayır                                           if banka_ilgili = evet
servis          mobil, internet, fast, atm, kart, pos, ödeme, müşteri hizmetleri, tüm kanallar, diğer, belirsiz
sorun           giriş, transfer, erişim, ödeme, gecikme, atm, kart, ulaşılamıyor,
                dolandırıcılık, diğer, yok
not             free note                                              no
id              your own identifier                                    no
==============  =====================================================  ===========

A complaint needs ``servis`` and ``sorun``. Rows without any label are counted and
skipped. Rows with the same text (after folding) are kept once.

Real posts stay outside the repositories: write imports and splits into a folder of
their own (``social-monitoring-workspace/datasets``), never into the project.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...domain.enums import REPUTATION_ISSUES, IssueCategory, ServiceCategory
from ..text_normalizer import fold

LABEL_SCHEMA_VERSION = 1


class DatasetError(Exception):
    """The file cannot be used; the message names the row."""


_COLUMNS = {
    "text": ("metin", "text", "tweet", "icerik", "gonderi"),
    "created_at": ("tarih", "created_at", "date", "zaman"),
    "sample": ("kaynak", "source", "sample", "sorgu"),
    "is_bank_related": ("banka_ilgili", "bankayla_ilgili", "banka ilgili", "is_bank_related", "banka"),
    "is_complaint": ("sikayet", "is_complaint", "sikayet_mi"),
    "service": ("servis", "service", "service_category", "hizmet"),
    "issue": ("sorun", "issue", "issue_category", "problem"),
    "note": ("not", "note", "aciklama"),
    "id": ("id", "post_id", "kimlik"),
}
_TRUE = {"evet", "e", "1", "true", "yes", "x", "var", "dogru"}
_FALSE = {"hayir", "h", "0", "false", "no", "yok", "yanlis"}
_SERVICES = {
    ServiceCategory.MOBILE_BANKING: ("mobil", "mobil bankacilik", "uygulama", "ziraat mobil"),
    ServiceCategory.INTERNET_BANKING: ("internet", "internet bankaciligi", "internet subesi", "web"),
    ServiceCategory.TRANSFER: ("fast", "eft", "havale", "transfer", "fast / eft / havale", "fast/eft/havale", "para transferi"),
    ServiceCategory.ATM: ("atm", "bankamatik"),
    ServiceCategory.CARD: ("kart", "kredi karti", "banka karti", "kredi / banka karti", "bankkart"),
    ServiceCategory.PAYMENTS: ("odeme", "odemeler", "fatura"),
    ServiceCategory.CUSTOMER_SERVICE: ("musteri hizmetleri", "cagri merkezi", "musteri hizmeti", "sube"),
    ServiceCategory.ALL_CHANNELS: ("tum kanallar", "genel kesinti", "genel", "tum kanallar / genel kesinti", "hepsi"),
    ServiceCategory.POS: ("pos", "uye isyeri", "pos / uye isyeri"),
    ServiceCategory.OTHER: ("diger",),
    ServiceCategory.UNKNOWN: ("belirsiz", "bilinmiyor", ""),
    ServiceCategory.REPUTATION: ("itibar", "itibar / mevduat riski"),
}
_ISSUES = {
    IssueCategory.NONE: ("yok", ""),
    IssueCategory.LOGIN_FAILURE: ("giris", "giris yapilamiyor", "giris sorunu", "sifre"),
    IssueCategory.TRANSFER_FAILURE: ("transfer", "transfer basarisiz", "para transferi basarisiz", "havale"),
    IssueCategory.APPLICATION_UNAVAILABLE: ("erisim", "erisilemiyor", "uygulama erisilemiyor", "uygulama / hizmet erisilemiyor", "cokme", "kesinti"),
    IssueCategory.PAYMENT_FAILURE: ("odeme", "odeme basarisiz"),
    IssueCategory.TRANSACTION_DELAY: ("gecikme", "islem gecikmesi"),
    IssueCategory.ATM_FAILURE: ("atm", "atm arizasi"),
    IssueCategory.CARD_FAILURE: ("kart", "kart sorunu"),
    IssueCategory.CUSTOMER_SERVICE_UNREACHABLE: ("ulasilamiyor", "musteri hizmetlerine ulasilamiyor", "ulasamiyorum"),
    IssueCategory.FRAUD_UNAUTHORIZED: ("dolandiricilik", "yetkisiz islem", "dolandiricilik / yetkisiz islem", "fraud"),
    IssueCategory.OTHER: ("diger",),
    IssueCategory.SOLVENCY_RUMOR: ("batma", "batma / iflas soylentisi", "iflas"),
    IssueCategory.DEPOSIT_FLIGHT: ("para cekme / hesap kapatma", "hesap kapatma", "baska bankaya gecis"),
    IssueCategory.RUN_CALL: ("para cekme cagrisi",),
}
_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
                 "%d/%m/%Y %H:%M", "%d/%m/%Y")


def _lookup(table: Mapping[Any, tuple[str, ...]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for member, aliases in table.items():
        result[member.value] = member
        for alias in aliases:
            result[fold(alias)] = member
    return result


_SERVICE_LOOKUP = _lookup(_SERVICES)
_ISSUE_LOOKUP = _lookup(_ISSUES)


@dataclass
class ImportReport:
    rows: list[dict[str, Any]] = field(default_factory=list)
    unlabelled: int = 0
    duplicates: int = 0


def post_id_for(text: str) -> str:
    return "ds-" + hashlib.sha256(fold(text).encode("utf-8")).hexdigest()[:16]


def import_file(path: Path) -> ImportReport:
    """Read a CSV / JSON / JSONL file of posts and labels into label rows."""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise DatasetError(f"cannot read {path}: {exc}") from exc
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path}: not valid JSON ({exc})") from exc
        records = data if isinstance(data, list) else data.get("posts", []) if isinstance(data, dict) else []
    elif suffix == ".jsonl":
        records = []
        for number, line in enumerate(raw.splitlines(), start=1):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise DatasetError(f"{path}: line {number} is not valid JSON ({exc})") from exc
    else:
        records = list(_read_csv(raw))
    report = ImportReport()
    seen: set[str] = set()
    for number, record in enumerate(records, start=2 if suffix == ".csv" else 1):
        if not isinstance(record, dict):
            raise DatasetError(f"{path}: row {number} is not an object")
        row = record if "label" in record and "label_schema" in record else _row_from_record(record, number)
        if row is None:
            report.unlabelled += 1
            continue
        key = fold(row["text"])
        if key in seen:
            report.duplicates += 1
            continue
        seen.add(key)
        report.rows.append(row)
    return report


def _read_csv(raw: str) -> Iterable[dict[str, str]]:
    sample = raw[:4096]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return csv.DictReader(io.StringIO(raw), dialect=dialect)


def _header(name: str) -> str:
    return fold(name).replace(" ", "_")


def _column(record: Mapping[str, Any], name: str) -> str:
    wanted = {_header(alias) for alias in _COLUMNS[name]}
    for key, value in record.items():
        if key is not None and _header(str(key)) in wanted:
            return "" if value is None else str(value).strip()
    return ""


def _flag(value: str, column: str, number: int) -> bool | None:
    folded = fold(value)
    if folded == "":
        return None
    if folded in _TRUE:
        return True
    if folded in _FALSE:
        return False
    raise DatasetError(f"row {number}: '{column}' must be evet or hayır, got {value!r}")


def _category(value: str, lookup: Mapping[str, Any], column: str, number: int) -> Any:
    folded = fold(value)
    if folded in lookup:
        return lookup[folded]
    if value.strip() in lookup:
        return lookup[value.strip()]
    raise DatasetError(f"row {number}: unknown {column} {value!r}")


def _date(value: str, number: int) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for pattern in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(value, pattern)
                break
            except ValueError:
                continue
        if parsed is None:
            raise DatasetError(f"row {number}: cannot read date {value!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _row_from_record(record: Mapping[str, Any], number: int) -> dict[str, Any] | None:
    text = _column(record, "text")
    if not text:
        raise DatasetError(f"row {number}: 'metin' is empty")
    bank_value, complaint_value = _column(record, "is_bank_related"), _column(record, "is_complaint")
    service_value, issue_value = _column(record, "service"), _column(record, "issue")
    if not any((bank_value, complaint_value, service_value, issue_value)):
        return None
    is_bank = _flag(bank_value, "banka_ilgili", number)
    is_complaint = _flag(complaint_value, "sikayet", number)
    if is_bank is None:
        is_bank = bool(is_complaint)
    if is_complaint is None:
        if is_bank:
            raise DatasetError(f"row {number}: 'sikayet' is empty (evet or hayır)")
        is_complaint = False
    if is_complaint and not is_bank:
        raise DatasetError(f"row {number}: a complaint must be about the bank (banka_ilgili = evet)")
    service = _category(service_value, _SERVICE_LOOKUP, "servis", number) if is_bank else ServiceCategory.UNKNOWN
    issue = _category(issue_value, _ISSUE_LOOKUP, "sorun", number) if is_bank else IssueCategory.NONE
    if is_complaint and (service is ServiceCategory.UNKNOWN and not service_value or issue is IssueCategory.NONE):
        raise DatasetError(f"row {number}: a complaint needs both 'servis' and 'sorun'")
    if not is_complaint and issue not in REPUTATION_ISSUES:
        issue = IssueCategory.NONE
    risk = [issue.value] if issue in REPUTATION_ISSUES else []
    sample = fold(_column(record, "sample")).replace(" ", "_") or "other"
    note = _column(record, "note") or None
    return {
        "label_schema": LABEL_SCHEMA_VERSION,
        "post_id": _column(record, "id") or post_id_for(text),
        "text": text,
        "created_at": _date(_column(record, "created_at"), number),
        "label": {
            "is_bank_related": is_bank,
            "is_complaint": is_complaint,
            "service_category": service.value,
            "issue_category": issue.value,
            "risk_signals": risk,
        },
        "verdict": "dataset",
        "sample": sample,
        "note": note,
        "origin": "import",
    }


# --- reading, writing and splitting ------------------------------------------------


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Label rows (export-labels / import-dataset format), checked."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise DatasetError(f"cannot read {path}: {exc}") from exc
    rows = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            label = row["label"]
            row["text"] = str(row["text"])
            row["post_id"] = str(row["post_id"])
            label["is_bank_related"] = bool(label["is_bank_related"])
            label["is_complaint"] = bool(label["is_complaint"])
            ServiceCategory(label["service_category"])
            IssueCategory(label["issue_category"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise DatasetError(f"{path}: line {number} is not a valid label row ({exc})") from exc
        rows.append(row)
    return rows


def write_rows(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    """Write rows as JSONL and return the file's SHA-256."""
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def split(rows: list[dict[str, Any]], test_share: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministic split keeping each (source, about bank, complaint) group in the same proportion."""
    if not 0 < test_share < 1:
        raise DatasetError("test share must be between 0 and 1")
    groups: dict[tuple[str, bool, bool], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        label = row["label"]
        groups[(str(row.get("sample", "other")), label["is_bank_related"], label["is_complaint"])].append(row)
    train: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda row: hashlib.sha256(fold(row["text"]).encode("utf-8")).hexdigest())
        count = round(len(members) * test_share)
        test.extend(members[:count])
        train.extend(members[count:])
    return train, test


def overlap(train: Iterable[Mapping[str, Any]], test: Iterable[Mapping[str, Any]]) -> int:
    """Number of training rows that are also in the test set (same id or same folded text)."""
    test_ids = set()
    test_texts = set()
    for row in test:
        test_ids.add(row["post_id"])
        test_texts.add(fold(row["text"]))
    return sum(1 for row in train if row["post_id"] in test_ids or fold(row["text"]) in test_texts)
