"""Startup self-diagnostic: verify environment compatibility."""
from __future__ import annotations

import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.exceptions import AICollectorError


@dataclass
class DiagnosticReport:
    """Results of the startup diagnostic checks."""
    python_version: str
    python_ok: bool
    platform_ok: bool
    directories_ok: bool
    disk_space_mb: float | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed_checks: int = 0


class SelfDiagnostic:
    """Run pre-flight environment checks."""

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
        report = DiagnosticReport(
            python_version=platform.python_version(),
            python_ok=True,
            platform_ok=True,
            directories_ok=True,
            disk_space_mb=None,
        )
        # Python version
        if sys.version_info < self.MIN_PYTHON:
            report.python_ok = False
            report.failed_checks += 1
            report.errors.append(
                f"Python {'.'.join(map(str, self.MIN_PYTHON))}+ required, "
                f"found {platform.python_version()}"
            )

        # Platform
        if platform.system() != "Linux":
            report.platform_ok = False
            report.warnings.append(f"Target OS is Linux, found {platform.system()}")

        # Directories writable
        for directory in (self._base_dir, self._log_dir):
            try:
                directory.mkdir(parents=True, exist_ok=True)
                (directory / ".write_test").touch()
                (directory / ".write_test").unlink()
            except OSError as exc:
                report.directories_ok = False
                report.errors.append(f"Directory not writable: {directory} ({exc})")

        # Disk space
        import shutil
        try:
            stat = shutil.disk_usage(self._base_dir)
            report.disk_space_mb = stat.free / (1024 * 1024)
            if report.disk_space_mb < self.MIN_DISK_MB:
                report.warnings.append(
                    f"Low disk space: {report.disk_space_mb:.1f} MB free"
                )
        except OSError:
            pass

        if report.errors:
            raise AICollectorError("Diagnostic failed: " + "; ".join(report.errors))
        return report
