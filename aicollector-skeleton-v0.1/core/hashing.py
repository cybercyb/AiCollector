"""SHA256 hashing utilities for JSON canonicalisation and file integrity."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


def _json_fallback_encoder(obj: Any) -> Any:
    """Fallback encoder for non-serialisable types in canonical JSON hashing."""
    if isinstance(obj, (set, frozenset)):
        return sorted(list(obj))  # Trié pour garantir la reproductibilité
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    try:
        return str(obj)
    except Exception:
        raise TypeError(f"Object of type {type(obj).__name__} is completely non-serialisable")


def compute_json_hash(data: dict[str, Any], *, canonical: bool = True) -> str:
    """Compute the SHA256 hex digest of a dictionary.

    Args:
        data: Dictionary to hash.
        canonical: If True, JSON is serialised with sorted keys and no
            indentation to guarantee reproducibility across runs.

    Returns:
        SHA256 hex digest prefixed with ``sha256:``.
    """
    if canonical:
        canonical_json = json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_fallback_encoder
        )
    else:
        canonical_json = json.dumps(
            data,
            sort_keys=False,
            default=_json_fallback_encoder
        )
    digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def compute_file_hash(path: Path) -> str:
    """Compute the SHA256 hex digest of a file on disk.

    Args:
        path: Absolute path to the file.

    Returns:
        SHA256 hex digest prefixed with ``sha256:``.

    Raises:
        FileNotFoundError: If the file does not exist.
        PermissionError: If the file cannot be accessed.
    """
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
