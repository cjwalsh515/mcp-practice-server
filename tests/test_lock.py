import json
import subprocess
import sys

import pytest

from photo_pipeline.lock import LOCK_FILENAME, LockHeld, acquire, check_lock_or_raise, read_lock


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_no_lock_file_is_a_no_op(tmp_path):
    check_lock_or_raise(tmp_path)  # must not raise
    assert read_lock(tmp_path) is None


def test_acquire_writes_and_releases_lock(tmp_path):
    with acquire(tmp_path, owner="review.py"):
        info = read_lock(tmp_path)
        assert info is not None
        assert info.owner == "review.py"
    assert read_lock(tmp_path) is None


def test_acquire_raises_when_another_live_process_holds_it(tmp_path):
    (tmp_path / LOCK_FILENAME).write_text(json.dumps({"owner": "review.py", "pid": __import__("os").getpid()}))

    with pytest.raises(LockHeld, match="review.py"):
        with acquire(tmp_path, owner="relabel.py"):
            pass


def test_stale_lock_from_dead_process_is_ignored(tmp_path):
    dead_pid = _dead_pid()
    (tmp_path / LOCK_FILENAME).write_text(json.dumps({"owner": "review.py", "pid": dead_pid}))

    with acquire(tmp_path, owner="relabel.py"):
        info = read_lock(tmp_path)
        assert info.owner == "relabel.py"  # took over the stale lock


def test_lock_released_even_if_the_wrapped_code_raises(tmp_path):
    with pytest.raises(ValueError):
        with acquire(tmp_path, owner="review.py"):
            raise ValueError("boom")
    assert read_lock(tmp_path) is None


def test_corrupt_lock_file_treated_as_no_lock(tmp_path):
    (tmp_path / LOCK_FILENAME).write_text("not json")
    check_lock_or_raise(tmp_path)  # must not raise
