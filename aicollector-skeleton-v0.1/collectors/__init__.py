"""Collector package — auto-discovery via pkgutil + @register_collector."""
from __future__ import annotations

# Re-export the decorator so every collector module can use it directly.
from core.registry import register_collector
from core.base_collector import BaseCollector

__all__ = ["register_collector", "BaseCollector"]
