"""Risk score decay and half-life smoothing."""

from __future__ import annotations
import math

class ScoreDecay:
    def __init__(self, half_life_seconds: float = 300.0) -> None:
        self.half_life = half_life_seconds

    def decay_score(self, score: float, elapsed_seconds: float) -> float:
        if elapsed_seconds <= 0.0 or self.half_life <= 0.0:
            return score
        decay_factor = math.pow(0.5, elapsed_seconds / self.half_life)
        return score * decay_factor
