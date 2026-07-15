#!/usr/bin/env python3
"""CPU collector: model, cores, flags, load, usage."""
from __future__ import annotations

import os
import platform
import time
from pathlib import Path

from collectors.base import BaseCollector, CollectorResult, CollectorCapabilities, Severity, SystemAdapter, register_collector


@register_collector("cpu")
class CPUCollector(BaseCollector):
    """Collect CPU model, topology, flags, load averages and usage."""

    name = "cpu"
    schema_version = "1.0"
    collector_version = "1.0.1"
    requires_root = False
    timeout_seconds = 15

    def collect(self, system: SystemAdapter) -> CollectorResult:
        """Collect CPU information from /proc/cpuinfo and /proc/stat."""
        start = time.monotonic()
        errors: list[dict] = []
        data: dict = {}

        # 1. Parsing global de /proc/cpuinfo pour le modèle et la topologie physique
        cores_physical = 0
        model_name = "unknown"
        cpu_flags: list[str] = []
        
        try:
            cpuinfo_content = Path("/proc/cpuinfo").read_text(encoding="utf-8")
            
            # Extraction du modèle (première occurrence)
            for line in cpuinfo_content.splitlines():
                if line.startswith("model name"):
                    model_name = line.split(":", 1)[1].strip()
                    break
                    
            # Extraction des flags (première occurrence)
            for line in cpuinfo_content.splitlines():
                if line.startswith("flags"):
                    cpu_flags = line.split(":", 1)[1].strip().split()
                    break

            # Calcul des cœurs physiques uniques (somme de "cpu cores" par "physical id")
            physical_processors: dict[str, int] = {}
            current_phys_id = None
            current_cores = None
            
            for line in cpuinfo_content.splitlines():
                if line.startswith("physical id"):
                    current_phys_id = line.split(":", 1)[1].strip()
                elif line.startswith("cpu cores"):
                    current_cores = int(line.split(":", 1)[1].strip())
                    
                if current_phys_id is not None and current_cores is not None:
                    physical_processors[current_phys_id] = current_cores
                    current_phys_id = None
                    current_cores = None
            
            cores_physical = sum(physical_processors.values()) if physical_processors else 0

        except OSError as exc:
            errors.append({"type": "cpuinfo_read_error", "detail": str(exc)})

        # 2. Données d'identité de base
        data["model"] = model_name
        data["architecture"] = platform.machine()
        data["cpu_flags"] = cpu_flags

        # 3. Topologie des cœurs
        cores_logical = os.cpu_count() or 0
        if cores_physical == 0:
            # Fallback si l'extraction par physical_id a échoué (ex: VM monocœur)
            cores_physical = cores_logical

        data["cores_logical"] = cores_logical
        data["cores_physical"] = cores_physical
        
        # Threads par cœur
        if cores_physical > 0:
            data["threads_per_core"] = round(cores_logical / cores_physical, 2)
        else:
            data["threads_per_core"] = 1.0

        # 4. Fréquence actuelle et maximale du CPU
        freq_mhz: float | None = None
        try:
            freq_path = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
            if freq_path.exists():
                freq_khz = int(freq_path.read_text(encoding="utf-8").strip())
                freq_mhz = round(freq_khz / 1000, 2)
        except (OSError, ValueError):
            pass
        data["frequency_mhz"] = freq_mhz
        data["frequency_max_mhz"] = freq_mhz  # placeholder

        # 5. Charge moyenne du système (Load Averages)
        try:
            loadavg = Path("/proc/loadavg").read_text(encoding="utf-8").split()
            data["load_average_1m"] = float(loadavg[0])
            data["load_average_5m"] = float(loadavg[1])
            data["load_average_15m"] = float(loadavg[2])
        except (OSError, IndexError, ValueError) as exc:
            errors.append({"type": "loadavg_read_error", "detail": str(exc)})
            data["load_average_1m"] = 0.0
            data["load_average_5m"] = 0.0
            data["load_average_15m"] = 0.0

        # Initialisation par défaut de métriques étendues (non requises dans le MVP)
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
