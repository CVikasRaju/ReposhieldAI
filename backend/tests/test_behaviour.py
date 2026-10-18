"""Behaviour window and kill-switch tests with an injected clock."""

from __future__ import annotations

import pytest

from backend.engines.behaviour import BehaviourTracker, directory_of
from backend.policy.models import BehaviourConfig, KillSwitchConfig


class Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def tracker() -> tuple[BehaviourTracker, Clock]:
    clock = Clock()
    return BehaviourTracker(BehaviourConfig(), KillSwitchConfig(), clock=clock), clock


def test_initial_score_is_zero(tracker: tuple[BehaviourTracker, Clock]) -> None:
    behaviour, _ = tracker
    score = behaviour.score("s1")
    assert score.combined == 0.0


def test_worked_behaviour_example(tracker: tuple[BehaviourTracker, Clock]) -> None:
    behaviour, _ = tracker
    targets = ["src/a.py", "src/b.py", "src/c.py", "docs/guide.md", "tests/test_x.py"]
    for target in targets:
        behaviour.record("s1", decision="ALLOW", sensitive_read=True, target=target)
    score = behaviour.score("s1", out_of_scope=True)
    assert score.sensitive == pytest.approx(1.0)
    assert score.breadth == pytest.approx(0.3)
    assert score.mismatch == pytest.approx(1.0)
    assert score.combined == pytest.approx(0.51, abs=0.01)


def test_blocked_count_uses_only_block_decisions(tracker: tuple[BehaviourTracker, Clock]) -> None:
    behaviour, _ = tracker
    behaviour.record("s1", decision="BLOCK")
    behaviour.record("s1", decision="HOLD")
    behaviour.record("s1", decision="MASK")
    behaviour.record("s1", decision="ALLOW")
    assert behaviour.blocked_in_window("s1") == 1


def test_window_expiry(tracker: tuple[BehaviourTracker, Clock]) -> None:
    behaviour, clock = tracker
    behaviour.record("s1", decision="BLOCK", sensitive_read=True, target="src/a.py")
    assert behaviour.blocked_in_window("s1") == 1
    clock.advance(61)
    assert behaviour.blocked_in_window("s1") == 0
    assert behaviour.score("s1").combined == 0.0


def test_five_blocks_lock(tracker: tuple[BehaviourTracker, Clock]) -> None:
    behaviour, _ = tracker
    for _ in range(4):
        behaviour.record("s1", decision="BLOCK")
    assert not behaviour.should_lock("s1", behaviour.score("s1"))
    behaviour.record("s1", decision="BLOCK")
    assert behaviour.should_lock("s1", behaviour.score("s1"))


def test_behaviour_threshold_locks(tracker: tuple[BehaviourTracker, Clock]) -> None:
    behaviour, _ = tracker
    for index in range(10):
        behaviour.record("s1", decision="ALLOW", target=f"dir{index}/file.py")
    for _ in range(5):
        behaviour.record("s1", decision="ALLOW", sensitive_read=True)
    for _ in range(4):
        behaviour.record("s1", decision="BLOCK")
    score = behaviour.score("s1", out_of_scope=True)
    assert score.combined == pytest.approx(0.93, abs=0.01)
    assert behaviour.blocked_in_window("s1") == 4
    assert behaviour.should_lock("s1", score)


def test_reset_clears_window(tracker: tuple[BehaviourTracker, Clock]) -> None:
    behaviour, _ = tracker
    behaviour.record("s1", decision="BLOCK")
    behaviour.reset("s1")
    assert behaviour.blocked_in_window("s1") == 0


def test_directory_of() -> None:
    assert directory_of("src/auth/login.py") == "src/auth"
    assert directory_of("README.md") == "."
    assert directory_of("db:customers") == "db:customers"
    assert directory_of("host:example.com") == "host:example.com"
