"""Collector for open/listening network ports."""
from __future__ import annotations

import re
import logging
from typing import Any, ClassVar

from core.base_collector import BaseCollector, Severity
from core.system_adapter import SystemAdapter
from core.exceptions import CommandExecutionError
from core.registry import register_collector, CollectorResult, CollectorCapabilities

logger = logging.getLogger("aicollector")


@register_collector("ports")
class PortsCollector(BaseCollector):
    """Collects listening TCP and UDP ports on the system."""

    name: ClassVar[str] = "ports"
    schema_version: ClassVar[str] = "1.0"
    collector_version: ClassVar[str] = "1.0.0"
    requires_root: ClassVar[bool] = False  # Préférable de tourner sans root, les noms de process seront juste absents
    timeout_seconds: ClassVar[int] = 15

    def capabilities(self) -> CollectorCapabilities:
        return CollectorCapabilities(
            supported_platforms=["linux"],
            min_confidence=0.95,
            known_inconsistencies=[
                "Process names and PIDs require root privileges to be collected by 'ss'.",
            ],
        )

    def collect(self, system: SystemAdapter) -> CollectorResult:
        errors: list[Exception] = []
        open_ports: list[dict[str, Any]] = []

        try:
            # -t (TCP), -u (UDP), -l (Listening), -p (Show processes), -n (Numeric ports)
            result = system.run_command(["ss", "-tulpn"])
            stdout = result.stdout
            
            # Analyse des lignes de 'ss'
            # Exemple d'en-tête et de ligne :
            # Netid  State      Recv-Q Send-Q  Local Address:Port   Peer Address:Port
            # tcp    LISTEN     0      128     127.0.0.1:53         0.0.0.0:*      users:(("named",pid=1234,fd=20))
            lines = stdout.strip().splitlines()
            if lines:
                # Écarter la ligne d'en-tête
                for line in lines[1:]:
                    parts = line.split()
                    if len(parts) < 5:
                        continue

                    netid = parts[0].lower()
                    # On se concentre uniquement sur TCP et UDP listening
                    if "tcp" not in netid and "udp" not in netid:
                        continue

                    protocol = "tcp" if "tcp" in netid else "udp"
                    local_addr_port = parts[4]

                    # Extraction IP et Port (format: IP:Port ou [IPv6]:Port)
                    try:
                        if "]:" in local_addr_port:  # IPv6
                            addr_part, port_part = local_addr_port.rsplit(":", 1)
                            address = addr_part.strip("[]")
                        else:  # IPv4
                            address, port_part = local_addr_port.rsplit(":", 1)
                        
                        # Remplacement des jokers d'adresse ss (* par 0.0.0.0 ou ::)
                        if address == "*":
                            address = "0.0.0.0"

                        port = int(port_part)
                    except ValueError:
                        continue

                    # Tenter d'extraire le processus et le PID
                    process_name = None
                    pid = None
                    
                    if len(parts) >= 6:
                        users_part = parts[5]
                        # Format attendu : users:(("named",pid=1234,fd=20))
                        # Regex robuste pour capturer le premier nom de processus et le PID
                        match = re.search(r'users:\(\("([^"]+)"(?:,[^)]*)?pid=(\d+)', users_part)
                        if match:
                            process_name = match.group(1)
                            pid = int(match.group(2))

                    open_ports.append({
                        "protocol": protocol,
                        "local_address": address,
                        "port": port,
                        "process": process_name,
                        "pid": pid,
                    })

        except CommandExecutionError as exc:
            logger.error("Failed to execute 'ss' command: %s", exc)
            errors.append(exc)
        except Exception as exc:
            logger.error("Unexpected error in PortsCollector: %s", exc)
            errors.append(exc)

        # Trier par protocole puis par numéro de port pour assurer un JSON déterministe
        open_ports.sort(key=lambda x: (x["protocol"], x["port"], x["local_address"]))

        data = {
            "open_ports": open_ports,
            "total_open_ports": len(open_ports),
        }

        return CollectorResult(
            data=data,
            errors=errors,
        )

    def classify_change(
        self,
        path: str,
        old_value: Any,
        new_value: Any,
    ) -> Severity:
        """Classify new open ports as WARNING to alert the security team."""
        # Si un port est ajouté ou modifié, on lève une alerte WARNING
        if "open_ports" in path:
            return Severity.WARNING
        return Severity.INFO
