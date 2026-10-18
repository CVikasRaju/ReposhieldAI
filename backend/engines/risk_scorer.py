"""Combine the four sensitivity signals into one risk score."""

from __future__ import annotations

from dataclasses import dataclass

from backend.policy.models import ScoringWeights


@dataclass(frozen=True)
class SignalScores:
    """The signals shown in the UI and the combined score."""

    pattern: float
    location: float
    content: float
    behaviour: float
    combined: float


class RiskScorer:
    """Apply the documented weighting formula."""

    def __init__(self, weights: ScoringWeights) -> None:
        self.weights = weights

    def combine(self, pattern: float, location: float, content: float, behaviour: float) -> SignalScores:
        """Return the four clamped signals and the combined score."""
        pattern = _clamp(pattern)
        location = _clamp(location)
        content = _clamp(content)
        behaviour = _clamp(behaviour)
        base = max(pattern, location)
        combined = (
            self.weights.base * base
            + self.weights.content * content
            + self.weights.behaviour * behaviour
        )
        return SignalScores(
            pattern=round(pattern, 4),
            location=round(location, 4),
            content=round(content, 4),
            behaviour=round(behaviour, 4),
            combined=round(_clamp(combined), 4),
        )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
