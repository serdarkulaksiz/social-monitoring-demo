from __future__ import annotations

from pathlib import Path

import pytest

from social_incident_monitor.application.errors import StorageError
from social_incident_monitor.infrastructure.jsonl.jsonl_file import JsonlFile


def test_missing_file_reads_as_empty(tmp_path: Path) -> None:
    assert list(JsonlFile(tmp_path / "absent.jsonl").read()) == []


def test_append_and_read_round_trip_utf8(tmp_path: Path) -> None:
    file = JsonlFile(tmp_path / "data" / "records.jsonl")
    file.append([{"id": 1, "text": "Ziraat çalışmıyor"}, {"id": 2}])
    assert list(file.read()) == [{"id": 1, "text": "Ziraat çalışmıyor"}, {"id": 2}]
    assert "çalışmıyor" in (tmp_path / "data" / "records.jsonl").read_text(encoding="utf-8")


def test_corrupt_lines_are_skipped_and_counted(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    path.write_bytes(b'{"id": 1}\nnot-json\n[1, 2]\n\n{"id": 2}\n\xff\xfe\n')
    file = JsonlFile(path)
    assert list(file.read()) == [{"id": 1}, {"id": 2}]
    assert file.corrupt_lines == 3


def test_torn_last_line_is_isolated_before_next_append(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    path.write_bytes(b'{"id": 1}\n{"id": 2, "tex')  # simulated crash in the middle of a write
    file = JsonlFile(path)
    file.append([{"id": 3}])
    reader = JsonlFile(path)
    assert list(reader.read()) == [{"id": 1}, {"id": 3}]
    assert reader.corrupt_lines == 1


def test_write_failure_raises_storage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = JsonlFile(tmp_path / "records.jsonl")

    def failing_open(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "open", failing_open)
    with pytest.raises(StorageError, match="No space left"):
        file.append([{"id": 1}])


def test_replace_all_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    file = JsonlFile(path)
    file.append([{"id": 1}, {"id": 2}])
    file.replace_all([{"id": 2}])
    assert list(JsonlFile(path).read()) == [{"id": 2}]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["records.jsonl"]
