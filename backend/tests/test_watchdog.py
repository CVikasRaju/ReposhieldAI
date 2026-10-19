"""Unit tests for the optional process watchdog (phase 9, C11)."""

from __future__ import annotations

from typing import Any

import pytest

from backend.core.models import Decision, EventRecord
from backend.watchdog import watchdog


class FakeAudit:
    """Capture appended events."""

    def __init__(self) -> None:
        self.events: list[EventRecord] = []

    async def append_event(self, event: EventRecord) -> EventRecord:
        self.events.append(event)
        return event


class FakeBus:
    """Capture published messages."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, kind: str, payload: dict[str, Any]) -> None:
        self.messages.append((kind, payload))


class FakeSessions:
    """Return one known session and nothing else."""

    async def get(self, session_id: str) -> object | None:
        return object() if session_id == "default" else None


def make_monitor() -> tuple[watchdog.ProcessMonitor, FakeAudit, FakeBus]:
    audit = FakeAudit()
    bus = FakeBus()
    return watchdog.ProcessMonitor(audit, FakeSessions(), bus), audit, bus


@pytest.mark.asyncio
async def test_watchdog_alert_logs_a_valid_event() -> None:
    monitor, audit, bus = make_monitor()
    await monitor._raise_watchdog_alert("default", "spawned curl", "WATCHDOG_ALERT")

    assert len(audit.events) == 1
    event = audit.events[0]
    assert event.decision == Decision.BLOCK
    assert event.connector == "system"
    assert event.reason_codes == ["WATCHDOG_ALERT"]
    assert event.reason_text == "spawned curl"
    assert isinstance(event.ts, str)
    assert monitor.watchdog_event_id == event.id
    assert bus.messages[0][0] == "event.created"


@pytest.mark.asyncio
async def test_start_watching_logs_when_a_shell_child_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor, audit, _bus = make_monitor()
    monkeypatch.setattr(watchdog, "suspicious_children", lambda pid: ["bash", "curl"])

    await monitor.start_watching("default", pid=1234)

    assert len(audit.events) == 1
    assert "bash" in audit.events[0].reason_text
    assert "curl" in audit.events[0].reason_text


@pytest.mark.asyncio
async def test_start_watching_is_quiet_without_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    monitor, audit, _bus = make_monitor()
    monkeypatch.setattr(watchdog, "suspicious_children", lambda pid: [])

    await monitor.start_watching("default", pid=1234)

    assert audit.events == []


@pytest.mark.asyncio
async def test_start_watching_ignores_unknown_sessions() -> None:
    monitor, audit, _bus = make_monitor()
    await monitor.start_watching("missing", pid=1234)
    assert audit.events == []


def test_suspicious_children_handles_a_missing_process() -> None:
    assert watchdog.suspicious_children(2_147_483_600) == []
