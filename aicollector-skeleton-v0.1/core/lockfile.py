"""Pidfile-based lock to prevent concurrent runs."""
from __future__ import annotations

import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from core.exceptions import LockfileError


class LockfileManager:
    """Manage the volatile pidfile that prevents simultaneous runs."""

    DEFAULT_PATH: ClassVar[Path] = Path("/run/aicollector/aicollector.lock")

    def __init__(self, lockfile_path: Path | None = None) -> None:
        self._lockfile = lockfile_path or self.DEFAULT_PATH

    def acquire(self, run_id: str) -> bool:
        """Acquire the lock.

        Args:
            run_id: UUID of the current run.

        Returns:
            True if the lock was acquired, False otherwise.

        Raises:
            LockfileError: If the lock cannot be created due to permissions.
        """
        self._lockfile.parent.mkdir(parents=True, exist_ok=True)

        if self._lockfile.exists():
            pid = self._read_pid()
            if pid is not None and self._process_alive(pid):
                raise LockfileError(
                    f"Another run is already in progress (PID {pid}). "
                    "Check running processes before retrying."
                )
            # Stale lockfile — safe to overwrite

        content = (
            f"PID:{os.getpid()}\n"
            f"TIMESTAMP:{datetime.now(timezone.utc).isoformat()}\n"
            f"RUN_ID:{run_id}\n"
        )
        try:
            self._lockfile.write_text(content)
        except OSError as exc:
            raise LockfileError(
                f"Cannot create lockfile {self._lockfile}: {exc}"
            ) from exc

        return True

    def release(self) -> None:
        """Remove the lockfile if it was created by the current process."""
        if self._lockfile.exists():
            pid = self._read_pid()
            if pid == os.getpid():
                self._lockfile.unlink()

    def _read_pid(self) -> int | None:
        """Read the PID stored in the lockfile."""
        try:
            content = self._lockfile.read_text()
        except OSError:
            return None
        for line in content.splitlines():
            if line.startswith("PID:"):
                return int(line.split(":", 1)[1])
        return None

    @staticmethod
    def _process_alive(pid: int) -> bool:
        """Check whether a process with the given PID is still alive."""
        try:
            os.kill(pid, signal.SIG_DFL)
            return True
        except OSError:
            return False
