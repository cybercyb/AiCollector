#!/usr/bin/env python3
"""APT and DPKG package collector: list installed software packages and versions."""
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


@register_collector("apt")
class APTCollector(BaseCollector):
    """Collect details on all currently installed APT/DPKG packages."""

    name = "apt"
    schema_version = "1.0"
    collector_version = "1.0.0"
    requires_root = False
    timeout_seconds = 40

    def collect(self, system: SystemAdapter) -> CollectorResult:
        """Query the dpkg database for installed packages using formatted dpkg-query."""
        start = time.monotonic()
        errors: list[dict] = []
        packages: list[dict[str, Any]] = []

        # Utilisation de dpkg-query avec un formatage strict séparé par des tabulations
        # ${db:Status-Status} permet de filtrer uniquement les paquets 'installed'
        query_format = "${Package}\\t${Version}\\t${Architecture}\\t${Installed-Size}\\t${db:Status-Status}\\t${Summary}\\n"
        
        result = system.run_command(
            "dpkg-query", 
            ["--show", f"--showformat={query_format}"]
        )

        if result.returncode != 0:
            errors.append({
                "type": "dpkg_query_error",
                "returncode": result.returncode,
                "detail": result.stderr.strip()
            })
        else:
            lines = result.stdout.strip().splitlines()
            for line in lines:
                if not line:
                    continue
                
                parts = line.split("\t")
                if len(parts) < 5:
                    continue

                name = parts[0]
                version = parts[1]
                arch = parts[2]
                installed_size_kb_str = parts[3]
                status = parts[4]
                # Le résumé (summary) peut être vide, on gère l'IndexError potentiel
                summary = parts[5] if len(parts) > 5 else ""

                # Filtrage strict : on ne garde que les paquets installés
                if status != "installed":
                    continue

                # Conversion sécurisée de la taille installée (en Ko)
                try:
                    size_kb = int(installed_size_kb_str) if installed_size_kb_str else 0
                    size_bytes = size_kb * 1024
                except ValueError:
                    size_bytes = 0

                packages.append({
                    "name": name,
                    "version": version,
                    "architecture": arch,
                    "size_bytes": size_bytes,
                    "summary": summary,
                })

        return CollectorResult(
            data={
                "packages": packages,
                "total_packages": len(packages)
            },
            errors=errors,
            execution_time_ms=(time.monotonic() - start) * 1000,
            raw_output=None,
        )

    def capabilities(self) -> CollectorCapabilities:
        """Return capabilities and limitations for the APT collector."""
        return CollectorCapabilities(
            supported_platforms=["linux"],
            min_confidence=0.99,
            known_inconsistencies=[
                "only tracks packages managed by dpkg/apt (excludes snap, flatpak, manual binaries)",
                "size_bytes is calculated from Installed-Size field which is an approximation"
            ],
        )

    def classify_change(
        self, path: str, old_value: Any, new_value: Any
    ) -> Severity:
        """Classify change severity.

        Installs or removals of package versions are classified as WARNING
        to flag environment mutations to the agent.
        """
        # Si la liste de paquets change de taille ou qu'un paquet est modifié
        if "total_packages" in path:
            return Severity.WARNING
        return Severity.INFO
