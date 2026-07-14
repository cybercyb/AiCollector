#!/usr/bin/env python3
"""RAM collector: memory and swap usage via /proc/meminfo."""
from __future__ import annotations

import time
from pathlib import Path

from collectors.base import (
    BaseCollector,
    CollectorResult,
    CollectorCapabilities,
    Severity,
    SystemAdapter,
    register_collector,
)


@register_collector("ram")
class RAMCollector(BaseCollector):
    """Collect physical memory and swap statistics from /proc/meminfo."""

    name = "ram"
    schema_version = "1.0"
    collector_version = "1.0.1"
    requires_root = False
    timeout_seconds = 10

    def collect(self, system: SystemAdapter) -> CollectorResult:
        """Parse /proc/meminfo and compute exact memory utilization."""
        start = time.monotonic()
        errors: list[dict] = []
        meminfo: dict[str, int] = {}

        try:
            # Lecture avec encodage explicite utf-8
            content = Path("/proc/meminfo").read_text(encoding="utf-8")
            for line in content.splitlines():
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                parts = value.strip().split()
                # Les valeurs de /proc/meminfo sont exprimées en kB (Kilooctets).
                # Nous convertissons en octets (bytes) -> * 1024.
                meminfo[key] = int(parts[0]) * 1024 if parts else 0
        except OSError as exc:
            errors.append({"type": "meminfo_read_error", "detail": str(exc)})

        total = meminfo.get("MemTotal", 0)
        free = meminfo.get("MemFree", 0)
        buffers = meminfo.get("Buffers", 0)
        cached = meminfo.get("Cached", 0) + meminfo.get("SReclaimable", 0)

        # Repli de calcul si MemAvailable n'est pas fourni par le noyau
        if "MemAvailable" in meminfo:
            available = meminfo["MemAvailable"]
        else:
            available = free + buffers + cached

        swap_total = meminfo.get("SwapTotal", 0)
        swap_free = meminfo.get("SwapFree", 0)
        
        # Calcul de l'utilisation réelle
        used = max(0, total - available)
        usage_pct = round(used / total * 100, 2) if total else 0.0

        return CollectorResult(
            data={
                "total_bytes": total,
                "available_bytes": available,
                "used_bytes": used,
                "free_bytes": free,
                "buffers_bytes": buffers,
                "cached_bytes": cached,
                "swap_total_bytes": swap_total,
                "swap_used_bytes": max(0, swap_total - swap_free),
                "swap_free_bytes": swap_free,
                "usage_percent": usage_pct,
            },
            errors=errors,
            execution_time_ms=(time.monotonic() - start) * 1000,
            raw_output=None,
        )

    def capabilities(self) -> CollectorCapabilities:
        """Return capabilities and limitations for the RAM collector."""
        return CollectorCapabilities(
            supported_platforms=["linux"],
            min_confidence=0.99,
            known_inconsistencies=[
                "MemAvailable might be approximated using active/inactive buffers on older kernels"
            ],
        )

    def classify_change(
        self, path: str, old_value: object, new_value: object
    ) -> Severity:
        """Classify severity of memory usage changes."""
        if "usage_percent" in path:
            try:
                val = float(str(new_value))
                if val >= 95.0:
                    return Severity.CRITICAL
                if val >= 90.0:
                    return Severity.WARNING
            except (ValueError, TypeError):
                pass
        return Severity.INFO
