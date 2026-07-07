"""Synchronous in-process pub/sub event bus."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from core.exceptions import EventBusError


@dataclass(frozen=True, slots=True)
class Event:
    """Immutable event object emitted by the pipeline."""
    event_type: str
    payload: dict
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


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
    """Singleton synchronous event dispatcher.

    Subscribers register callbacks for specific event types.
    When ``emit()`` is called, all registered handlers are invoked
    synchronously in the calling thread.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[Event], None]]] = {}

    def subscribe(self, event_type: str, handler: Callable[[Event], None]) -> None:
        """Register a handler for the given event type.

        Args:
            event_type: Event type string (e.g. ``"run.finished"``).
            handler: Callable that will be invoked with an ``Event`` instance.
        """
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    def emit(self, event: Event) -> None:
        """Dispatch ``event`` to all registered handlers.

        Args:
            event: The event to dispatch.

        Raises:
            EventBusError: If a handler raises unexpectedly.
        """
        handlers = list(self._subscribers.get(event.event_type, []))
        # Also deliver to wildcard subscribers
        handlers.extend(self._subscribers.get("*", []))
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001
                raise EventBusError(
                    f"Handler for '{event.event_type}' raised: {exc}"
                ) from exc

    def clear(self) -> None:
        """Remove all subscribers. Useful for testing."""
        self._subscribers.clear()

    def subscriber_count(self, event_type: str) -> int:
        """Return the number of subscribers for a given event type."""
        return len(self._subscribers.get(event_type, []))
