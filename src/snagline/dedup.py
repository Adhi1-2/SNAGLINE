"""Fingerprint deduplication for event payloads."""

from __future__ import annotations
import hashlib
import json
from typing import Any, Set

class EventDeduplicator:
    def __init__(self) -> None:
        self._seen_hashes: Set[str] = set()

    def is_duplicate(self, payload: Any) -> bool:
        normalized = json.dumps(payload, sort_keys=True)
        h = hashlib.sha256(normalized.encode()).hexdigest()
        if h in self._seen_hashes:
            return True
        self._seen_hashes.add(h)
        return False
