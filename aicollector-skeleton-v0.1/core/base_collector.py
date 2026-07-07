"""Abstract base collector and in-transit dataclasses."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

if False:
    from core.system_adapter import SystemAdapter


class Severity(StrEnum):
    """Change severity classification."""
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class CollectorResult:
    """Result returned by a collector's ``collect()`` method."""
    data: dict[str, Any]
    errors: list[dict[str, Any]]
    execution_time_ms: float
    raw_output: str | None = None


@dataclass(frozen=True, slots=True)
class CollectorCapabilities:
    """Collector capability description."""
    supported_platforms: list[str]
    min_confidence: float
    known_inconsistencies: list[str] = field(default_factory=list)


class BaseCollector(ABC):
    """Abstract base class that all collectors must implement.

    Collectors are auto-discovered and registered at startup via
    ``@register_collector``.
    """

    name: ClassVar[str] = "base"
    schema_version: ClassVar[str] = "1.0"
    collector_version: ClassVar[str] = "1.0.0"
    requires_root: ClassVar[bool] = False
    timeout_seconds: ClassVar[int] = 30

    @abstractmethod
    def collect(self, system: "SystemAdapter") -> CollectorResult:
        """Collect raw system data via the SystemAdapter.

        Args:
            system: Injected SystemAdapter instance.

        Returns:
            CollectorResult with raw data, non-blocking errors, and timing info.
        """
        ...  # pragma: no cover

    def capabilities(self) -> CollectorCapabilities:
        """Return the capabilities and known limitations of this collector."""
        return CollectorCapabilities(
            supported_platforms=["linux"],
            min_confidence=0.95,
            known_inconsistencies=[],
        )

    def classify_change(
        self,
        path: str,
        old_value: Any,
        new_value: Any,
    ) -> Severity:
        """Classify the severity of a detected change.

        Default implementation always returns Severity.INFO.
        Override to provide collector-specific severity logic.
        """
        return Severity.INFO
