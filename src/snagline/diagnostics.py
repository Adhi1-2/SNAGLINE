"""Telemetry diagnostics and metric collectors for SNAGLINE monitor."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List

DEFAULT_WINDOW_SECONDS = 60

@dataclass
class DiagnosticMetrics:
    counters: Dict[str, int] = field(default_factory=dict)
    timings: Dict[str, List[float]] = field(default_factory=dict)

    def record_counter(self, name: str, increment: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + increment

    def record_timing(self, name: str, value: float) -> None:
        if name not in self.timings:
            self.timings[name] = []
        self.timings[name].append(value)
