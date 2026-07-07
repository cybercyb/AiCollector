"""Structured NDJSON logger with EventBus integration."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from core.event_bus import EventBus


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    json_output: bool = True,
) -> logging.Logger:
    """Configure and return the application logger.

    Args:
        log_dir: Directory where log files are written.
        level: Logging level (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``).
        json_output: If True, write NDJSON lines instead of plain text.

    Returns:
        Configured ``logging.Logger`` instance.
    """
    logger = logging.getLogger("aicollector")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    # Console handler (always present)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)
    if json_output:
        console_handler.setFormatter(_NDJSONFormatter())
    else:
        console_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
    logger.addHandler(console_handler)

    # File handler with daily rotation (30 days retention)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        log_dir / "aicollector.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_NDJSONFormatter())
    logger.addHandler(file_handler)

    return logger


class _NDJSONFormatter(logging.Formatter):
    """Format log records as NDJSON (one JSON object per line)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "run_id"):
            payload["run_id"] = record.run_id
        if hasattr(record, "collector_name"):
            payload["collector_name"] = record.collector_name
        if hasattr(record, "event_type"):
            payload["event_type"] = record.event_type
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)
