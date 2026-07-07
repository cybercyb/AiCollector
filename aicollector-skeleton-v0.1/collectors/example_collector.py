"""Example collector — monitor_disk_usage.

This file serves as the canonical example of how to implement and register
a new collector.  Copy this template to create new collectors.

See SPEC.md section 10.1 for the full walkthrough.
"""
from __future__ import annotations

import time
from typing import Any

from collectors.base import BaseCollector, CollectorResult, CollectorCapabilities, Severity, SystemAdapter, register_collector  # noqa: E501


@register_collector("disk_usage")
class DiskUsageCollector(BaseCollector):
    """Example collector: disk usage per mount point via ``df``.

    This is a reference implementation demonstrating:
    - Decorator-based registration (``@register_collector``)
    - Use of ``SystemAdapter`` for all system calls
    - Proper error handling (non-blocking errors in CollectorResult.errors)
    - Timing measurement (``execution_time_ms``)
    - Capability declaration (``capabilities()``)
    - Severity classification (``classify_change()``)
    """

    name = "disk_usage"
    schema_version = "1.0"
    collector_version = "1.0.0"
    requires_root = False
    timeout_seconds = 20

    def collect(self, system: SystemAdapter) -> CollectorResult:
        """Collect disk usage statistics for all mount points."""
        start = time.monotonic()
        errors: list[dict] = []
        data: dict[str, Any] = {"mounts": []}

        result = system.run_command("df", ["-h", "--output=source,target,size,used,avail,pcent,fstype"])
        if result.returncode != 0:
            errors.append({"type": "command_error", "detail": result.stderr})
        else:
            lines = result.stdout.strip().split("\n")[1:]
            for line in lines:
                parts = [p for p in line.split() if p]
                if len(parts) >= 7:
                    try:
                        used_pct = int(parts[6].rstrip("%"))
                        data["mounts"].append({
                            "device": parts[0],
                            "mountpoint": parts[1],
                            "size": parts[2],
                            "used": parts[3],
                            "available": parts[4],
                            "usage_percent": used_pct,
                            "fstype": parts[7] if len(parts) > 7 else "unknown",
                        })
                    except (ValueError, IndexError):
                        errors.append({"type": "parse_error", "line": line})

        return CollectorResult(
            data=data,
            errors=errors,
            execution_time_ms=(time.monotonic() - start) * 1000,
            raw_output=result.stdout if result.returncode == 0 else None,
        )

    def capabilities(self) -> CollectorCapabilities:
        return CollectorCapabilities(
            supported_platforms=["linux"],
            min_confidence=0.95,
            known_inconsistencies=[
                "df output may vary across distributions",
                "tmpfs and devtmpfs mounts are included",
            ],
        )

    def classify_change(
        self, path: str, old_value: Any, new_value: Any
    ) -> Severity:
        """Alarm on high disk usage (> 90%)."""
        if "usage_percent" in path:
            try:
                new_pct = int(new_value) if isinstance(new_value, int) else int(str(new_value).rstrip("%"))
                if new_pct >= 95:
                    return Severity.CRITICAL
                elif new_pct >= 90:
                    return Severity.WARNING
            except (ValueError, TypeError):
                pass
        return Severity.INFO
