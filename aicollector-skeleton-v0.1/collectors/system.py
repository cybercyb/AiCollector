#!/usr/bin/env python3
"""System collector: hostname, OS version, kernel, architecture and uptime."""
from __future__ import annotations

import platform
import socket
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
    collector_version = "1.0.1"
    requires_root = False
    timeout_seconds = 10

    def collect(self, system: SystemAdapter) -> CollectorResult:
        """Collect hostname, OS release, kernel details, architecture and uptime."""
        start = time.monotonic()
        errors: list[dict] = []
        data: dict = {}

        # Identité réseau de l'hôte
        hostname = platform.node()
        data["hostname"] = hostname
        
        # Résolution propre du domaine DNS (FQDN)
        try:
            fqdn = socket.getfqdn()
            if fqdn and fqdn != hostname and "." in fqdn:
                data["domain"] = fqdn.partition(".")[2]
            else:
                data["domain"] = None
        except Exception:
            data["domain"] = None

        # Informations du Système d'Exploitation (OS)
        os_name = "Linux"
        os_version = "unknown"
        
        try:
            # Utilisation de la méthode native Python 3.10+ (standard Freedesktop)
            os_data = platform.freedesktop_os_release()
            os_name = os_data.get("NAME", "Linux")
            os_version = os_data.get("VERSION_ID", os_data.get("VERSION", "unknown"))
        except (AttributeError, OSError):
            # Fallback manuel si freedesktop_os_release() échoue ou n'est pas disponible
            try:
                os_release_path = Path("/etc/os-release")
                if os_release_path.exists():
                    for line in os_release_path.read_text().splitlines():
                        if "=" in line:
                            key, _, value = line.partition("=")
                            clean_value = value.strip("\"'").replace('"', "")
                            if key == "NAME":
                                os_name = clean_value
                            elif key == "VERSION_ID":
                                os_version = clean_value
            except OSError as exc:
                errors.append({"type": "os_release_read_error", "detail": str(exc)})

        data["os_name"] = os_name
        data["os_version"] = os_version
        data["kernel"] = platform.system()
        data["kernel_release"] = platform.release()
        data["architecture"] = platform.machine()

        # Lecture de l'uptime du système
        uptime_seconds = 0
        try:
            uptime_file = Path("/proc/uptime")
            if uptime_file.exists():
                parts = uptime_file.read_text().split()
                if parts:
                    uptime_seconds = int(float(parts[0]))
            else:
                errors.append({"type": "missing_uptime_file", "detail": "/proc/uptime not found"})
        except (OSError, IndexError, ValueError) as exc:
            errors.append({"type": "uptime_read_error", "detail": str(exc)})

        data["uptime_seconds"] = uptime_seconds

        return CollectorResult(
            data=data,
            errors=errors,
            execution_time_ms=(time.monotonic() - start) * 1000,
            raw_output=None,
        )

    def capabilities(self) -> CollectorCapabilities:
        """Return capabilities and known constraints of this collector."""
        return CollectorCapabilities(
            supported_platforms=["linux"],
            min_confidence=0.99,
            known_inconsistencies=[
                "domain field may be null if DNS/FQDN is not configured in local resolver",
                "os_version fallback parsing is used if freedesktop_os_release helper is unavailable"
            ],
        )
