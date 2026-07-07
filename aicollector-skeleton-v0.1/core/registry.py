"""Dynamic collector registry with auto-discovery via pkgutil."""
from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from core.exceptions import CollectorNotFoundError

if TYPE_CHECKING:
    from core.base_collector import BaseCollector


# Module-level registry populated by @register_collector decorators
_registered: dict[str, type["BaseCollector"]] = {}


def register_collector(name: str):
    """Decorator that registers a BaseCollector subclass."""
    def decorator(cls: type["BaseCollector"]) -> type["BaseCollector"]:
        cls.name = name  # type: ignore[attr-defined]
        _registered[name] = cls
        return cls
    return decorator


class Registry:
    """Singleton registry managing collector discovery and access."""

    _discovered: bool = False

    @classmethod
    def discover(cls, collectors_dir: Path | None = None) -> None:
        """Auto-discover all collectors in the collectors package.

        Args:
            collectors_dir: Explicit path to the collectors directory.
                Defaults to ``collectors/`` relative to this module.
        """
        if cls._discovered:
            return

        if collectors_dir is None:
            this_file = Path(__file__).resolve()
            collectors_dir = this_file.parent.parent / "collectors"

        collectors_path = str(collectors_dir.parent)
        if collectors_path not in sys.path:
            sys.path.insert(0, collectors_path)

        for importer, name, ispkg in pkgutil.iter_modules([str(collectors_dir)]):
            if ispkg or name.startswith("_"):
                continue
            try:
                # Use importlib for Python 3.12+ compatibility
                importlib.import_module(f"collectors.{name}")
            except Exception as exc:
                import logging
                logging.getLogger("aicollector").warning(
                    "Failed to load collector module '%s': %s", name, exc
                )
        cls._discovered = True

    @classmethod
    def register(cls, name: str, collector_cls: type["BaseCollector"]) -> None:
        """Manually register a collector class (called by ``@register_collector``)."""
        _registered[name] = collector_cls

    @classmethod
    def get_collector(cls, name: str) -> "BaseCollector":
        """Instantiate and return a collector by name."""
        if name not in _registered:
            raise CollectorNotFoundError(name, f"Collector '{name}' is not registered")
        return _registered[name]()

    @classmethod
    def list_collectors(cls) -> list[str]:
        """Return the sorted list of registered collector names."""
        return sorted(_registered.keys())

    @classmethod
    def reset(cls) -> None:
        """Clear the registry. Useful for testing."""
        _registered.clear()
        cls._discovered = False
