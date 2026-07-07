"""Shared import alias for collector modules.

All collectors import from here so their import graph is stable
regardless of where the core package is installed.
"""
from core.base_collector import (
    BaseCollector,
    CollectorResult,
    CollectorCapabilities,
    Severity,
)
from core.system_adapter import SystemAdapter
from core.registry import register_collector

__all__ = ["BaseCollector", "CollectorResult", "CollectorCapabilities", "Severity", "SystemAdapter", "register_collector"]
