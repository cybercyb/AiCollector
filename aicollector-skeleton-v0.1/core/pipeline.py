"""Four-phase pipeline orchestrator."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.base_collector import (
    BaseCollector,
    CollectorResult,
    CollectorCapabilities,
    Severity,
)
from core.event_bus import EventBus, Event
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
        self._collectors: list[BaseCollector] = []
        self._stats: PipelineStats | None = None

    def run(self) -> PipelineStats:
        """Execute the full pipeline.

        Returns:
            PipelineStats for the completed run.
        """
        import time
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()
        self._stats = PipelineStats(run_id=run_id, started_at=started_at)
        logger.info("Pipeline run started", extra={"run_id": run_id})
        self._event_bus.emit(Event(RUN_STARTED, {"run_id": run_id}))

        try:
            # Phase 1 — COLLECT
            self._phase_collect(run_id)

            # Phase 2 — NORMALIZE
            self._phase_normalize(run_id)

            # Phase 3 — COMPARE
            changes = self._phase_compare(run_id)

            # Phase 4 — KNOWLEDGE BASE
            self._phase_knowledge_base(run_id, changes)

        except AICollectorError:
            self._stats = self._stats = PipelineStats(
                run_id=run_id,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=0.0,
                collectors_run=0,
                collectors_failed=0,
                changes_detected=0,
            )
            raise
        finally:
            finished_at = datetime.now(timezone.utc).isoformat()
            self._stats = PipelineStats(
                run_id=run_id,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=(time.time() * 1000),  # Will be refined later
                collectors_run=self._stats.collectors_run if self._stats else 0,
                collectors_failed=self._stats.collectors_failed if self._stats else 0,
                changes_detected=0,
            )
            self._event_bus.emit(Event(RUN_FINISHED, {
                "run_id": run_id,
                "stats": {
                    "collectors_run": self._stats.collectors_run,
                    "collectors_failed": self._stats.collectors_failed,
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
            try:
                collector = Registry.get_collector(name)
                self._collectors.append(collector)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to instantiate collector '%s': %s", name, exc)
                continue

    # ── Phase 2: NORMALIZE ───────────────────────────────────────────────────

    def _phase_normalize(self, run_id: str) -> None:
        """Validate, sanitize, and hash each collector result."""
        # TODO: implement per-collector normalisation with Pydantic schemas
        for collector in self._collectors:
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
