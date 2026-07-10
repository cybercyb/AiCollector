"""Startup self-diagnostic: verify environment compatibility and write safety."""
from __future__ import annotations

import errno
import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from core.exceptions import AICollectorError


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Results of the startup diagnostic checks."""
    python_version: str
    python_ok: bool
    platform_ok: bool
    directories_ok: bool
    disk_space_mb: float | None
    failed_checks: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SelfDiagnostic:
    """Run robust pre-flight environment checks."""

    MIN_PYTHON: tuple[int, ...] = (3, 12)
    MIN_DISK_MB: float = 100.0

    def __init__(self, base_dir: Path, log_dir: Path) -> None:
        self._base_dir = base_dir
        self._log_dir = log_dir

    def run(self) -> DiagnosticReport:
        """Execute all diagnostic checks.

        Returns:
            DiagnosticReport with pass/fail for each check.

        Raises:
            AICollectorError: On any fatal check failure.
        """
        errors: list[str] = []
        warnings: list[str] = []
        failed_checks = 0
        python_ok = True
        platform_ok = True
        directories_ok = True
        disk_space_mb = None

        # 1. Python version check
        if sys.version_info < self.MIN_PYTHON:
            python_ok = False
            failed_checks += 1
            errors.append(
                f"Python {'.'.join(map(str, self.MIN_PYTHON))}+ required, "
                f"found {platform.python_version()}"
            )

        # 2. Platform compatibility check
        if platform.system() != "Linux":
            platform_ok = False
            warnings.append(f"Target OS is Linux, found {platform.system()}")

        # 3. Directories writeability check with guaranteed clean-up
        for directory in (self._base_dir, self._log_dir):
            try:
                directory.mkdir(parents=True, exist_ok=True)
                test_file = directory / f".write_test_{directory.name}"
                try:
                    test_file.touch()
                finally:
                    # Garantit le nettoyage immédiat sans laisser de fichier orphelin
                    if test_file.exists():
                        test_file.unlink()
            except OSError as exc:
                directories_ok = False
                failed_checks += 1
                err_msg = f"Directory write check failed on '{directory}': "
                if exc.errno == errno.ENOSPC:
                    err_msg += "No space left on device"
                elif exc.errno == errno.EROFS:
                    err_msg += "Read-only file system"
                elif exc.errno == errno.EACCES:
                    err_msg += "Permission denied"
                else:
                    err_msg += str(exc)
                errors.append(err_msg)

        # 4. Disk space check (resolved against existing parent path to avoid FileNotFoundError)
        target_disk_path = self._base_dir
        while not target_disk_path.exists() and target_disk_path != target_disk_path.parent:
            target_disk_path = target_disk_path.parent

        try:
            stat = shutil.disk_usage(target_disk_path)
            disk_space_mb = stat.free / (1024 * 1024)
            if disk_space_mb < self.MIN_DISK_MB:
                warnings.append(
                    f"Low disk space on device backing '{target_disk_path}': {disk_space_mb:.1f} MB free (Min: {self.MIN_DISK_MB} MB)"
                )
        except OSError as exc:
            warnings.append(f"Could not check disk space on '{target_disk_path}': {exc}")

        # Assemble and return immutable report
        report = DiagnosticReport(
            python_version=platform.python_version(),
            python_ok=python_ok,
            platform_ok=platform_ok,
            directories_ok=directories_ok,
            disk_space_mb=disk_space_mb,
            failed_checks=failed_checks,
            errors=errors,
            warnings=warnings,
        )

        if report.errors:
            raise AICollectorError("Diagnostic failed: " + "; ".join(report.errors))

        return report
