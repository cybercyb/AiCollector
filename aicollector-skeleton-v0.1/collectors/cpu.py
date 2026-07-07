#!/usr/bin/env python3
"""CPU collector: model, cores, flags, load, usage."""
from __future__ import annotations

import os
import platform
import time
from pathlib import Path
from pathlib import Path

from collectors.base import BaseCollector, CollectorResult, CollectorCapabilities, Severity, SystemAdapter, register_collector  # noqa: E501


@register_collector("cpu")
class CPUCollector(BaseCollector):
    """Collect CPU model, topology, flags, load averages and usage."""

    name = "cpu"
    schema_version = "1.0"
    collector_version = "1.0.0"
    requires_root = False
    timeout_seconds = 15

    def collect(self, system: SystemAdapter) -> CollectorResult:
        """Collect CPU information from /proc/cpuinfo and /proc/stat."""
        start = time.monotonic()
        errors: list[dict] = []
        data: dict = {}

        # Model name
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text()
            model_name = ""
            for line in cpuinfo.splitlines():
                if line.startswith("model name"):
                    model_name = line.split(":", 1)[1].strip()
                    break
            data["model"] = model_name or "unknown"
        except OSError as exc:
            errors.append({"type": "cpuinfo_read_error", "detail": str(exc)})
            data["model"] = "unknown"

        data["architecture"] = platform.machine()
        data["cores_physical"] = os.cpu_count() or 0
        data["cores_logical"] = os.cpu_count(logical=True) or 0
        data["threads_per_core"] = round(
            (os.cpu_count(logical=True) or 0) / max(os.cpu_count() or 1, 1), 2
        )

        # Frequency
        freq_mhz: float | None = None
        try:
            freq_path = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
            if freq_path.exists():
                freq_khz = int(freq_path.read_text().strip())
                freq_mhz = round(freq_khz / 1000, 2)
        except (OSError, ValueError):
            pass
        data["frequency_mhz"] = freq_mhz
        data["frequency_max_mhz"] = freq_mhz  # placeholder

        # CPU flags
        try:
            flags_line = ""
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("flags"):
                    flags_line = line.split(":", 1)[1].strip()
                    break
            data["cpu_flags"] = flags_line.split() if flags_line else []
        except OSError:
            data["cpu_flags"] = []

        # Load averages
        try:
            loadavg = Path("/proc/loadavg").read_text().split()
            data["load_average_1m"] = float(loadavg[0])
            data["load_average_5m"] = float(loadavg[1])
            data["load_average_15m"] = float(loadavg[2])
        except (OSError, IndexError, ValueError) as exc:
            errors.append({"type": "loadavg_read_error", "detail": str(exc)})
            data["load_average_1m"] = 0.0
            data["load_average_5m"] = 0.0
            data["load_average_15m"] = 0.0

        data["usage_percent"] = None
        data["temperature_celsius"] = None

        return CollectorResult(
            data=data,
            errors=errors,
            execution_time_ms=(time.monotonic() - start) * 1000,
            raw_output=None,
        )

    def capabilities(self) -> CollectorCapabilities:
        return CollectorCapabilities(
            supported_platforms=["linux"],
            min_confidence=0.95,
            known_inconsistencies=[
                "frequency_mhz may be null if cpufreq not available",
                "temperature_celsius requires lm-sensors",
            ],
        )
