"""Pidfile-based lock to prevent concurrent runs."""
from __future__ import annotations

import os
import errno
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from core.exceptions import LockfileError

logger = logging.getLogger("aicollector")


class LockfileManager:
    """Manage the volatile pidfile that prevents simultaneous runs."""

    DEFAULT_PATH: ClassVar[Path] = Path("/run/aicollector/aicollector.lock")

    def __init__(self, lockfile_path: Path | None = None) -> None:
        self._lockfile = lockfile_path or self.DEFAULT_PATH

    def acquire(self, run_id: str) -> bool:
        """Acquire the lock atomicaly.

        Args:
            run_id: UUID of the current run.

        Returns:
            True if the lock was acquired.

        Raises:
            LockfileError: If the lock is held by an active process or permissions fail.
        """
        # S'assurer que le répertoire parent existe
        try:
            self._lockfile.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LockfileError(
                f"Cannot create lock directory '{self._lockfile.parent}': {exc}"
            ) from exc

        content = (
            f"PID:{os.getpid()}\n"
            f"TIMESTAMP:{datetime.now(timezone.utc).isoformat()}\n"
            f"RUN_ID:{run_id}\n"
        )

        try:
            # Mode 'x' (O_CREAT | O_EXCL) : création et écriture atomique.
            # Échoue immédiatement si le fichier existe déjà, éliminant la race condition TOCTOU.
            with self._lockfile.open("x", encoding="utf-8") as f:
                f.write(content)
            return True

        except FileExistsError:
            # Le lockfile existe déjà. Analysons si le lock est obsolète (stale)
            pid = self._read_pid()
            
            if pid is None:
                # Fichier corrompu ou vide : on l'écrase de manière sécurisée
                logger.warning(f"Lockfile '{self._lockfile}' is empty or corrupt. Overwriting.")
                self._force_overwrite(content)
                return True

            if self._process_alive(pid):
                raise LockfileError(
                    f"Another run is already in progress (PID {pid}). "
                    "Check running processes before retrying."
                )

            # Le processus d'origine est mort (stale lockfile) : écrasement sécurisé
            logger.info(f"Lockfile found for dead PID {pid}. Cleaning up and acquiring lock.")
            self._force_overwrite(content)
            return True

        except OSError as exc:
            raise LockfileError(
                f"Failed to write lockfile '{self._lockfile}': {exc}"
            ) from exc

    def release(self) -> None:
        """Remove the lockfile if it was created by the current process."""
        try:
            if self._lockfile.exists():
                pid = self._read_pid()
                if pid == os.getpid():
                    self._lockfile.unlink(missing_ok=True)
        except OSError as exc:
            # Ne pas faire crasher le teardown de l'application si l'unlink échoue, mais logger l'incident
            logger.error(f"Failed to release lockfile '{self._lockfile}': {exc}")

    def _read_pid(self) -> int | None:
        """Read the PID stored in the lockfile."""
        try:
            content = self._lockfile.read_text(encoding="utf-8")
            for line in content.splitlines():
                if line.startswith("PID:"):
                    return int(line.split(":", 1)[1].strip())
        except (OSError, ValueError, IndexError):
            # OSError (lecture impossible), ValueError (cast int impossible), IndexError (split invalide)
            return None
        return None

    def _force_overwrite(self, content: str) -> None:
        """Force overwrite a stale lockfile using atomic replace."""
        tmp_path = self._lockfile.with_suffix(".lock.tmp")
        try:
            tmp_path.write_text(content, encoding="utf-8")
            # os.replace est garanti atomique sous Linux/POSIX
            os.replace(tmp_path, self._lockfile)
        except OSError as exc:
            # Nettoyer le temporaire au cas où
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise LockfileError(f"Cannot overwrite stale lockfile: {exc}") from exc

    @staticmethod
    def _process_alive(pid: int) -> bool:
        """Check whether a process with the given PID is still alive.

        Uses standard POSIX signal 0 to verify process existence.
        """
        if pid <= 0:
            return False
        try:
            # Signal 0 : n'envoie pas de signal mais effectue la vérification d'existence
            # et de droits d'accès au niveau de l'OS.
            os.kill(pid, 0)
            return True
        except OSError as exc:
            # ESRCH : Le processus n'existe pas
            if exc.errno == errno.ESRCH:
                return False
            # EPERM : Le processus existe mais nous n'avons pas le droit de lui envoyer de signal
            if exc.errno == errno.EPERM:
                return True
            # Autre erreur OS étrange : on assume que le process est vivant par précaution
            return True
