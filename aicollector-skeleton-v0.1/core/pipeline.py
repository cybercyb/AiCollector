"""Four-phase pipeline orchestrator."""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field, replace  # Ajout de replace pour les frozen dataclasses
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.base_collector import (
    BaseCollector,
    CollectorResult,
    CollectorCapabilities,
    Severity,
)
# Correction de l'import : importation des constantes d'événements
from core.event_bus import (
    EventBus,
    Event,
    RUN_STARTED,
    RUN_FINISHED,
    RUN_FAILED,
    COLLECTOR_STARTED,
    COLLECTOR_FINISHED,
    COLLECTOR_FAILED,
)
from core.registry import Registry
from core.system_adapter import SystemAdapter
from core.diff_engine import DiffEngine
from core.sanitizer import Sanitizer
from core.knowledge_store import KnowledgeStore
from core.config_loader import AICollectorConfig
from core.hashing import compute_json_hash
from core.exceptions import (
    AICollectorError,
    CollectorError,
    CollectorTimeoutError,
)

logger = logging.getLogger("aicollector")


@dataclass(frozen=True, slots=True)
class PipelineStats:
    """Aggregated statistics for a single pipeline run."""
    run_id: str
    started_at: str
    finished_at: str | None = None
    duration_ms: float = 0.0
    collectors_run: int = 0
    collectors_failed: int = 0
    changes_detected: int = 0
    memory_peak_mb: float = 0.0


class Pipeline:
    """Orchestrate the four execution phases: COLLECT → NORMALIZE → COMPARE → KNOWLEDGE BASE."""

    def __init__(
        self,
        config: AICollectorConfig,
        event_bus: EventBus,
        knowledge_store: KnowledgeStore,
    ) -> None:
        self._config = config
        self._event_bus = event_bus
        self._knowledge_store = knowledge_store
        self._system_adapter = SystemAdapter(enable_cache=True)
        self._diff_engine = DiffEngine()
        self._sanitizer = Sanitizer(event_bus)
        
        # On va stocker les instances de collecteurs ET leurs résultats bruts
        self._collectors: list[BaseCollector] = []
        self._raw_results: dict[str, CollectorResult] = {}
        self._stats: PipelineStats | None = None

    def run(self) -> PipelineStats:
        """Execute the full pipeline.

        Returns:
            PipelineStats for the completed run.
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()
        start_time_mono = time.monotonic()  # Pour un calcul de durée précis et robuste
        
        self._stats = PipelineStats(run_id=run_id, started_at=started_at)
        logger.info("Pipeline run started", extra={"run_id": run_id})
        self._event_bus.emit(Event("run.started", {"run_id": run_id}))

        success = False
        try:
            # Phase 1 — COLLECT
            self._phase_collect(run_id)

            # Phase 2 — NORMALIZE
            self._phase_normalize(run_id)

            # Phase 3 — COMPARE
            changes = self._phase_compare(run_id)

            # Phase 4 — KNOWLEDGE BASE
            self._phase_knowledge_base(run_id, changes)
            
            success = True

        except Exception as exc:  # noqa: BLE001
            # Événement run.failed requis par la spécification
            self._event_bus.emit(Event(RUN_FAILED, {
                "run_id": run_id,
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            }))
            logger.error("Pipeline run failed: %s", exc, exc_info=True)
            raise
        finally:
            finished_at = datetime.now(timezone.utc).isoformat()
            duration_ms = (time.monotonic() - start_time_mono) * 1000
            
            # Mise à jour propre du frozen dataclass avec 'replace'
            if self._stats:
                self._stats = replace(
                    self._stats,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                )
            
            if success:
                self._event_bus.emit(Event("run.finished", {
                    "run_id": run_id,
                    "duration_ms": duration_ms,
                    "stats": {
                        "collectors_run": self._stats.collectors_run if self._stats else 0,
                        "collectors_failed": self._stats.collectors_failed if self._stats else 0,
                    }
                }))

        return self._stats

    # ── Phase 1: COLLECT ──────────────────────────────────────────────────────

    def _phase_collect(self, run_id: str) -> None:
        """Discover and run all enabled collectors."""
        Registry.discover()
        all_names = Registry.list_collectors()
        enabled = [
            n for n in all_names
            if (not self._config.collectors.enabled or n in self._config.collectors.enabled)
            and n not in self._config.collectors.disabled
        ]
        
        for name in enabled:
            self._event_bus.emit(Event(
                COLLECTOR_STARTED,
                {"collector_name": name, "run_id": run_id}
            ))
            
            # Mise à jour statistique (utilisation sécurisée de replace)
            if self._stats:
                self._stats = replace(self._stats, collectors_run=self._stats.collectors_run + 1)
                
            try:
                # 1. Instanciation
                collector = Registry.get_collector(name)
                self._collectors.append(collector)
                
                # 2. Exécution de la collecte (Correction de l'omission majeure)
                # Note : Le timeout est géré au niveau du collecteur ou du system_adapter
                result = collector.collect(self._system_adapter)
                self._raw_results[name] = result
                
                # Si le collecteur renvoie des erreurs internes non-bloquantes
                if result.errors:
                    logger.warning("Collector '%s' completed with non-blocking errors", name)

            except Exception as exc:  # noqa: BLE001
                # Gestion de l'échec d'un collecteur individuel
                if self._stats:
                    self._stats = replace(self._stats, collectors_failed=self._stats.collectors_failed + 1)
                
                self._event_bus.emit(Event(COLLECTOR_FAILED, {
                    "collector_name": name,
                    "run_id": run_id,
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc)
                }))
                logger.error("Failed to execute collector '%s': %s", name, exc)

    # ── Phase 2: NORMALIZE ───────────────────────────────────────────────────

    def _phase_normalize(self, run_id: str) -> None:
        """Validate, sanitize, and hash each collector result."""
        # TODO: Implémenter la normalisation par schéma Pydantic
        for collector in self._collectors:
            if collector.name not in self._raw_results:
                continue
                
            # Ici s'exécutera la sanitization et la validation
            # self._sanitizer.sanitize(...)
            
            self._event_bus.emit(Event(
                COLLECTOR_FINISHED,
                {"collector_name": collector.name, "run_id": run_id}
            ))

    # ── Phase 3: COMPARE ────────────────────────────────────────────────────

    def _phase_compare(self, run_id: str) -> list[dict[str, Any]]:
        """Diff current vs previous snapshots and emit change events."""
        # TODO: implement full diff + change detection
        return []

    # ── Phase 4: KNOWLEDGE BASE ─────────────────────────────────────────────

    def _phase_knowledge_base(
        self,
        run_id: str,
        changes: list[dict[str, Any]],
    ) -> None:
        """Persist normalised JSONs, rotate history, prune changes."""
        # TODO: write knowledge JSONs, rotate history, prune FIFO
        pass

    @property
    def stats(self) -> PipelineStats | None:
        """Return the stats of the last completed run."""
        return self._stats
