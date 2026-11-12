"""Canonical serialisation and hashing for envelopes (HLD §8, D-020)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_bytes(obj: Any) -> bytes:
    """Sorted keys, no insignificant whitespace, UTF-8. Stable across re-serialisation."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def envelope_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()
