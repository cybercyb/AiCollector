"""Synchronous in-process pub/sub event bus."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from core.exceptions import EventBusError

# Initialisation du logger pour tracer les défaillances des abonnés
logger = logging.getLogger("aicollector")


def _get_utc_now_z() -> str:
    """Return current UTC time in strict ISO8601 format ending with 'Z'."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Event:
    """Immutable event object emitted by the pipeline."""
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp_utc: str = field(default_factory=_get_utc_now_z)


# Public event type constants
RUN_STARTED = "run.started"
RUN_FINISHED = "run.finished"
RUN_FAILED = "run.failed"
COLLECTOR_STARTED = "collector.started"
COLLECTOR_FINISHED = "collector.finished"
COLLECTOR_FAILED = "collector.failed"
CHANGE_DETECTED = "change.detected"
SECURITY_SECRET_REDACTED = "security.secret_redacted"
EXPORT_STARTED = "export.started"


class EventBus:
    """Synchronous event dispatcher.

    Subscribers register callbacks for specific event types.
    When ``emit()`` is called, all registered handlers are invoked
    synchronously in the calling thread.
    
    Any exception raised by a subscriber is caught, logged, and does
    not interrupt the event propagation or the pipeline execution.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[Event], None]]] = {}

    def subscribe(self, event_type: str, handler: Callable[[Event], None]) -> None:
        """Register a handler for the given event type.

        Args:
            event_type: Event type string (e.g. ``"run.finished"``) or ``"*"`` for all.
            handler: Callable that will be invoked with an ``Event`` instance.
        """
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    def emit(self, event: Event) -> None:
        """Dispatch ``event`` to all registered handlers synchronously.

        Failures in subscribers are non-blocking: they are logged as errors, 
        but do not prevent other subscribers from running, nor do they interrupt 
        the core execution flow of the pipeline.

        Args:
            event: The event to dispatch.
        """
        # Récupération des handlers spécifiques
        handlers = list(self._subscribers.get(event.event_type, []))
        # Ajout des handlers wildcard (*)
        handlers.extend(self._subscribers.get("*", []))

        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001
                # On encapsule l'erreur dans un EventBusError pour le log structuré
                wrapped_error = EventBusError(
                    f"Handler '{handler.__name__}' failed on event '{event.event_type}': {exc}"
                )
                # Log d'erreur non-bloquant : protège le cycle de vie du pipeline
                logger.error(
                    "EventBus non-blocking error: %s", 
                    wrapped_error, 
                    exc_info=True,
                    extra={
                        "event_type": event.event_type,
                        "handler_name": handler.__name__
                    }
                )

    def clear(self) -> None:
        """Remove all subscribers. Useful for testing."""
        self._subscribers.clear()

    def subscriber_count(self, event_type: str) -> int:
        """Return the number of subscribers for a given event type."""
        return len(self._subscribers.get(event_type, []))
