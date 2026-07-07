"""Pydantic-powered configuration loader with dev/prod path resolution."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

try:
    from pydantic import BaseModel, Field, ConfigDict
except ImportError as exc:
    raise ImportError("pydantic is required: pip install pydantic pyyaml") from exc

try:
    import yaml
except ImportError as exc:
    raise ImportError("pyyaml is required: pip install pyyaml") from exc

from core.exceptions import ConfigFileNotFoundError, ConfigValidationError


class RetentionConfig(BaseModel):
    """Retention configuration for history, changes and logs."""
    model_config = ConfigDict(extra="forbid")

    history_versions: int = Field(default=50, ge=1, le=1000)
    changes_entries: int = Field(default=200, ge=1)
    logs_days: int = Field(default=30, ge=1)


class PathsConfig(BaseModel):
    """FHS path configuration, resolved via AICOLLECTOR_ROOT in dev mode."""
    model_config = ConfigDict(extra="forbid")

    base_dir: Path = Path("/var/lib/aicollector")
    config_dir: Path = Path("/etc/aicollector")
    cache_dir: Path = Path("/var/cache/aicollector")
    log_dir: Path = Path("/var/log/aicollector")
    lockfile_path: Path = Path("/run/aicollector/aicollector.lock")
    knowledge_subdir: str = "knowledge"
    history_subdir: str = "history"
    changes_subdir: str = "changes"
    cache_subdir: str = "cache"


class SchedulerConfig(BaseModel):
    """Scheduler configuration (cron or systemd timer)."""
    model_config = ConfigDict(extra="forbid")

    frequency_cron: str = "0 */2 * * *"
    use_systemd_timer: bool = False
    systemd_unit_dir: Path = Path("/etc/systemd/system")


class CollectorsConfig(BaseModel):
    """Collectors runtime configuration."""
    model_config = ConfigDict(extra="forbid")

    enabled: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=30, ge=1, le=3600)
    parallel: bool = False
    root_required_behavior: Literal["skip", "warn", "fail"] = "skip"


class SecurityConfig(BaseModel):
    """Security configuration (whitelist and exclusions)."""
    model_config = ConfigDict(extra="forbid")

    allowed_commands: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    redact_patterns: list[dict] = Field(default_factory=list)


class AICollectorConfig(BaseModel):
    """Root configuration object validated by Pydantic."""
    model_config = ConfigDict(extra="forbid")

    server_uuid: str | None = None
    logging_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    collectors: CollectorsConfig = Field(default_factory=CollectorsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> AICollectorConfig:
        """Load and validate a YAML configuration file.

        Args:
            path: Path to ``config.yaml``.

        Returns:
            Validated ``AICollectorConfig`` instance.

        Raises:
            ConfigFileNotFoundError: If the file does not exist.
            ConfigValidationError: If validation fails.
        """
        if not path.exists():
            raise ConfigFileNotFoundError(str(path))
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigValidationError(str(exc)) from exc

        # Resolve dev-mode paths when AICOLLECTOR_ROOT is set
        if "AICOLLECTOR_ROOT" in os.environ or _is_dev_mode():
            raw = _resolve_dev_paths(raw)

        try:
            return cls.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            raise ConfigValidationError(str(exc)) from exc


def _is_dev_mode() -> bool:
    """Return True when production paths do not exist."""
    return not Path("/opt/aicollector").exists()


def _resolve_dev_paths(raw: dict) -> dict:
    """Redirect FHS paths to local directories under AICOLLECTOR_ROOT."""
    root = Path(os.environ.get("AICOLLECTOR_ROOT", ".")).resolve()
    raw.setdefault("paths", {})
    raw["paths"]["base_dir"] = str(root / "data")
    raw["paths"]["config_dir"] = str(root)
    raw["paths"]["cache_dir"] = str(root / "data" / "cache")
    raw["paths"]["log_dir"] = str(root / "logs")
    raw["paths"]["lockfile_path"] = str(root / "data" / "aicollector.lock")
    raw["paths"]["knowledge_subdir"] = "knowledge"
    raw["paths"]["history_subdir"] = "history"
    raw["paths"]["changes_subdir"] = "changes"
    raw["paths"]["cache_subdir"] = "cache"
    return raw
