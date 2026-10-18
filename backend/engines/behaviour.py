"""Per-session behaviour score from a sliding window."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from backend.policy.models import BehaviourConfig, KillSwitchConfig


@dataclass(frozen=True)
class BehaviourScore:
    """The four normalised behaviour terms and their weighted sum."""

    blocked: float
    sensitive: float
    breadth: float
    mismatch: float
    combined: float


@dataclass
class _SessionWindow:
    blocked: list[float] = field(default_factory=list)
    sensitive: list[float] = field(default_factory=list)
    directories: dict[str, float] = field(default_factory=dict)


def directory_of(target: str) -> str:
    """Return the directory component used for breadth counting."""
    normalised = target.replace("\\", "/")
    if normalised.startswith(("db:", "host:", "cmd:")):
        return normalised
    if "/" in normalised:
        head = normalised.rsplit("/", 1)[0]
        return head or "."
    return "."


class BehaviourTracker:
    """Track blocked attempts, sensitive reads, breadth and task mismatch.

    The clock is injectable so sliding-window behaviour is deterministic in tests.
    """

    def __init__(
        self,
        behaviour: BehaviourConfig,
        kill_switch: KillSwitchConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._behaviour = behaviour
        self._kill = kill_switch
        self._clock = clock
        self._windows: dict[str, _SessionWindow] = {}

    def _window(self, session_id: str) -> _SessionWindow:
        return self._windows.setdefault(session_id, _SessionWindow())

    def _prune(self, window: _SessionWindow, now: float) -> None:
        behaviour_cutoff = now - self._behaviour.window_seconds
        kill_cutoff = now - self._kill.window_seconds
        window.blocked = [ts for ts in window.blocked if ts >= kill_cutoff]
        window.sensitive = [ts for ts in window.sensitive if ts >= behaviour_cutoff]
        window.directories = {
            name: ts for name, ts in window.directories.items() if ts >= behaviour_cutoff
        }

    def record(
        self,
        session_id: str,
        *,
        decision: str,
        sensitive_read: bool = False,
        target: str = "",
    ) -> None:
        """Record one completed action. Only BLOCK decisions count as blocked."""
        now = self._clock()
        window = self._window(session_id)
        if decision == "BLOCK":
            window.blocked.append(now)
        if sensitive_read:
            window.sensitive.append(now)
        if target:
            window.directories[directory_of(target)] = now
        self._prune(window, now)

    def blocked_in_window(self, session_id: str) -> int:
        """Return BLOCK decisions inside the kill-switch window."""
        window = self._window(session_id)
        self._prune(window, self._clock())
        return len(window.blocked)

    def score(self, session_id: str, *, out_of_scope: bool = False) -> BehaviourScore:
        """Compute the behaviour signal for the current request."""
        now = self._clock()
        window = self._window(session_id)
        self._prune(window, now)
        weights = self._behaviour.weights
        blocked = min(1.0, len(window.blocked) / self._kill.max_blocked_attempts)
        sensitive = min(1.0, len(window.sensitive) / 5.0)
        breadth = min(1.0, len(window.directories) / 10.0)
        mismatch = 1.0 if out_of_scope else 0.0
        combined = min(
            1.0,
            weights.blocked * blocked
            + weights.sensitive_reads * sensitive
            + weights.breadth * breadth
            + weights.mismatch * mismatch,
        )
        return BehaviourScore(
            blocked=blocked,
            sensitive=sensitive,
            breadth=breadth,
            mismatch=mismatch,
            combined=combined,
        )

    def sensitive_read_count(self, session_id: str) -> int:
        """Return sensitive reads inside the behaviour window."""
        window = self._window(session_id)
        self._prune(window, self._clock())
        return len(window.sensitive)

    def should_lock(self, session_id: str, behaviour: BehaviourScore) -> bool:
        """Return True when the kill-switch conditions are met."""
        return (
            self.blocked_in_window(session_id) >= self._kill.max_blocked_attempts
            or behaviour.combined >= self._kill.behaviour_threshold
        )

    def reset(self, session_id: str) -> None:
        """Clear a session window, used when a human unlocks the session."""
        self._windows.pop(session_id, None)
