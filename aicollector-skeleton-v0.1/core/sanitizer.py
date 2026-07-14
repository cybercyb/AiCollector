"""Defence-in-depth secret redaction before JSON serialisation."""
from __future__ import annotations

import fnmatch
import re
import threading
from typing import Any, ClassVar

from core.event_bus import EventBus

# Motifs de détection des secrets avec groupes de capture
# Le dernier groupe de capture de chaque regex correspond à la valeur sensible à masquer
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("password", re.compile(r"(?i)(password|passwd|pwd)[\"'\\s:=]+[\"']?([^\"'\\s]+)")),
    ("api_key", re.compile(r"(?i)(api[_-]?key|apikey)[\"'\\s:=]+[\"']?([^\"'\\s]+)")),
    ("ssh_key", re.compile(r"(?i)(ssh[_-]?key|sshprivatekey)[\"'\\s:=]+[\"']?([^\"'\\s]+)")),
    ("token", re.compile(r"(?i)(bearer[_-]?token|auth[_-]?token|refresh[_-]?token)[\"'\\s:=]+[\"']?([^\"'\\s]+)")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE)),
    ("docker_auth", re.compile(r"(?i)(docker[_-]?auth|docker[_-]?config)[\"'\\s:=]+[\"']?([^\"'\\s]+)")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("credit_card", re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")),
    ("env_secret", re.compile(r"(?i)(SECRET|PASSWORD|API_KEY|TOKEN|PRIVATE)[\"\s]?=['\"]?([^\"'\s]+)")),
]

FORBIDDEN_PATHS: frozenset[str] = frozenset({
    "/etc/shadow",
    "/root/.ssh",
    "/root/.ssh/*",
    "/home/*/.ssh",
    "/home/*/.ssh/*",
})

REDACTED: str = "***REDACTED***"


class Sanitizer:
    """Recursively scan and redact secrets in data structures without losing diagnostics."""

    # Thread-local storage pour éviter les boucles d'événements infinies en cas de multi-threading
    _local: ClassVar[threading.local] = threading.local()

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus

    @property
    def _is_emitting(self) -> bool:
        """Détermine si le thread actuel est déjà en cours d'émission d'un événement de sécurité."""
        return getattr(self._local, "is_emitting", False)

    @_is_emitting.setter
    def _is_emitting(self, value: bool) -> None:
        self._local.is_emitting = value

    def sanitize(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return a deep copy of ``data`` with secrets and forbidden paths replaced.

        Args:
            data: Arbitrary dictionary to sanitize.

        Returns:
            Sanitized deep copy of the input dictionary.
        """
        visited: set[int] = set()
        return self._sanitize_value(data, visited)

    def _sanitize_value(self, value: Any, visited: set[int]) -> Any:
        # Protection contre l'analyse de structures cycliques (Infinite Loops)
        val_id = id(value)
        if val_id in visited:
            return REDACTED  # Rompt la référence circulaire de manière sûre

        if isinstance(value, dict):
            visited.add(val_id)
            try:
                return {k: self._sanitize_value(v, visited) for k, v in value.items()}
            finally:
                visited.discard(val_id)

        if isinstance(value, list):
            visited.add(val_id)
            try:
                return [self._sanitize_value(item, visited) for item in value]
            finally:
                visited.discard(val_id)

        if isinstance(value, str):
            return self._sanitize_string(value)

        return value

    def _sanitize_string(self, value: str) -> str:
        # 1. Traitement prioritaire des chemins système interdits (FORBIDDEN_PATHS)
        # S'applique si la chaîne ressemble à un chemin absolu ou contient un chemin interdit
        stripped_val = value.strip()
        if stripped_val.startswith("/"):
            for forbidden_pattern in FORBIDDEN_PATHS:
                if fnmatch.fnmatchcase(stripped_val, forbidden_pattern):
                    self._emit_redact_event("forbidden_path")
                    return REDACTED

        # 2. Substitution ciblée des secrets par regex
        modified_value = value
        for secret_type, pattern in SECRET_PATTERNS:
            # Fonction de remplacement chirurgicale : on ne masque que la partie sensible (le secret)
            def redact_match(match: re.Match[str]) -> str:
                self._emit_redact_event(secret_type)
                # Si la regex contient un ou plusieurs groupes de capture
                if match.groups():
                    # On identifie la position exacte du dernier groupe capturé (la valeur du secret)
                    last_group_idx = len(match.groups())
                    start_group, end_group = match.span(last_group_idx)
                    
                    # On convertit les index globaux en index relatifs à la correspondance courante
                    match_start = match.start(0)
                    rel_start = start_group - match_start
                    rel_end = end_group - match_start
                    
                    full_match_str = match.group(0)
                    # On ne remplace que le segment sensible
                    return (
                        full_match_str[:rel_start]
                        + REDACTED
                        + full_match_str[rel_end:]
                    )
                # S'il n'y a pas de groupe de capture (ex: private key complète), on remplace tout
                return REDACTED

            modified_value, count = pattern.subn(redact_match, modified_value)

        return modified_value

    def _emit_redact_event(self, secret_type: str) -> None:
        """Émet un signal sur l'EventBus de manière synchrone et sécurisée contre la réentrance."""
        if self._event_bus is None or self._is_emitting:
            return

        self._is_emitting = True
        try:
            from core.event_bus import Event
            self._event_bus.emit(Event(
                event_type="security.secret_redacted",
                payload={"secret_type": secret_type},
                timestamp_utc="",  # Géré lors de l'intégration dans le bus
            ))
        finally:
            self._is_emitting = False
