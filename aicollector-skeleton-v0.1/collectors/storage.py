"""Storage collector: mounted filesystems via df and lsblk."""
from __future__ import annotations

import platform
import time

from collectors.base import BaseCollector, CollectorResult, CollectorCapabilities, Severity, SystemAdapter, register_collector  # noqa: E501


@register_collector("storage")
class StorageCollector(BaseCollector):
    """Collect mounted filesystem information via df."""

    name = "storage"
    schema_version = "1.0"
    collector_version = "1.0.0"
    requires_root = False
    timeout_seconds = 20

    def collect(self, system: SystemAdapter) -> CollectorResult:
        """Collect df output and parse mount points."""
        start = time.monotonic()
        errors: list[dict] = []

        result = system.run_command("df", ["-h", "--output=source,target,fstype,size,used,avail,pcent"])
        mounts: list[dict] = []
        total_devices = 0

        if result.returncode != 0:
            errors.append({"type": "df_error", "detail": result.stderr})
        else:
            lines = result.stdout.strip().split("\n")[1:]
            for line in lines:
                parts = [p for p in line.split() if p]
                if len(parts) < 7:
                    continue
                try:
                    usage_pct = int(parts[6].rstrip("%"))
                    total_devices += 1
                    mounts.append({
                        "device": parts[0],
                        "mountpoint": parts[1],
                        "fstype": parts[2],
                        "size": parts[3],
                        "used": parts[4],
                        "available": parts[5],
                        "usage_percent": usage_pct,
                    })
                except (ValueError, IndexError) as exc:
                    errors.append({"type": "parse_error", "line": line, "detail": str(exc)})

        return CollectorResult(
            data={"mounts": mounts, "total_devices": total_devices},
            errors=errors,
            execution_time_ms=(time.monotonic() - start) * 1000,
            raw_output=result.stdout if result.returncode == 0 else None,
        )

    def capabilities(self) -> CollectorCapabilities:
        return CollectorCapabilities(
            supported_platforms=["linux"],
            min_confidence=0.98,
            known_inconsistencies=[
                "tmpfs and devtmpfs are included",
                "size/used/available are human-readable strings",
            ],
        )
