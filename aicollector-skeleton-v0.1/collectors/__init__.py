"""Collector package — auto-discovery via pkgutil + @register_collector."""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

# Re-export key components for collectors
from core.registry import register_collector
from core.base_collector import BaseCollector

__all__ = ["register_collector", "BaseCollector"]

# Auto-discover and import all modules inside this package.
# This forces the execution of @register_collector in each file.
_package_dir = str(Path(__file__).resolve().parent)
for _, module_name, is_pkg in pkgutil.iter_modules([_package_dir]):
    if not is_pkg and not module_name.startswith("_"):
        importlib.import_module(f"{__name__}.{module_name}")
