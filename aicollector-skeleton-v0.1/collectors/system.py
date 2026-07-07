"""System collector: hostname, OS version, kernel, uptime."""
from __future__ import annotations

import platform
import time
from pathlib import Path

from collectors.base import (
    BaseCollector,
    CollectorResult,
    CollectorCapabilities,
    SystemAdapter,
    register_collector,
)


@register_collector("system")
class SystemCollector(BaseCollector):
    """Collect system identity and basic OS information."""

    name = "system"
    schema_version = "1.0"
    collector_version = "1.0.0"
    requires_root = False
    timeout_seconds = 10

    def collect(self, system: SystemAdapter) -> CollectorResult:
        """Collect hostname, OS release, kernel and uptime."""
        start = time.monotonic()
        errors: list[dict] = []

        data: dict = {}
        data["hostname"] = platform.node()
        data["domain"] = None

        # OS info
        try:
            os_release = Path("/etc/os-release")
            if os_release.exists():
                lines = os_release.read_text().splitlines()
                for line in lines:
                    if "=" in line:
                        key, _, value = line.partition("=")
                        value = value.strip("\"'").replace('"', "")
                        if key == "NAME":
                            data["os_name"] = value
                        elif key == "VERSION_ID":
                            data["os_version"] = value
        except OSError as exc:
            errors.append({"type": "os_release_read_error", "detail": str(exc)})

        data["kernel"] = platform.system()
        data["kernel_release"] = platform.release()
        data["architecture"] = platform.machine()

        try:
            uptime_secs = int(Path("/proc/uptime").read_text().split()[0])
            data["uptime_seconds"] = uptime_secs
        except (OSError, IndexError, ValueError) as exc:
            errors.append({"type": "uptime_read_error", "detail": str(exc)})
            data["uptime_seconds"] = 0

        return CollectorResult(
            data=data,
            errors=errors,
            execution_time_ms=(time.monotonic() - start) * 1000,
            raw_output=None,
        )

    def capabilities(self) -> CollectorCapabilities:
        return CollectorCapabilities(
            supported_platforms=["linux"],
            min_confidence=0.99,
            known_inconsistencies=[
                "domain field is null if DNS domain not configured",
            ],
        )
