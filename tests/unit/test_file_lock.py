from pathlib import Path

import pytest

from social_incident_monitor.infrastructure.file_lock import LockUnavailableError, ProcessLock


def test_second_lock_on_same_file_is_refused(tmp_path: Path) -> None:
    first = ProcessLock(tmp_path / "monitor.lock")
    first.acquire()
    try:
        with pytest.raises(LockUnavailableError):
            ProcessLock(tmp_path / "monitor.lock").acquire()
    finally:
        first.release()
    with ProcessLock(tmp_path / "monitor.lock") as again:
        assert again.is_held
