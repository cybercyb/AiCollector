#!/usr/bin/env python3
"""RAM collector: memory and swap usage via /proc/meminfo."""
from __future__ import annotations

import platform
import time
from pathlib import Path
import time
from pathlib import Path

from collectors.base import BaseCollector, CollectorResult, CollectorCapabilities, Severity, SystemAdapter, register_collector  # noqa: E501


@register_collector("ram")
class RAMCollector(BaseCollector):
    """Collect physical memory and swap statistics."""

    name = "ram"
    schema_version = "1.0"
    collector_version = "1.0.0"
    requires_root = False
    timeout_seconds = 10

    def collect(self, system: SystemAdapter) -> CollectorResult:
        """Parse /proc/meminfo and compute memory utilisation."""
        start = time.monotonic()
        errors: list[dict] = []
        meminfo: dict[str, int] = {}

        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                parts = value.strip().split()
                meminfo[key] = int(parts[0]) * 1024 if parts else 0
        except OSError as exc:
            errors.append({"type": "meminfo_read_error", "detail": str(exc)})

        total = meminfo.get("MemTotal", 0)
        free = meminfo.get("MemFree", 0)
        available = meminfo.get("MemAvailable", free)
        buffers = meminfo.get("Buffers", 0)
        cached = meminfo.get("Cached", 0) + meminfo.get("SReclaimable", 0)
        swap_total = meminfo.get("SwapTotal", 0)
        swap_free = meminfo.get("SwapFree", 0)
        used = total - available

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
                "swap_used_bytes": swap_total - swap_free,
                "swap_free_bytes": swap_free,
                "usage_percent": usage_pct,
            },
            errors=errors,
            execution_time_ms=(time.monotonic() - start) * 1000,
            raw_output=None,
        )

    def capabilities(self) -> CollectorCapabilities:
        return CollectorCapabilities(
            supported_platforms=["linux"],
            min_confidence=0.99,
            known_inconsistencies=[],
        )
