"""Single abstraction layer for all OS-level system interactions.

All subprocess calls must go through this class. Commands not on the
whitelist will raise ``ForbiddenCommandError`` immediately.
"""
from __future__ import annotations

import os
import subprocess
import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Final

from core.exceptions import (
    ForbiddenCommandError,
    CommandExecutionError,
    ProcFileReadError,
)

logger = logging.getLogger("aicollector")

# Whitelist stricte de commandes autorisées en lecture seule
ALLOWED_COMMANDS: Final[frozenset[str]] = frozenset({
    "systemctl", "docker", "ip", "journalctl", "ss",
    "apt", "dpkg", "ufw", "iptables", "smartctl",
    "auditctl", "crontab", "openssl", "lsblk", "df",
    "sensors", "nproc", "hostname", "uname", "ls",
    "cat", "grep", "awk", "cut", "sort", "uniq",
    "wc", "find", "stat", "id", "whoami", "uptime",
    "free", "mount", "ps", "netstat", "dpkg-query", "apt",
})

# Arguments suspects détectés par simple sous-chaîne (contient)
DANGEROUS_SUBSTRING_PATTERNS: Final[frozenset[str]] = frozenset({
    "-exec", "system", "eval", ">", "<", "|", ";", "&&", "||"
})

# Exécutables interdits détectés de manière exacte pour éviter les faux positifs (comme '--show')
DANGEROUS_EXECUTABLE_NAMES: Final[frozenset[str]] = frozenset({
    "sh", "bash", "python", "perl", "nc", "ncat", "curl", "wget"
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
        self._cache: dict[tuple[str, tuple[str, ...]], Any] = {}
        self._file_cache: dict[Path, str] = {}
        self._enable_cache = enable_cache
        
        # Chemins explicitement interdits (sécurité en profondeur)
        self._forbidden_substrings: list[str] = [
            "/etc/shadow", "/etc/gshadow", "/root/.ssh", "/home/", ".env", "id_rsa"
        ]

    def _validate_safe_args(self, cmd: str, args: list[str]) -> None:
        """Analyze arguments to prevent shell bypass/injection."""
        for arg in args:
            lower_arg = arg.lower()

            # 1. Validation des motifs par sous-chaîne
            if any(pattern in lower_arg for pattern in DANGEROUS_SUBSTRING_PATTERNS):
                logger.error(f"Security block: Dangerous argument pattern '{arg}' detected in command '{cmd}'")
                raise ForbiddenCommandError(f"{cmd} (rejected due to unsafe argument: {arg})")

            # 2. Validation exacte des binaires interdits (ex: 'sh' ou '/bin/sh')
            is_forbidden_exe = (
                lower_arg in DANGEROUS_EXECUTABLE_NAMES or
                any(lower_arg.endswith("/" + exe) for exe in DANGEROUS_EXECUTABLE_NAMES)
            )
            if is_forbidden_exe:
                logger.error(f"Security block: Forbidden executable '{arg}' passed as argument to command '{cmd}'")
                raise ForbiddenCommandError(f"{cmd} (rejected due to unsafe executable argument: {arg})")

    def _assert_safe_path(self, path: Path, allowed_roots: list[Path]) -> None:
        """Ensure a file path is safe and restricted to whitelisted pseudo-filesystems."""
        try:
            resolved = path.resolve()
        except OSError as exc:
            raise ProcFileReadError(str(path), cause=exc)

        # 1. Vérification contre les sous-chaînes interdites
        resolved_str = str(resolved)
        if any(forbidden in resolved_str for forbidden in self._forbidden_substrings):
            logger.error(f"Security block: Attempted read access to forbidden path '{resolved_str}'")
            raise ProcFileReadError(str(path), cause=PermissionError("Access denied by SystemAdapter policy"))

        # 2. Vérification que le chemin réside sous l'une des racines autorisées
        is_under_allowed_root = False
        for root in allowed_roots:
            try:
                resolved.relative_to(root.resolve())
                is_under_allowed_root = True
                break
            except ValueError:
                continue

        if not is_under_allowed_root:
            logger.error(f"Security block: Attempted read access outside allowed roots: {resolved_str}")
            raise ProcFileReadError(str(path), cause=PermissionError("Path is outside allowed directories"))

    def run_command(
        self,
        cmd: str,
        args: list[str] | None = None,
        timeout: int = 30,
    ) -> CommandResult:
        """Execute a whitelisted system command safely.

        Args:
            cmd: Command name (must be in ALLOWED_COMMANDS).
            args: List of command-line arguments.
            timeout: Timeout in seconds.

        Returns:
            CommandResult with stdout, stderr, returncode, and elapsed_ms.
        """
        if cmd not in ALLOWED_COMMANDS:
            raise ForbiddenCommandError(cmd)

        safe_args = args or []
        self._validate_safe_args(cmd, safe_args)

        # Structuration de la clé de cache (tuple immuable et déterministe)
        cache_key = (cmd, tuple(safe_args))
        if self._enable_cache and cache_key in self._cache:
            return self._cache[cache_key]

        full_cmd = [cmd] + safe_args
        start = time.monotonic()
        try:
            # Execution sécurisée sans shell (shell=False implicite)
            result = subprocess.run(  # noqa: S603
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # Les exceptions de timeout sont non-bloquantes (CommandExecutionError a is_blocking=False)
            raise CommandExecutionError(cmd, f"Command timed out after {timeout}s") from exc
        except OSError as exc:
            raise CommandExecutionError(cmd, f"Failed to execute command: {exc}") from exc

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

    def _read_file_cached(self, path: Path, allowed_roots: list[Path]) -> str:
        """Read and cache a system pseudo-file with strict path checks."""
        self._assert_safe_path(path, allowed_roots)

        if self._enable_cache and path in self._file_cache:
            return self._file_cache[path]

        try:
            # Utilisation de errors="replace" pour éviter les plantages sur caractères non-UTF8
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ProcFileReadError(str(path), cause=exc)

        if self._enable_cache:
            self._file_cache[path] = content
        return content

    def read_proc_file(self, path: str | Path) -> str:
        """Read the content of a /proc/ file.

        Args:
            path: Absolute path within /proc/.
        """
        return self._read_file_cached(Path(path), allowed_roots=[Path("/proc")])

    def read_sys_file(self, path: str | Path) -> str:
        """Read the content of a /sys/ file.

        Args:
            path: Absolute path within /sys/.
        """
        # strip() appliqué par défaut pour le sysfs qui contient beaucoup de retours à la ligne
        return self._read_file_cached(Path(path), allowed_roots=[Path("/sys")]).strip()

    def list_directory(self, path: str | Path, pattern: str = "*") -> list[Path]:
        """List directory contents with optional glob pattern.

        Args:
            path: Directory to list.
            pattern: Glob pattern (default: all files).
        """
        base = Path(path)
        # On restreint le listing aux structures systèmes légitimes pour le collecteur
        self._assert_safe_path(base, allowed_roots=[Path("/proc"), Path("/sys"), Path("/etc")])
        
        if not base.is_dir():
            return []
        try:
            return sorted(base.glob(pattern))
        except OSError as exc:
            logger.warning(f"Failed to list directory {path} with pattern {pattern}: {exc}")
            return []

    def check_tool_available(self, tool: str) -> bool:
        """Check whether a binary is available in PATH.

        Args:
            tool: Command name to check.
        """
        import shutil
        return shutil.which(tool) is not None

    def clear_cache(self) -> None:
        """Clear the intra-run cache. Called between runs."""
        self._cache.clear()
        self._file_cache.clear()
