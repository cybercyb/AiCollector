"""Recursive SHA256-based diff engine for detecting added/removed/modified keys."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

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


class DiffEngine:
    """Compare two dictionaries and produce a list of structural changes."""

    # Clés d'identification communes pour l'alignement intelligent dans les listes
    IDENTIFIER_KEYS = ("name", "id", "uuid", "port", "destination", "interface", "alias")

    def compare(
        self,
        old_data: dict[str, Any],
        new_data: dict[str, Any],
        classify_fn: Callable[[str, Any, Any], Severity] | None = None,
    ) -> list[Change]:
        """Recursively compare two dictionaries.

        Args:
            old_data: The previous snapshot (already normalized).
            new_data: The current snapshot (already normalized).
            classify_fn: Optional callback function to classify change severity.

        Returns:
            List of ``Change`` objects (empty if identical).
        """
        raw_changes = self._compare_recursive(old_data, new_data, "content")
        
        if not classify_fn:
            return raw_changes

        # Application de la classification de sévérité spécifique au collecteur
        classified_changes: list[Change] = []
        for change in raw_changes:
            severity = classify_fn(change.path, change.old_value, change.new_value)
            classified_changes.append(
                Change(
                    change_type=change.change_type,
                    path=change.path,
                    old_value=change.old_value,
                    new_value=change.new_value,
                    severity=severity,
                )
            )
        return classified_changes

    def _compare_recursive(
        self,
        old: Any,
        new: Any,
        path: str,
    ) -> list[Change]:
        changes: list[Change] = []

        # Cas 1: Les deux structures sont des dictionnaires
        if isinstance(old, dict) and isinstance(new, dict):
            all_keys = set(old.keys()) | set(new.keys())
            for key in all_keys:
                child_path = f"{path}.{key}"
                if key in old and key in new:
                    changes.extend(self._compare_recursive(old[key], new[key], child_path))
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

        # Cas 2: Les deux structures sont des listes (Alignement intelligent)
        elif isinstance(old, list) and isinstance(new, list):
            # Si ce sont des listes d'objets complexes, on tente d'aligner par identifiant
            if old and new and isinstance(old[0], dict) and isinstance(new[0], dict):
                # Trouver un champ identifiant commun (ex: "name")
                id_key = next((k for k in self.IDENTIFIER_KEYS if k in old[0] and k in new[0]), None)
                
                if id_key:
                    old_by_id = {item[id_key]: item for item in old if isinstance(item, dict) and id_key in item}
                    new_by_id = {item[id_key]: item for item in new if isinstance(item, dict) and id_key in item}
                    
                    all_ids = set(old_by_id.keys()) | set(new_by_id.keys())
                    for item_id in all_ids:
                        # Représentation du JSONPath standard : path.containers[name=redis-cache]
                        child_path = f"{path}[{id_key}={item_id}]"
                        if item_id in old_by_id and item_id in new_by_id:
                            changes.extend(self._compare_recursive(old_by_id[item_id], new_by_id[item_id], child_path))
                        elif item_id in old_by_id:
                            changes.append(Change(
                                change_type=ChangeType.REMOVED,
                                path=child_path,
                                old_value=old_by_id[item_id],
                                new_value=None,
                            ))
                        else:
                            changes.append(Change(
                                change_type=ChangeType.ADDED,
                                path=child_path,
                                old_value=None,
                                new_value=new_by_id[item_id],
                            ))
                    return changes

            # Alignement de secours par index si ce ne sont pas des objets identifiables
            max_len = max(len(old), len(new))
            for idx in range(max_len):
                child_path = f"{path}[{idx}]"
                if idx < len(old) and idx < len(new):
                    changes.extend(self._compare_recursive(old[idx], new[idx], child_path))
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

        # Cas 3: Valeurs simples ou types différents
        elif old != new:
            changes.append(Change(
                change_type=ChangeType.MODIFIED,
                path=path,
                old_value=old,
                new_value=new,
            ))

        return changes
