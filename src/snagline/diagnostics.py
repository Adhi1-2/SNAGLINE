"""Telemetry diagnostics and metric collectors for SNAGLINE monitor.

Provides in-memory performance profiling and event step rate counters.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List

DEFAULT_WINDOW_SECONDS = 60

@dataclass
class DiagnosticMetrics:
    """In-memory telemetry metric container with counter and timing series."""
    counters: Dict[str, int] = field(default_factory=dict)
    timings: Dict[str, List[float]] = field(default_factory=dict)

    def record_counter(self, name: str, increment: int = 1) -> None:
        """Increment named counter by specified non-negative amount."""
        if not name:
            raise ValueError("counter name cannot be empty")
        if increment < 0:
            raise ValueError("increment must be non-negative")
        self.counters[name] = self.counters.get(name, 0) + increment

    def record_timing(self, name: str, value: float) -> None:
        """Record sample value into named latency series."""
        if not name:
            raise ValueError("timing name cannot be empty")
        if name not in self.timings:
            self.timings[name] = []
        self.timings[name].append(value)
