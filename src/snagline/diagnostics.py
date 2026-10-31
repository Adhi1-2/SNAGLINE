"""Telemetry diagnostics and metric collectors for SNAGLINE monitor.

Provides in-memory performance profiling and event step rate counters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_WINDOW_SECONDS = 120
DEFAULT_MAX_SAMPLES = 1000


@dataclass
class DiagnosticMetrics:
    """In-memory telemetry metric container with counter and timing series."""

    counters: dict[str, int] = field(default_factory=dict)
    timings: dict[str, list[float]] = field(default_factory=dict)
    max_samples: int = DEFAULT_MAX_SAMPLES

    def __post_init__(self) -> None:
        """Validate the capacity cap before any series is touched.

        ``record_timing`` keeps the ``max_samples`` most recent values, and
        ``docs/diagnostics.md`` guarantees "bounded memory usage" via a "fixed
        ring-buffer capacity per metric series". A cap of 0 or less makes that
        method ``pop()`` an empty list -- ``IndexError`` on the first sample --
        and a NaN cap makes ``len(series) >= max_samples`` always False, so the
        series grows without bound and the documented guarantee is void
        (issue #728). Rejected here, at construction, following the sibling
        ``BoundedEventBuffer`` capacity guard in ``buffer.py``.
        """
        try:
            # int() raises ValueError for NaN and non-numeric values, and
            # OverflowError for an infinite literal.
            n = int(self.max_samples)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                f"max_samples must be a positive integer; got {self.max_samples!r}"
            ) from exc
        if n <= 0:
            raise ValueError(
                f"max_samples must be a positive integer; got {self.max_samples!r}"
            )
        self.max_samples = n

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

    def percentile(self, name: str, p: float) -> float | None:
        """Compute p-th percentile from recorded timing values via linear interpolation."""
        series = self.timings.get(name)
        if not series or not (0.0 <= p <= 100.0):
            return None
        sorted_vals = sorted(series)
        k = (len(sorted_vals) - 1) * (p / 100.0)
        f = int(k)
        c = min(f + 1, len(sorted_vals) - 1)
        if f == c:
            return float(sorted_vals[f])
        return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)
