#!/usr/bin/env python3
"""Storage collector: mounted filesystems via df."""
from __future__ import annotations

import time
from typing import Any

from collectors.base import (
    BaseCollector,
    CollectorResult,
    CollectorCapabilities,
    Severity,
    SystemAdapter,
    register_collector,
)


@register_collector("storage")
class StorageCollector(BaseCollector):
    """Collect mounted filesystem information via df in exact bytes."""

    name = "storage"
    schema_version = "1.0"
    collector_version = "1.0.1"
    requires_root = False
    timeout_seconds = 20

    def collect(self, system: SystemAdapter) -> CollectorResult:
        """Collect df output and parse mount points matching the system schema."""
        start = time.monotonic()
        errors: list[dict] = []
        mounts: list[dict[str, Any]] = []
        total_devices = 0

        # --block-size=1 (ou -B1) force l'affichage en octets bruts pour satisfaire le schéma Pydantic
        result = system.run_command(
            "df", 
            ["--block-size=1", "--output=source,target,fstype,size,used,avail,pcent"]
        )

        if result.returncode != 0:
            errors.append({"type": "df_error", "detail": result.stderr})
        else:
            lines = result.stdout.strip().split("\n")[1:]
            for line in lines:
                parts = [p for p in line.split() if p]
                # Au moins 7 colonnes requises
                if len(parts) < 7:
                    continue
                try:
                    # Extraction robuste par indices négatifs pour supporter les espaces dans les chemins de montages
                    device = parts[0]
                    fstype = parts[-5]
                    
                    # Conversion brute des blocs en entiers
                    size_bytes = int(parts[-4])
                    used_bytes = int(parts[-3])
                    available_bytes = int(parts[-2])
                    
                    # Nettoyage et conversion de l'utilisation en float
                    usage_percent = float(parts[-1].rstrip("%"))

                    # Reconstruction du point de montage s'il contenait des espaces
                    mountpoint = " ".join(parts[1:-5]) if len(parts) > 7 else parts[1]

                    total_devices += 1
                    mounts.append({
                        "device": device,
                        "mountpoint": mountpoint,
                        "fstype": fstype,
                        "size_bytes": size_bytes,
                        "used_bytes": used_bytes,
                        "available_bytes": available_bytes,
                        "usage_percent": usage_percent,
                    })
                except (ValueError, IndexError) as exc:
                    errors.append({
                        "type": "parse_error", 
                        "line": line, 
                        "detail": str(exc)
                    })

        return CollectorResult(
            data={"mounts": mounts, "total_devices": total_devices},
            errors=errors,
            execution_time_ms=(time.monotonic() - start) * 1000,
            raw_output=result.stdout if result.returncode == 0 else None,
        )

    def capabilities(self) -> CollectorCapabilities:
        """Return capabilities and known limits for this collector."""
        return CollectorCapabilities(
            supported_platforms=["linux"],
            min_confidence=0.98,
            known_inconsistencies=[
                "virtual filesystems (tmpfs, devtmpfs, cgroup) are listed by df",
                "bind mounts may report identical size and usage as their source filesystem",
            ],
        )

    def classify_change(
        self, path: str, old_value: object, new_value: object
    ) -> Severity:
        """Classify severity of disk space changes. Triggers warning/critical on high usage."""
        if "usage_percent" in path:
            try:
                new_pct = float(str(new_value).rstrip("%"))
                if new_pct >= 95.0:
                    return Severity.CRITICAL
                if new_pct >= 90.0:
                    return Severity.WARNING
            except (ValueError, TypeError):
                pass
        return Severity.INFO
