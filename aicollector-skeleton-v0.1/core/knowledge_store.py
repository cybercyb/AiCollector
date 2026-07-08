"""Read / write the knowledge base, history, and changes directories with strict atomicity."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.exceptions import KnowledgeWriteError, HistoryReadError
from core.hashing import compute_json_hash


logger = logging.getLogger("aicollector")


class KnowledgeStore:
    """Persist and retrieve knowledge-base JSON files atomicaally with manifest support."""

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir
        self._knowledge_dir = base_dir / "knowledge"
        self._history_dir = base_dir / "history"
        self._changes_dir = base_dir / "changes"
        
        for d in (self._knowledge_dir, self._history_dir, self._changes_dir):
            d.mkdir(parents=True, exist_ok=True)

    def _safe_write_json(self, path: Path, data: dict[str, Any], indent: int | None = 2) -> None:
        """Write JSON data to a file atomically using a temporary file."""
        tmp_path = path.with_suffix(".tmp")
        try:
            tmp_path.write_text(
                json.dumps(data, indent=indent, sort_keys=False),
                encoding="utf-8",
            )
            # Renommage atomique POSIX
            os.replace(tmp_path, path)
        except OSError as exc:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise exc

    def write_knowledge(
        self,
        collector_name: str,
        data: dict[str, Any],
    ) -> None:
        """Write a collector's normalised JSON to ``knowledge/`` and update manifest.

        Raises:
            KnowledgeWriteError: If the write fails.
        """
        path = self._knowledge_dir / f"{collector_name}.json"
        try:
            self._safe_write_json(path, data, indent=2)
            self._update_knowledge_manifest(collector_name, data)
        except OSError as exc:
            raise KnowledgeWriteError(collector_name, str(path)) from exc

    def read_knowledge(self, collector_name: str) -> dict[str, Any] | None:
        """Read the last known snapshot for a collector."""
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
        """Copy current snapshot into ``history/<collector>/`` with a strict FIFO rolling index.

        The history folder will contain files strictly named from 0001.json to <max_versions>.json.
        """
        hist_dir = self._history_dir / collector_name
        hist_dir.mkdir(parents=True, exist_ok=True)

        existing = sorted(hist_dir.glob("*.json"))
        
        # Si nous avons atteint la limite, on supprime le plus ancien (0001.json) 
        # et on décale tous les autres fichiers vers le bas.
        if len(existing) >= max_versions:
            try:
                existing[0].unlink()  # Supprime 0001.json
            except OSError as exc:
                logger.warning(f"Failed to delete oldest history file {existing[0]}: {exc}")
            
            # Décaler physiquement : 0002 -> 0001, 0003 -> 0002...
            for i in range(1, len(existing)):
                old_file = existing[i]
                new_file = hist_dir / f"{i:04d}.json"
                try:
                    os.replace(old_file, new_file)
                except OSError as exc:
                    logger.warning(f"Failed to rename history file {old_file} to {new_file}: {exc}")
            
            # Recalculer la liste après décalage
            existing = sorted(hist_dir.glob("*.json"))

        # Écrire le nouveau snapshot à la fin de la file
        next_num = len(existing) + 1
        next_path = hist_dir / f"{next_num:04d}.json"
        try:
            self._safe_write_json(next_path, data, indent=2)
        except OSError as exc:
            logger.error(f"Failed to write history file {next_path}: {exc}")

    def write_change(self, change_data: dict[str, Any]) -> None:
        """Append a change detection event to the changes directory and update manifest."""
        ts = change_data.get("timestamp_utc", datetime.now(timezone.utc).isoformat())
        safe_ts = ts.replace(":", "-").replace(".", "-")
        path = self._changes_dir / f"{safe_ts}.json"
        try:
            self._safe_write_json(path, change_data, indent=2)
            self._update_changes_manifest(change_data)
        except OSError as exc:
            logger.error(f"Failed to write change file {path}: {exc}")

    def prune_changes(self, keep: int = 200) -> None:
        """Purge oldest change files, keeping only ``keep`` most recent."""
        files = sorted(self._changes_dir.glob("*.json"))
        # Exclure le manifest de la liste de purge si présent
        files = [f for f in files if f.name != "manifest.json"]
        
        while len(files) > keep:
            try:
                files.pop(0).unlink()
            except OSError as exc:
                logger.warning(f"Failed to prune old change file: {exc}")
                break

    def _update_knowledge_manifest(self, collector_name: str, data: dict[str, Any]) -> None:
        """Update the global knowledge manifest with the latest hashes and timestamps."""
        manifest_path = self._knowledge_dir / "manifest.json"
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass

        manifest[collector_name] = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "hash": compute_json_hash(data),
            "schema_version": data.get("schema_version", "1.0"),
        }
        try:
            self._safe_write_json(manifest_path, manifest, indent=2)
        except OSError as exc:
            logger.error(f"Failed to write knowledge manifest: {exc}")

    def _update_changes_manifest(self, change_data: dict[str, Any]) -> None:
        """Append an entry to the global changes manifest file."""
        manifest_path = self._changes_dir / "manifest.json"
        manifest = {"total_entries": 0, "entries": []}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass

        entry = {
            "change_id": change_data.get("change_id"),
            "timestamp_utc": change_data.get("timestamp_utc"),
            "collector": change_data.get("collector"),
            "severity": change_data.get("severity"),
            "total_changes": change_data.get("total_changes", 0),
        }
        manifest["entries"].append(entry)
        manifest["total_entries"] = len(manifest["entries"])
        
        try:
            self._safe_write_json(manifest_path, manifest, indent=2)
        except OSError as exc:
            logger.error(f"Failed to write changes manifest: {exc}")
