"""
AICollector — Configuration Loader & Validator (Pydantic v2)
"""
from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, FieldValidationInfo, field_validator, model_validator
import yaml

from core.exceptions import ConfigError


class LoggingLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class RootBehavior(str, Enum):
    SKIP = "skip"
    WARN = "warn"
    FAIL = "fail"


class RetentionConfig(BaseModel):
    history_versions: int = Field(default=50, gt=0, description="Max snapshots to retain per collector")
    changes_entries: int = Field(default=200, gt=0, description="Max change entries in history")
    logs_days: int = Field(default=30, gt=0, description="Log retention in days")


class SchedulerConfig(BaseModel):
    frequency_cron: str = Field(default="0 */2 * * *")
    use_systemd_timer: bool = Field(default=False)

    @field_validator("frequency_cron")
    @classmethod
    def validate_cron(cls, v: str) -> str:
        # Simple validation: checks that it has at least 5 parts
        parts = v.strip().split()
        if len(parts) < 5 or len(parts) > 6:
            raise ValueError("Cron expression must consist of 5 (or 6) space-separated fields")
        return v


class CollectorsConfig(BaseModel):
    enabled: List[str] = Field(default_factory=list)
    disabled: List[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=30, gt=0)
    parallel: bool = Field(default=False)
    root_required_behavior: RootBehavior = Field(default=RootBehavior.SKIP)


class SecurityConfig(BaseModel):
    allowed_commands: List[str] = Field(default_factory=list)
    exclude_paths: List[Path] = Field(default_factory=list)
    redact_patterns: List[str] = Field(default_factory=list)

    @field_validator("redact_patterns")
    @classmethod
    def validate_regex_patterns(cls, v: List[str]) -> List[str]:
        for pattern in v:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"Invalid regular expression pattern '{pattern}': {e}")
        return v


class PathsConfig(BaseModel):
    base_dir: Path = Field(default=Path("/var/lib/aicollector"))
    config_dir: Path = Field(default=Path("/etc/aicollector"))
    cache_dir: Path = Field(default=Path("/var/cache/aicollector"))
    log_dir: Path = Field(default=Path("/var/log/aicollector"))
    lockfile_path: Path = Field(default=Path("/run/aicollector/aicollector.lock"))
    knowledge_subdir: Path = Field(default=Path("knowledge"))
    history_subdir: Path = Field(default=Path("history"))
    changes_subdir: Path = Field(default=Path("changes"))
    cache_subdir: Path = Field(default=Path("cache"))


class AICollectorConfig(BaseModel):
    server_uuid: Optional[UUID] = Field(default=None)
    logging_level: LoggingLevel = Field(default=LoggingLevel.INFO)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    collectors: CollectorsConfig = Field(default_factory=CollectorsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)

    @model_validator(mode="before")
    @classmethod
    def handle_dev_mode_overrides(cls, data: Any) -> Any:
        """
        Intercepte la configuration avant sa validation Pydantic.
        Si la variable d'environnement AICOLLECTOR_ROOT est active,
        tous les chemins FHS de production sont traduits vers l'arborescence locale.
        """
        if not isinstance(data, dict):
            return data

        env_root = os.environ.get("AICOLLECTOR_ROOT")
        if env_root:
            root_path = Path(env_root).resolve()
            paths_data = data.get("paths", {})
            if not isinstance(paths_data, dict):
                paths_data = {}

            # Redirection dynamique vers la racine de développement locale
            paths_data["base_dir"] = str(root_path / "data")
            paths_data["config_dir"] = str(root_path)
            paths_data["cache_dir"] = str(root_path / "cache")
            paths_data["log_dir"] = str(root_path / "logs")
            paths_data["lockfile_path"] = str(root_path / "data" / "aicollector.lock")

            data["paths"] = paths_data

        return data

    @classmethod
    def from_yaml(cls, path: Path) -> AICollectorConfig:
        """
        Charge et valide le fichier YAML de configuration.
        """
        try:
            with path.open("r", encoding="utf-8") as f:
                content = yaml.safe_load(f) or {}
            return cls(**content)
        except FileNotFoundError:
            raise ConfigError(f"Configuration file not found: {path}", exit_code=10)
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML syntax in {path}: {exc}", exit_code=10)
        except Exception as exc:
            raise ConfigError(f"Configuration validation failed: {exc}", exit_code=10)
