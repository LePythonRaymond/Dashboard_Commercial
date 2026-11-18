"""
One pipeline run at a time on the server.

The 06:00 cron and the dashboard's "Actualiser les données" (scripts/refresh_watch.py)
both run scripts/run_pipeline.py. Two runs writing the same Google Sheets tabs at the
same time can leave a tab empty: each write clears the tab before writing it. An
exclusive lock on a file (fcntl, released by the system if the process dies) makes
the second run wait for the first.

Taking a lock also touches the file. The server's weekly cleanup deletes the files of
logs/ that are 7 to 14 days old; a lock file deleted while held would let the next
run lock a new file of the same name and run alongside. Touched on every use (each
minute for the watcher, each morning for the pipeline), a lock file never gets old.
"""

import fcntl
import os
import time
from pathlib import Path
from typing import Callable, Optional


class LockTimeout(Exception):
    """The lock was still held when the wait ran out."""


class FileLock:
    """Exclusive lock on a file, held until release() or the end of the process."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = None

    def try_acquire(self) -> bool:
        """Take the lock if it is free; never waits."""
        handle = open(self.path, "a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        os.utime(self.path)  # see the module docstring: keeps the weekly log cleanup away
        self._file = handle
        return True

    def acquire(
        self,
        wait_seconds: float,
        poll_seconds: float = 5.0,
        on_wait: Optional[Callable[[], None]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Take the lock, waiting up to wait_seconds; on_wait is called once if it has to wait."""
        deadline = time.monotonic() + wait_seconds
        warned = False
        while not self.try_acquire():
            if time.monotonic() >= deadline:
                raise LockTimeout(f"{self.path} still locked after {wait_seconds:.0f} s")
            if on_wait and not warned:
                on_wait()
                warned = True
            sleep(poll_seconds)

    def release(self) -> None:
        if self._file is not None:
            fcntl.flock(self._file, fcntl.LOCK_UN)
            self._file.close()
            self._file = None


def is_locked(path: Path) -> bool:
    """True when another process holds the lock (checked by taking and releasing it)."""
    lock = FileLock(path)
    if lock.try_acquire():
        lock.release()
        return False
    return True


def pipeline_lock_path(project_root: Path) -> Path:
    """The lock shared by every run of scripts/run_pipeline.py that writes."""
    return Path(project_root) / "logs" / ".pipeline.lock"
