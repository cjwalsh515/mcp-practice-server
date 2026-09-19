"""A simple advisory file lock so review.py's long-lived server and
relabel.py's structural move don't race on the same --output tree.

review.py loads review_decisions.json once into memory at startup and
writes the *entire* in-memory copy back on every keystroke — it never
re-reads the file. If relabel.py rewrites that file (and moves the
folders review.py's cached item list points at) while a review.py server
is still running, the next click in that browser tab silently overwrites
relabel's changes with review.py's stale pre-relabel state. This isn't a
distributed lock or crash-proof against clock skew — it's just enough to
turn that realistic mistake into a clear, immediate error instead of
silent data loss.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOCK_FILENAME = ".photo_pipeline.lock"


class LockHeld(RuntimeError):
    pass


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else -- treat as alive
    except OSError:
        return False
    return True


@dataclass
class LockInfo:
    owner: str
    pid: int


def read_lock(output_dir: Path) -> Optional[LockInfo]:
    path = output_dir / LOCK_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return LockInfo(owner=data["owner"], pid=int(data["pid"]))
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return None


def check_lock_or_raise(output_dir: Path) -> None:
    info = read_lock(output_dir)
    if info is None:
        return
    if _pid_is_alive(info.pid):
        raise LockHeld(
            f"{info.owner} (pid {info.pid}) is currently using {output_dir}. "
            "Stop it first, then re-run this command — running two of these tools "
            "against the same output folder at once can silently overwrite each "
            "other's changes. If that process isn't actually running anymore, "
            f"delete {output_dir / LOCK_FILENAME} and try again."
        )
    logger.warning(
        "Found a stale lock left by %s (pid %d, not running anymore) — proceeding.",
        info.owner,
        info.pid,
    )


@contextmanager
def acquire(output_dir: Path, *, owner: str):
    """Raises LockHeld if another live process already holds the lock;
    otherwise holds it for the duration of the ``with`` block.
    """
    check_lock_or_raise(output_dir)
    path = output_dir / LOCK_FILENAME
    path.write_text(json.dumps({"owner": owner, "pid": os.getpid()}), encoding="utf-8")
    try:
        yield
    finally:
        current = read_lock(output_dir)
        if current is not None and current.pid == os.getpid():
            try:
                path.unlink()
            except OSError:
                pass
