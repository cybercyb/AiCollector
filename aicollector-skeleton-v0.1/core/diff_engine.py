"""Recursive SHA256-based diff engine for detecting added/removed/modified keys."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from core.base_collector import Severity
from core.hashing import compute_json_hash


class ChangeType(StrEnum):
    """Enumeration of possible change types."""
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


@dataclass(frozen=True, slots=True)
class Change:
    """Represents a single change between two snapshots."""
    change_type: ChangeType
    path: str
    old_value: Any | None
    new_value: Any | None
    severity: Severity = Severity.INFO


@dataclass
class DiffEngine:
    """Compare two dictionaries and produce a list of structural changes."""

    def compare(self, old_data: dict[str, Any], new_data: dict[str, Any]) -> list[Change]:
        """Recursively compare two dictionaries.

        Args:
            old_data: The previous snapshot.
            new_data: The current snapshot.

        Returns:
            List of ``Change`` objects (empty if identical).
        """
        return self._compare_recursive(old_data, new_data, "content")

    def _compare_recursive(
        self,
        old: Any,
        new: Any,
        path: str,
    ) -> list[Change]:
        changes: list[Change] = []

        if isinstance(old, dict) and isinstance(new, dict):
            all_keys = set(old.keys()) | set(new.keys())
            for key in all_keys:
                child_path = f"{path}.{key}"
                if key in old and key in new:
                    if isinstance(old[key], dict) and isinstance(new[key], dict):
                        changes.extend(self._compare_recursive(old[key], new[key], child_path))
                    elif old[key] != new[key]:
                        old_hash = self._value_hash(old[key])
                        new_hash = self._value_hash(new[key])
                        changes.append(Change(
                            change_type=ChangeType.MODIFIED,
                            path=child_path,
                            old_value=old[key],
                            new_value=new[key],
                        ))
                elif key in old:
                    changes.append(Change(
                        change_type=ChangeType.REMOVED,
                        path=child_path,
                        old_value=old[key],
                        new_value=None,
                    ))
                else:
                    changes.append(Change(
                        change_type=ChangeType.ADDED,
                        path=child_path,
                        old_value=None,
                        new_value=new[key],
                    ))
        elif isinstance(old, list) and isinstance(new, list):
            max_len = max(len(old), len(new))
            for idx in range(max_len):
                child_path = f"{path}[{idx}]"
                if idx < len(old) and idx < len(new):
                    if isinstance(old[idx], dict) and isinstance(new[idx], dict):
                        changes.extend(self._compare_recursive(old[idx], new[idx], child_path))
                    elif old[idx] != new[idx]:
                        changes.append(Change(
                            change_type=ChangeType.MODIFIED,
                            path=child_path,
                            old_value=old[idx],
                            new_value=new[idx],
                        ))
                elif idx < len(old):
                    changes.append(Change(
                        change_type=ChangeType.REMOVED,
                        path=child_path,
                        old_value=old[idx],
                        new_value=None,
                    ))
                else:
                    changes.append(Change(
                        change_type=ChangeType.ADDED,
                        path=child_path,
                        old_value=None,
                        new_value=new[idx],
                    ))
        elif old != new:
            changes.append(Change(
                change_type=ChangeType.MODIFIED,
                path=path,
                old_value=old,
                new_value=new,
            ))

        return changes

    @staticmethod
    def _value_hash(value: Any) -> str:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()
