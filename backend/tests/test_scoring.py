"""Scoring examples E1 to E7 and strictest-wins decisions."""

from __future__ import annotations

import pytest

from backend.core.decision import TIER_DEFAULT, decide, score_decision
from backend.core.models import Decision
from backend.engines.risk_scorer import RiskScorer
from backend.policy.models import ScoringWeights, Thresholds

WEIGHTS = ScoringWeights(base=0.45, content=0.30, behaviour=0.25)
STRICT = Thresholds(mask=0.30, hold=0.55, block=0.80)


@pytest.mark.parametrize(
    ("name", "pattern", "location", "content", "behaviour", "expected_score"),
    [
        ("E1", 0.0, 0.0, 0.05, 0.0, 0.015),
        ("E2", 0.0, 0.6, 0.70, 0.0, 0.480),
        ("E3", 0.0, 0.6, 0.70, 0.51, 0.6075),
        ("E4", 0.0, 0.0, 0.10, 0.0, 0.030),
        ("E5", 0.0, 0.9, 0.60, 0.0, 0.585),
        ("E6", 0.0, 0.5, 0.20, 0.0, 0.285),
        ("E7", 0.6, 0.0, 0.30, 0.0, 0.360),
    ],
)
def test_worked_score_examples(
    name: str,
    pattern: float,
    location: float,
    content: float,
    behaviour: float,
    expected_score: float,
) -> None:
    scores = RiskScorer(WEIGHTS).combine(pattern, location, content, behaviour)
    assert scores.combined == pytest.approx(expected_score, abs=0.01), name


@pytest.mark.parametrize(
    ("name", "rule", "tier", "score", "floor", "expected"),
    [
        ("E1", "ALLOW", 0, 0.015, False, "ALLOW"),
        ("E2", "ALLOW", 0, 0.480, False, "MASK"),
        ("E3", "ALLOW", 0, 0.6075, False, "HOLD"),
        ("E4", "ALLOW", 0, 0.030, False, "ALLOW"),
        ("E5", "HOLD", 1, 0.585, True, "HOLD"),
        ("E6", "ALLOW", 1, 0.285, True, "MASK"),
        ("E7", "ALLOW", 0, 0.360, False, "MASK"),
    ],
)
def test_worked_decision_examples(
    name: str, rule: str, tier: int, score: float, floor: bool, expected: str
) -> None:
    result = decide(Decision(rule), TIER_DEFAULT[tier], score, STRICT, floor)
    assert result == Decision(expected), name


def test_strictest_wins_in_every_order() -> None:
    values = list(Decision)
    for rule in values:
        for tier in values:
            result = decide(rule, tier, 0.0, STRICT, False)
            assert result == max(rule, tier, key=lambda item: item.rank)


def test_masking_floor_sets_mask() -> None:
    assert decide(Decision.ALLOW, Decision.ALLOW, 0.0, STRICT, True) == Decision.MASK


def test_score_bands() -> None:
    assert score_decision(0.29, STRICT) == Decision.ALLOW
    assert score_decision(0.30, STRICT) == Decision.MASK
    assert score_decision(0.55, STRICT) == Decision.HOLD
    assert score_decision(0.80, STRICT) == Decision.BLOCK
    assert score_decision(0.999, STRICT) == Decision.BLOCK


def test_risk_scorer_clamps_signals() -> None:
    scores = RiskScorer(WEIGHTS).combine(2.0, -1.0, 5.0, -3.0)
    assert scores.pattern == 1.0
    assert scores.location == 0.0
    assert scores.content == 1.0
    assert scores.behaviour == 0.0
    assert scores.combined == pytest.approx(0.75)


def test_signal_storage_rounds_to_four_decimals() -> None:
    scores = RiskScorer(WEIGHTS).combine(0.123456, 0.2, 0.3, 0.4)
    assert scores.pattern == 0.1235
