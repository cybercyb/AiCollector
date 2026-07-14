"""Structured NDJSON logger with EventBus integration."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from core.event_bus import EventBus, Event

# Configuration des champs système standards à ignorer lors de l'extraction des variables dynamiques (extra)
RESERVED_ATTRS: frozenset[str] = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "message", "module", "msecs",
    "msg", "name", "pathname", "process", "processName", "relativeCreated",
    "stack_info", "thread", "threadName"
})


class EventBusHandler(logging.Handler):
    """Logging handler that publishes critical log events to the EventBus."""

    def __init__(self, event_bus: EventBus, level: int = logging.WARNING) -> None:
        super().__init__(level=level)
        self._event_bus = event_bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # On ne publie sur le bus que les alertes WARNING, ERROR, et CRITICAL
            event_type = f"log.{record.levelname.lower()}"
            
            # Extraction des attributs dynamiques
            extra_data = {
                k: v for k, v in record.__dict__.items()
                if k not in RESERVED_ATTRS and not k.startswith("_")
            }
            
            payload = {
                "message": record.getMessage(),
                "logger": record.name,
                "timestamp_utc": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                **extra_data
            }
            
            if record.exc_info:
                payload["exc_info"] = logging.Formatter().formatException(record.exc_info)

            # Publication synchrone in-process
            self._event_bus.publish(
                Event(
                    event_type=event_type,
                    payload=payload,
                    timestamp_utc=payload["timestamp_utc"]
                )
            )
        except Exception:
            # Ne jamais laisser un échec du système de log faire planter l'application principale
            self.handleError(record)


class NDJSONFormatter(logging.Formatter):
    """Format log records as NDJSON (one JSON object per line) with structured fields."""

    def format(self, record: logging.LogRecord) -> str:
        # Utilisation de l'heure exacte de création du log d'origine
        log_time = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        
        payload: dict[str, Any] = {
            "timestamp": log_time,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Inclusion automatique de toutes les variables contextuelles passées via 'extra'
        for key, value in record.__dict__.items():
            if key not in RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = value

        # Traitement propre des exceptions et des traces de pile
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        # Utilisation d'un sérialiseur robuste gérant les types complexes non-standards
        return json.dumps(payload, default=self._json_fallback_encoder)

    @staticmethod
    def _json_fallback_encoder(obj: Any) -> str:
        """Garantit l'absence de crash de sérialisation pour les types complexes."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        if isinstance(obj, Path):
            return str(obj)
        try:
            return str(obj)
        except Exception:
            return f"<unserializable {type(obj).__name__}>"


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    json_output: bool = True,
    event_bus: EventBus | None = None,
) -> logging.Logger:
    """Configure and return the application logger.

    Args:
        log_dir: Directory where log files are written.
        level: Minimum Logging level ("DEBUG", "INFO", "WARNING", "ERROR").
        json_output: If True, write NDJSON lines instead of plain text.
        event_bus: Optional EventBus instance to dispatch critical log events.

    Returns:
        Configured ``logging.Logger`` instance.
    """
    logger = logging.getLogger("aicollector")
    
    # Le logger principal accepte TOUT à partir de DEBUG pour laisser les handlers filtrer ensuite
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # Détermination du niveau de filtrage configuré pour l'application
    app_log_level = getattr(logging, level.upper(), logging.INFO)

    # 1. Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    # Respecte le niveau configuré en paramètre pour la console
    console_handler.setLevel(app_log_level)
    
    if json_output:
        console_handler.setFormatter(NDJSONFormatter())
    else:
        console_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] (%(name)s) : %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%SZ",
            )
        )
    logger.addHandler(console_handler)

    # 2. File handler avec rotation quotidienne (Rétention stricte 30 jours)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            log_dir / "aicollector.log",
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8",
            delay=True,  # N'ouvre le descripteur de fichier que lors du premier write (évite les conflits d'init)
        )
        # Le fichier capture tout à partir de DEBUG pour un historique d'analyse complet en cas d'erreur
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(NDJSONFormatter())
        logger.addHandler(file_handler)
    except OSError as exc:
        # Fallback console obligatoire si les droits d'écriture sur /var/log/ manquent au démarrage
        logger.warning(
            f"Unable to initialize file logger in '{log_dir}': {exc}. "
            "Logging exclusively to console."
        )

    # 3. Handler EventBus optionnel pour notifier le pipeline en cas d'erreurs d'exécution
    if event_bus is not None:
        eb_handler = EventBusHandler(event_bus, level=logging.WARNING)
        logger.addHandler(eb_handler)

    return logger
