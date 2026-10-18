"""Strictest-wins decision resolution."""

from __future__ import annotations

from backend.core.models import Decision, strictest
from backend.policy.models import Thresholds

TIER_DEFAULT: dict[int, Decision] = {
    0: Decision.ALLOW,
    1: Decision.MASK,
    2: Decision.ALLOW,
    3: Decision.HOLD,
    4: Decision.HOLD,
    5: Decision.BLOCK,
}


def score_decision(score: float, thresholds: Thresholds) -> Decision:
    """Map a numeric score to the decision its band requires."""
    if score >= thresholds.block:
        return Decision.BLOCK
    if score >= thresholds.hold:
        return Decision.HOLD
    if score >= thresholds.mask:
        return Decision.MASK
    return Decision.ALLOW


def decide(
    rule: Decision,
    tier_default: Decision,
    score: float,
    thresholds: Thresholds,
    has_mask_floor: bool,
) -> Decision:
    """Resolve the final decision as the strictest of all inputs."""
    floor = Decision.MASK if has_mask_floor else Decision.ALLOW
    return strictest(rule, tier_default, score_decision(score, thresholds), floor)
