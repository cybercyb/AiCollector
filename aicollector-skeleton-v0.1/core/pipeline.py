# /opt/aicollector/core/pipeline.py
"""Four-phase pipeline orchestrator."""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.base_collector import (
    BaseCollector,
    CollectorResult,
    CollectorCapabilities,
    Severity,
)
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
from core.diff_engine import DiffEngine, ChangeType
from core.sanitizer import Sanitizer
from core.knowledge_store import KnowledgeStore
from core.config_loader import AICollectorConfig
from core.hashing import compute_json_hash
from core.schemas import validate_knowledge_json
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
        
        self._collectors: list[BaseCollector] = []
        self._raw_results: dict[str, CollectorResult] = {}
        self._normalized_snapshots: dict[str, dict[str, Any]] = {}
        self._stats: PipelineStats | None = None

    def run(self) -> PipelineStats:
        """Execute the full pipeline.

        Returns:
            PipelineStats for the completed run.
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        start_time_mono = time.monotonic()
        
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
            self._event_bus.emit(Event(RUN_FAILED, {
                "run_id": run_id,
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            }))
            logger.error("Pipeline run failed: %s", exc, exc_info=True)
            raise
        finally:
            finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            duration_ms = (time.monotonic() - start_time_mono) * 1000
            
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
                        "changes_detected": self._stats.changes_detected if self._stats else 0,
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
            
            if self._stats:
                self._stats = replace(self._stats, collectors_run=self._stats.collectors_run + 1)
                
            try:
                collector = Registry.get_collector(name)
                self._collectors.append(collector)
                
                result = collector.collect(self._system_adapter)
                self._raw_results[name] = result
                
                if result.errors:
                    logger.warning("Collector '%s' completed with non-blocking errors", name)

            except Exception as exc:  # noqa: BLE001
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
        for collector in self._collectors:
            name = collector.name
            if name not in self._raw_results:
                continue
                
            raw_result = self._raw_results[name]
            
            try:
                # 1. Désinfection en profondeur contre la fuite de secrets
                sanitized_content = self._sanitizer.sanitize(raw_result.data)
                
                # Récupération sécurisée avec valeurs par défaut
                confidence_score = getattr(raw_result, "confidence_score", None)
                dependencies = getattr(raw_result, "dependencies", [])
                
                # Convertir les erreurs structurées du collecteur en listes de chaînes
                raw_errors = getattr(raw_result, "errors", [])
                string_errors = [str(err) for err in raw_errors] if raw_errors else []

                # Formatage strict du timestamp sans microsecondes (ex: 2026-07-16T09:12:22Z)
                timestamp_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                # 2. Construction de l'enveloppe commune standardisée (sans hash initial pour le calculer de façon propre)
                normalized_doc = {
                    "schema_version": getattr(collector, "schema_version", "1.0"),
                    "collector_version": getattr(collector, "collector_version", "1.0.0"),
                    "server_uuid": self._config.server_uuid or "00000000-0000-0000-0000-000000000000",
                    "timestamp_utc": timestamp_utc,
                    "source": name,
                    "content": sanitized_content,
                    "confidence_score": confidence_score,
                    "dependencies": dependencies,
                    "inconsistencies_detected": string_errors,
                    "capabilities": {
                        "supported_platforms": collector.capabilities().supported_platforms,
                        "min_confidence": collector.capabilities().min_confidence,
                        "known_inconsistencies": collector.capabilities().known_inconsistencies,
                    } if hasattr(collector, "capabilities") and collector.capabilities() else None
                }
                
                # 3. Calcul du hash canonique du document (compute_json_hash fournit l'hexadécimal pur)
                doc_hash = f"sha256:{compute_json_hash(normalized_doc)}"
                normalized_doc["hash"] = doc_hash
                
                # 4. Validation stricte du format final par schéma Pydantic
                validate_knowledge_json(normalized_doc)
                
                # Sauvegarde en mémoire pour les phases suivantes
                self._normalized_snapshots[name] = normalized_doc
                
                self._event_bus.emit(Event(
                    COLLECTOR_FINISHED,
                    {"collector_name": name, "run_id": run_id}
                ))
                
            except Exception as exc:  # noqa: BLE001
                if self._stats:
                    self._stats = replace(self._stats, collectors_failed=self._stats.collectors_failed + 1)
                
                self._event_bus.emit(Event(COLLECTOR_FAILED, {
                    "collector_name": name,
                    "run_id": run_id,
                    "error_type": "NormalizationError",
                    "error_message": f"Normalization or schema validation failed: {exc}"
                }))
                logger.error("Failed to normalize collector '%s': %s", name, exc, exc_info=True)
                
    # ── Phase 3: COMPARE ────────────────────────────────────────────────────

    def _phase_compare(self, run_id: str) -> list[dict[str, Any]]:
        """Diff current vs previous snapshots and emit change events."""
        all_change_events: list[dict[str, Any]] = []
        
        for name, current_doc in self._normalized_snapshots.items():
            previous_doc = self._knowledge_store.read_knowledge(name)
            if not previous_doc:
                continue
            
            raw_changes = self._diff_engine.compare(
                old_data=previous_doc,
                new_data=current_doc,
                classify_fn=getattr(Registry.get_collector(name), "classify_change", None)
            )
            
            if not raw_changes:
                continue
                
            change_id = str(uuid.uuid4())
            timestamp_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            
            serialized_changes = []
            highest_severity = Severity.INFO
            
            for change in raw_changes:
                if change.severity == Severity.WARNING and highest_severity == Severity.INFO:
                    highest_severity = Severity.WARNING
                elif change.severity == Severity.ERROR:
                    highest_severity = Severity.ERROR
                
                serialized_changes.append({
                    "type": str(change.change_type),
                    "path": change.path,
                    "description": f"Value changed at path: {change.path}",
                    "old_value_hash": f"sha256:{compute_json_hash({'v': change.old_value})}" if change.old_value is not None else None,
                    "new_value_hash": f"sha256:{compute_json_hash({'v': change.new_value})}" if change.new_value is not None else None,
                    "severity": str(change.severity),
                })
                
            change_event = {
                "schema_version": "1.0",
                "change_id": change_id,
                "run_id": run_id,
                "timestamp_utc": timestamp_utc,
                "collector": name,
                "severity": str(highest_severity),
                "summary": f"{len(raw_changes)} change(s) detected in collector '{name}'",
                "total_changes": len(raw_changes),
                "changes": serialized_changes,
            }
            
            all_change_events.append(change_event)
            
            if self._stats:
                self._stats = replace(self._stats, changes_detected=self._stats.changes_detected + len(raw_changes))
            
            self._event_bus.emit(Event("change_detected", {
                "collector_name": name,
                "change_id": change_id,
                "severity": str(highest_severity),
                "total_changes": len(raw_changes),
            }))
            
        return all_change_events

    # ── Phase 4: KNOWLEDGE BASE ─────────────────────────────────────────────

    def _phase_knowledge_base(
        self,
        run_id: str,
        changes: list[dict[str, Any]],
    ) -> None:
        """Persist normalised JSONs, rotate history, prune changes."""
        # Résolution résiliente et tolérante aux variations de RetentionConfig
        max_versions = 50
        max_changes = 200
        
        if hasattr(self._config, "retention") and self._config.retention is not None:
            max_versions = getattr(self._config.retention, "history_versions", 50)
            
            # Recherche du paramètre de rétention pour les changements par alias probables
            for attr in ("change_events", "changes_events", "changes", "max_changes", "keep_changes"):
                if hasattr(self._config.retention, attr):
                    max_changes = getattr(self._config.retention, attr)
                    break
        
        # 1. Écriture des connaissances normalisées et rotation FIFO de l'historique
        for name, doc in self._normalized_snapshots.items():
            self._knowledge_store.write_knowledge(name, doc)
            self._knowledge_store.rotate_history(name, doc, max_versions=max_versions)
            
        # 2. Écriture des événements de changement détectés
        for change_doc in changes:
            self._knowledge_store.write_change(change_doc)
            
        # 3. Purge des anciens changements
        self._knowledge_store.prune_changes(keep=max_changes)

    @property
    def stats(self) -> PipelineStats | None:
        """Return the stats of the last completed run."""
        return self._stats
