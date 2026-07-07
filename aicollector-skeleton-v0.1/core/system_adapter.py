"""Single abstraction layer for all OS-level system interactions.

All subprocess calls must go through this class.  Commands not on the
whitelist will raise ``ForbiddenCommandError`` immediately.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from core.exceptions import ForbiddenCommandError, CommandExecutionError, ProcFileReadError


ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "systemctl", "docker", "ip", "journalctl", "ss",
    "apt", "dpkg", "ufw", "iptables", "smartctl",
    "auditctl", "crontab", "openssl", "lsblk", "df",
    "sensors", "nproc", "hostname", "uname", "ls",
    "cat", "grep", "awk", "cut", "sort", "uniq",
    "wc", "find", "stat", "id", "whoami", "uptime",
    "free", "mount", "ps", "netstat",
})


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Result of a single subprocess execution."""
    stdout: str
    stderr: str
    returncode: int
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class ProcFileResult:
    """Result of reading a /proc or /sys file."""
    content: str
    path: str


class SystemAdapter:
    """Unified interface for all system calls and file reads."""

    def __init__(self, enable_cache: bool = True) -> None:
        self._cache: dict[str, Any] = {}
        self._enable_cache = enable_cache
        self._forbidden_paths: set[str] = {
            "/etc/shadow", "/root/.ssh",
        }

    def run_command(
        self,
        cmd: str,
        args: list[str] | None = None,
        timeout: int = 30,
    ) -> CommandResult:
        """Execute a whitelisted system command.

        Args:
            cmd: Command name (must be in ALLOWED_COMMANDS).
            args: List of command-line arguments.
            timeout: Timeout in seconds.

        Returns:
            CommandResult with stdout, stderr, returncode, and elapsed_ms.

        Raises:
            ForbiddenCommandError: If ``cmd`` is not on the whitelist.
            CommandExecutionError: If the command returns non-zero.
            subprocess.TimeoutExpired: If the command times out.
        """
        if cmd not in ALLOWED_COMMANDS:
            raise ForbiddenCommandError(cmd)

        cache_key = f"{cmd}:{args}"
        if self._enable_cache and cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]

        import time
        start = time.monotonic()
        full_cmd = [cmd] + (args or [])
        try:
            result = subprocess.run(  # noqa: S603
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandExecutionError(cmd, f"Command timed out after {timeout}s") from exc

        elapsed_ms = (time.monotonic() - start) * 1000
        cmd_result = CommandResult(
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
            elapsed_ms=elapsed_ms,
        )

        if self._enable_cache:
            self._cache[cache_key] = cmd_result

        return cmd_result

    def read_proc_file(self, path: str | Path) -> str:
        """Read the content of a /proc/ file.

        Args:
            path: Absolute path within /proc/.

        Returns:
            File content as a string.

        Raises:
            ProcFileReadError: If the file cannot be read.
        """
        abs_path = Path(path)
        cache_key = f"proc:{abs_path}"
        if self._enable_cache and cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]

        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ProcFileReadError(str(abs_path)) from exc

        if self._enable_cache:
            self._cache[cache_key] = content
        return content

    def read_sys_file(self, path: str | Path) -> str:
        """Read the content of a /sys/ file.

        Args:
            path: Absolute path within /sys/.

        Returns:
            File content as a string.

        Raises:
            ProcFileReadError: If the file cannot be read.
        """
        abs_path = Path(path)
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            raise ProcFileReadError(str(abs_path)) from exc
        return content

    def list_directory(self, path: str | Path, pattern: str = "*") -> list[Path]:
        """List directory contents with optional glob pattern.

        Args:
            path: Directory to list.
            pattern: Glob pattern (default: all files).

        Returns:
            List of matching ``Path`` objects.
        """
        base = Path(path)
        if not base.is_dir():
            return []
        return sorted(base.glob(pattern))

    def check_tool_available(self, tool: str) -> bool:
        """Check whether a binary is available in PATH.

        Args:
            tool: Command name to check.

        Returns:
            True if the tool is found and executable.
        """
        import shutil
        return shutil.which(tool) is not None

    def clear_cache(self) -> None:
        """Clear the intra-run cache. Called between runs."""
        self._cache.clear()
