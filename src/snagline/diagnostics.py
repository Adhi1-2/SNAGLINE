"""Telemetry diagnostics and metric collectors for SNAGLINE monitor.

Provides in-memory performance profiling and event step rate counters.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional

DEFAULT_WINDOW_SECONDS = 120
DEFAULT_MAX_SAMPLES = 1000

@dataclass
class DiagnosticMetrics:
    """In-memory telemetry metric container with counter and timing series."""
    counters: Dict[str, int] = field(default_factory=dict)
    timings: Dict[str, List[float]] = field(default_factory=dict)
    max_samples: int = DEFAULT_MAX_SAMPLES

    def record_counter(self, name: str, increment: int = 1) -> None:
        """Increment named counter by specified non-negative amount."""
        if not name:
            raise ValueError("counter name cannot be empty")
        if increment < 0:
            raise ValueError("increment must be non-negative")
        self.counters[name] = self.counters.get(name, 0) + increment

    def record_timing(self, name: str, value: float) -> None:
        """Record sample value into named latency series with capacity cap."""
        if not name:
            raise ValueError("timing name cannot be empty")
        if name not in self.timings:
            self.timings[name] = []
        series = self.timings[name]
        if len(series) >= self.max_samples:
            series.pop(0)
        series.append(value)

    def percentile(self, name: str, p: float) -> Optional[float]:
        """Compute p-th percentile from recorded timing values."""
        series = self.timings.get(name)
        if not series:
            return None
        sorted_vals = sorted(series)
        k = (len(sorted_vals) - 1) * (p / 100.0)
        f = int(k)
        c = min(f + 1, len(sorted_vals) - 1)
        d0 = sorted_vals[f] * (c - k)
        d1 = sorted_vals[c] * (k - f)
        return d0 + d1
