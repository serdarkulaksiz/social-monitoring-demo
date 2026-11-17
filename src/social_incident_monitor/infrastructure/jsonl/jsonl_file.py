"""Low-level, crash-tolerant JSONL file access.

Guarantees and limits:

* Each record is written as one UTF-8 line with a single ``write`` call, followed by
  ``flush`` and ``os.fsync``. A crash can leave at most one partial (torn) last line.
* Before the first append of a process, a missing trailing newline is repaired by
  writing a newline, so a torn fragment can never merge with the next record.
* Reading skips blank lines and lines that are not valid JSON objects; skipped lines are
  counted in ``corrupt_lines`` and logged with their line number (never their content).
* Atomic rewrite (``replace_all``) writes to a temporary file in the same directory and
  swaps it in with ``os.replace``, which is atomic on Windows and POSIX.
* The class is not safe for use by several processes at once; the application enforces
  a single writer process with a lock file.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from ...application.errors import StorageError

logger = logging.getLogger(__name__)


class JsonlFile:
    def __init__(self, path: Path, *, fsync: bool = True) -> None:
        self.path = path
        self._fsync = fsync
        self._tail_checked = False
        self.corrupt_lines = 0

    def read(self) -> Iterator[dict[str, Any]]:
        """Yield every valid record. A missing file yields nothing."""
        if not self.path.exists():
            return
        try:
            with self.path.open("rb") as handle:
                for line_number, raw in enumerate(handle, start=1):
                    record = self._decode(raw, line_number)
                    if record is not None:
                        yield record
        except OSError as exc:
            raise StorageError(f"cannot read {self.path.name}: {exc.strerror or exc}") from exc

    def append(self, records: Iterable[Mapping[str, Any]]) -> None:
        payload = b"".join(self._encode(record) for record in records)
        if not payload:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            prefix = self._tail_repair_prefix()
            with self.path.open("ab") as handle:
                handle.write(prefix + payload)
                handle.flush()
                if self._fsync:
                    os.fsync(handle.fileno())
            self._tail_checked = True
        except OSError as exc:
            raise StorageError(f"cannot write {self.path.name}: {exc.strerror or exc}") from exc

    def replace_all(self, records: Iterable[Mapping[str, Any]]) -> None:
        """Atomically replace the file content with ``records``."""
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as handle:
                for record in records:
                    handle.write(self._encode(record))
                handle.flush()
                if self._fsync:
                    os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self._tail_checked = True
        except OSError as exc:
            raise StorageError(f"cannot rewrite {self.path.name}: {exc.strerror or exc}") from exc

    def mark_corrupt(self, line_hint: str) -> None:
        """Record a line that parsed as JSON but failed schema validation."""
        self.corrupt_lines += 1
        logger.warning("skipping invalid record in %s (%s)", self.path.name, line_hint)

    def _tail_repair_prefix(self) -> bytes:
        if self._tail_checked or not self.path.exists():
            return b""
        size = self.path.stat().st_size
        if size == 0:
            return b""
        with self.path.open("rb") as handle:
            handle.seek(size - 1)
            last_byte = handle.read(1)
        if last_byte != b"\n":
            logger.warning("repairing torn last line in %s", self.path.name)
            return b"\n"
        return b""

    def _decode(self, raw: bytes, line_number: int) -> dict[str, Any] | None:
        stripped = raw.strip()
        if not stripped:
            return None
        try:
            record = json.loads(stripped.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            record = None
        if not isinstance(record, dict):
            self.corrupt_lines += 1
            logger.warning("skipping corrupt line %d in %s", line_number, self.path.name)
            return None
        return record

    @staticmethod
    def _encode(record: Mapping[str, Any]) -> bytes:
        return (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
