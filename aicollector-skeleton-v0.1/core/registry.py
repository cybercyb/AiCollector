"""Dynamic collector registry with auto-discovery via pkgutil."""
from __future__ import annotations

import importlib
import logging
import pkgutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from core.exceptions import CollectorNotFoundError

if TYPE_CHECKING:
    from core.base_collector import BaseCollector

logger = logging.getLogger("aicollector")

# Registre global associant le nom d'un collecteur à sa classe
_registered: dict[str, type[BaseCollector]] = {}


def register_collector(name: str):
    """Decorator that registers a BaseCollector subclass.

    Args:
        name: Unique identifier for the collector (e.g., 'cpu', 'memory').
    """
    def decorator(cls: type[BaseCollector]) -> type[BaseCollector]:
        cls.name = name  # Force l'attribut de classe 'name'
        _registered[name] = cls
        return cls
    return decorator


class Registry:
    """Singleton registry managing collector discovery, validation, and access."""

    _discovered: ClassVar[bool] = False

    @classmethod
    def discover(cls, collectors_dir: Path | None = None) -> None:
        """Auto-discover all collectors in the collectors package.

        Discovers and imports modules dynamically using importlib.

        Args:
            collectors_dir: Explicit path to the collectors directory.
                Defaults to ``collectors/`` relative to the project root.
        """
        if cls._discovered:
            return

        if collectors_dir is None:
            this_file = Path(__file__).resolve()
            collectors_dir = this_file.parent.parent / "collectors"

        if not collectors_dir.exists() or not collectors_dir.is_dir():
            logger.warning(
                f"Collectors directory not found at '{collectors_dir}'. "
                "No dynamic collectors will be loaded."
            )
            cls._discovered = True
            return

        # Gestion propre de l'importation sans altérer globalement sys.path à l'index 0
        parent_dir = str(collectors_dir.parent)
        added_to_path = False
        if parent_dir not in sys.path:
            sys.path.append(parent_dir)  # Append plutôt que prepend pour éviter le shadowing
            added_to_path = True

        try:
            for _, name, ispkg in pkgutil.iter_modules([str(collectors_dir)]):
                if ispkg or name.startswith("_"):
                    continue

                module_fullname = f"collectors.{name}"
                try:
                    # Si le module a déjà été importé précédemment (ex: tests), on le force à se recharger
                    if module_fullname in sys.modules:
                        importlib.reload(sys.modules[module_fullname])
                    else:
                        importlib.import_module(module_fullname)
                except Exception as exc:
                    logger.error(
                        f"Failed to import collector module '{module_fullname}': {exc}",
                        exc_info=True,
                    )
        finally:
            # On nettoie sys.path si on l'a modifié pour éviter les effets de bord
            if added_to_path and parent_dir in sys.path:
                sys.path.remove(parent_dir)

        cls._discovered = True
        logger.info(
            f"Collector discovery complete. Registered collectors: {cls.list_collectors()}"
        )

    @classmethod
    def register(cls, name: str, collector_cls: type[BaseCollector]) -> None:
        """Manually register a collector class (primarily useful for mock testing)."""
        _registered[name] = collector_cls

    @classmethod
    def get_collector(cls, name: str) -> BaseCollector:
        """Instantiate and return a collector by name.

        Args:
            name: The registered name of the collector.

        Returns:
            An instance of the collector.

        Raises:
            CollectorNotFoundError: If no collector with this name is registered.
        """
        if name not in _registered:
            raise CollectorNotFoundError(
                name, f"Collector '{name}' is not registered in the system."
            )
        return _registered[name]()

    @classmethod
    def list_collectors(cls) -> list[str]:
        """Return the sorted list of registered collector names."""
        return sorted(_registered.keys())

    @classmethod
    def reset(cls) -> None:
        """Clear the registry and unload imported collector modules from cache.

        Crucial for isolation between unit tests.
        """
        # Nettoyage des modules importés de sys.modules pour permettre une ré-importation
        collectors_modules = [
            mod_name for mod_name in sys.modules if mod_name.startswith("collectors.")
        ]
        for mod_name in collectors_modules:
            sys.modules.pop(mod_name, None)

        _registered.clear()
        cls._discovered = False
