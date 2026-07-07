"""Defence-in-depth secret redaction before JSON serialisation."""
from __future__ import annotations

import re
from typing import Any

from core.event_bus import EventBus


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
    "/home/*/.ssh",
})

REDACTED: str = "***REDACTED***"


class Sanitizer:
    """Recursively scan and redact secrets in data structures."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus

    def sanitize(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return a deep copy of ``data`` with secrets replaced."""
        return self._sanitize_value(data)

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: self._sanitize_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        if isinstance(value, str):
            return self._sanitize_string(value)
        return value

    def _sanitize_string(self, value: str) -> str:
        for secret_type, pattern in SECRET_PATTERNS:
            if pattern.search(value):
                self._emit_redact_event(secret_type)
                return REDACTED
        return value

    def _emit_redact_event(self, secret_type: str) -> None:
        if self._event_bus is None:
            return
        from core.event_bus import Event
        self._event_bus.emit(Event(
            event_type="security.secret_redacted",
            payload={"secret_type": secret_type},
            timestamp_utc="",
        ))
