"""Read / write the knowledge base, history, and changes directories."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.exceptions import KnowledgeWriteError, HistoryReadError
from core.hashing import compute_json_hash


logger = logging.getLogger("aicollector")


class KnowledgeStore:
    """Persist and retrieve knowledge-base JSON files."""

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir
        self._knowledge_dir = base_dir / "knowledge"
        self._history_dir = base_dir / "history"
        self._changes_dir = base_dir / "changes"
        for d in (self._knowledge_dir, self._history_dir, self._changes_dir):
            d.mkdir(parents=True, exist_ok=True)

    def write_knowledge(
        self,
        collector_name: str,
        data: dict[str, Any],
    ) -> None:
        """Write a collector's normalised JSON to ``knowledge/``.

        Args:
            collector_name: Collector identifier (used in filename).
            data: Normalised JSON dictionary ready for persistence.

        Raises:
            KnowledgeWriteError: If the write fails.
        """
        path = self._knowledge_dir / f"{collector_name}.json"
        try:
            path.write_text(
                json.dumps(data, indent=2, sort_keys=False),
                encoding="utf-8",
            )
        except OSError as exc:
            raise KnowledgeWriteError(collector_name, str(path)) from exc

    def read_knowledge(self, collector_name: str) -> dict[str, Any] | None:
        """Read the last known snapshot for a collector.

        Args:
            collector_name: Collector identifier.

        Returns:
            Parsed JSON dict, or None if the file does not exist.
        """
        path = self._knowledge_dir / f"{collector_name}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            return None

    def rotate_history(
        self,
        collector_name: str,
        data: dict[str, Any],
        max_versions: int = 50,
    ) -> None:
        """Copy the current snapshot into ``history/<collector>/``.

        Args:
            collector_name: Collector identifier.
            data: Data to archive.
            max_versions: Maximum versions to retain (FIFO purge).
        """
        hist_dir = self._history_dir / collector_name
        hist_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(hist_dir.glob("*.json"))
        next_num = (int(existing[-1].stem) if existing else 0) + 1
        next_path = hist_dir / f"{next_num:04d}.json"
        next_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        # FIFO purge
        while len(existing) >= max_versions:
            oldest = existing.pop(0)
            oldest.unlink()

    def write_change(self, change_data: dict[str, Any]) -> None:
        """Append a change detection event to the changes directory.

        Args:
            change_data: Normalised change JSON.
        """
        ts = change_data.get("timestamp_utc", datetime.now(timezone.utc).isoformat())
        safe_ts = ts.replace(":", "-").replace(".", "-")
        path = self._changes_dir / f"{safe_ts}.json"
        path.write_text(json.dumps(change_data, indent=2), encoding="utf-8")

    def prune_changes(self, keep: int = 200) -> None:
        """Purge the oldest change files, keeping only ``keep`` most recent.

        Args:
            keep: Maximum number of change files to retain.
        """
        files = sorted(self._changes_dir.glob("*.json"))
        while len(files) > keep:
            files.pop(0).unlink()
