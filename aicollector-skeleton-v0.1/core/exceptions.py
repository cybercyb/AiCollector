"""Hierarchical custom exceptions for AICollector.

Exit codes:
  1  — Generic / unclassified error
  2  — CollectorError base
  3  — Collector timeout / permission
  4  — ForbiddenCommandError (blocking)
  5  — ProcFileReadError
  10 — ConfigError (blocking)
  20 — PipelineError (blocking)
  30 — LockfileError (blocking)
  40 — SchemaValidationError
  50 — KnowledgeStoreError
  60 — EventBusError
"""
from __future__ import annotations

from typing import ClassVar


class AICollectorError(Exception):
    """Base exception for all AICollector errors."""

    exit_code: ClassVar[int] = 1
    _is_blocking: ClassVar[bool] = True

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return f"{self.__class__.__name__}: {self.message}"


# ── Collector errors ──────────────────────────────────────────────────────

class CollectorError(AICollectorError):
    """Base for collector-specific errors."""
    exit_code = 2
    _is_blocking = False

    def __init__(self, collector_name: str, message: str) -> None:
        super().__init__(message)
        self.collector_name = collector_name

    def __str__(self) -> str:
        return f"{self.__class__.__name__}[{self.collector_name}]: {self.message}"


class CollectorNotFoundError(CollectorError):
    """Raised when a requested collector is not registered."""
    exit_code = 2


class CollectorTimeoutError(CollectorError):
    """Raised when a collector exceeds its timeout."""
    exit_code = 3


class CollectorPermissionError(CollectorError):
    """Raised when a collector lacks required permissions."""
    exit_code = 3


# ── SystemAdapter errors ───────────────────────────────────────────────────

class SystemAdapterError(AICollectorError):
    """Base for SystemAdapter errors."""
    exit_code = 4
    _is_blocking = True


class ForbiddenCommandError(SystemAdapterError):
    """Raised when a command is not on the allowed whitelist."""
    exit_code = 4

    def __init__(self, command: str) -> None:
        super().__init__(f"Command not allowed: {command}")
        self.command = command


class CommandExecutionError(SystemAdapterError):
    """Raised when a whitelisted command fails."""
    exit_code = 4

    def __init__(self, command: str, stderr: str) -> None:
        super().__init__(f"Command failed: {command}")
        self.command = command
        self.stderr = stderr


class ProcFileReadError(SystemAdapterError):
    """Raised when a /proc or /sys file cannot be read."""
    exit_code = 5
    _is_blocking = False

    def __init__(self, path: str) -> None:
        super().__init__(f"Cannot read proc/sys file: {path}")
        self.path = path


# ── Config errors ───────────────────────────────────────────────────────────

class ConfigError(AICollectorError):
    """Base for configuration errors."""
    exit_code = 10


class ConfigFileNotFoundError(ConfigError):
    """Raised when config.yaml does not exist."""
    exit_code = 10

    def __init__(self, path: str) -> None:
        super().__init__(f"Configuration file not found: {path}")
        self.path = path


class ConfigValidationError(ConfigError):
    """Raised when config.yaml fails Pydantic validation."""
    exit_code = 10

    def __init__(self, errors: str) -> None:
        super().__init__(f"Configuration validation failed: {errors}")
        self.errors = errors


# ── Pipeline errors ────────────────────────────────────────────────────────

class PipelineError(AICollectorError):
    """Base for pipeline-level errors."""
    exit_code = 20


class PhaseError(PipelineError):
    """Raised when a pipeline phase encounters a fatal error."""

    def __init__(self, phase: str, message: str) -> None:
        super().__init__(message)
        self.phase = phase

    def __str__(self) -> str:
        return f"PhaseError[{self.phase}]: {self.message}"


class RunInterruptedError(PipelineError):
    """Raised when the pipeline run is interrupted externally."""
    exit_code = 20


# ── Lockfile error ──────────────────────────────────────────────────────────

class LockfileError(AICollectorError):
    """Raised when a concurrent run is detected or lockfile cannot be created."""
    exit_code = 30


# ── Schema errors ───────────────────────────────────────────────────────────

class SchemaValidationError(AICollectorError):
    """Raised when a JSON does not match its Pydantic schema."""
    exit_code = 40
    _is_blocking = False

    def __init__(self, source: str, errors: str) -> None:
        super().__init__(f"Schema validation failed for '{source}': {errors}")
        self.source = source
        self.errors = errors


# ── Knowledge store errors ──────────────────────────────────────────────────

class KnowledgeStoreError(AICollectorError):
    """Base for knowledge store errors."""
    exit_code = 50
    _is_blocking = False


class KnowledgeWriteError(KnowledgeStoreError):
    """Raised when writing a knowledge JSON file fails."""
    exit_code = 51

    def __init__(self, collector_name: str, path: str) -> None:
        super().__init__(f"Cannot write knowledge file for '{collector_name}': {path}")
        self.collector_name = collector_name
        self.path = path


class HistoryReadError(KnowledgeStoreError):
    """Raised when a historical version cannot be read."""
    exit_code = 52

    def __init__(self, collector_name: str, version: int) -> None:
        super().__init__(f"Cannot read history for '{collector_name}' version {version}")
        self.collector_name = collector_name
        self.version = version


# ── EventBus errors ─────────────────────────────────────────────────────────

class EventBusError(AICollectorError):
    """Raised on EventBus internal errors."""
    exit_code = 60
    _is_blocking = False
