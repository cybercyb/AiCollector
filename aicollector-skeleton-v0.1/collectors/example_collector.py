"""Example collector — monitor_disk_usage.

This file serves as the canonical example of how to implement and register
a new collector. Copy this template to create new collectors.

See SPEC.md section 10.1 for the full walkthrough.
"""
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
    collector_version = "1.0.1"
    requires_root = False
    timeout_seconds = 20

    def collect(self, system: SystemAdapter) -> CollectorResult:
        """Collect disk usage statistics for all mount points."""
        start = time.monotonic()
        errors: list[dict] = []
        data: dict[str, Any] = {"mounts": []}

        # Commande sécurisée déléguée au SystemAdapter
        result = system.run_command(
            "df", 
            ["-h", "--output=source,target,size,used,avail,pcent,fstype"]
        )
        
        if result.returncode != 0:
            errors.append({
                "type": "command_error", 
                "detail": result.stderr or "df command execution failed"
            })
        else:
            lines = result.stdout.strip().split("\n")[1:]  # On saute l'en-tête
            for line in lines:
                if not line.strip():
                    continue
                
                parts = [p for p in line.split() if p]
                # df renvoie exactement 7 colonnes avec l'option --output demandée.
                # Si un nom de montage ou de périphérique contient un espace, parts aura plus de 7 éléments.
                if len(parts) >= 7:
                    try:
                        # La colonne pcent (pourcentage) est l'avant-dernière (index -2)
                        # La colonne fstype est la toute dernière (index -1)
                        raw_pct = parts[-2]
                        used_pct = int(raw_pct.rstrip("%"))
                        
                        # Reconstitution des chemins si le périphérique ou point de montage contenait un espace
                        device = parts[0]
                        fstype = parts[-1]
                        
                        # Point de montage intermédiaire (peut contenir des espaces)
                        mountpoint = " ".join(parts[1:-5])
                        
                        data["mounts"].append({
                            "device": device,
                            "mountpoint": mountpoint,
                            "size": parts[-5],
                            "used": parts[-4],
                            "available": parts[-3],
                            "usage_percent": used_pct,
                            "fstype": fstype,
                        })
                    except (ValueError, IndexError) as exc:
                        errors.append({
                            "type": "parse_error", 
                            "line": line, 
                            "detail": str(exc)
                        })
                else:
                    errors.append({
                        "type": "malformed_output_line", 
                        "line": line
                    })

        return CollectorResult(
            data=data,
            errors=errors,
            execution_time_ms=(time.monotonic() - start) * 1000,
            raw_output=result.stdout if result.returncode == 0 else None,
        )

    def capabilities(self) -> CollectorCapabilities:
        """Return capabilities and known limits for this collector."""
        return CollectorCapabilities(
            supported_platforms=["linux"],
            min_confidence=0.95,
            known_inconsistencies=[
                "df output formatting may vary slightly depending on coreutils version",
                "virtual filesystems (tmpfs, devtmpfs) are collected unless explicitly filtered",
            ],
        )

    def classify_change(
        self, path: str, old_value: Any, new_value: Any
    ) -> Severity:
        """Classify change severity. Trigger warning/critical alerts on high disk usage."""
        if "usage_percent" in path:
            try:
                new_pct = (
                    int(new_value) 
                    if isinstance(new_value, (int, float)) 
                    else int(str(new_value).rstrip("%"))
                )
                if new_pct >= 95:
                    return Severity.CRITICAL
                elif new_pct >= 90:
                    return Severity.WARNING
            except (ValueError, TypeError):
                pass
        return Severity.INFO
