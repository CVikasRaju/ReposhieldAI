"""Approval manager tests."""

from __future__ import annotations

import asyncio
import builtins
import time
from pathlib import Path

from backend.core.models import ConnectorName, Resolution, SignalScores
from backend.tests.helpers import make_runtime


async def _request(runtime, timeout_seconds: int | None = None):
    return await runtime.approvals.request(
        session_id="default",
        connector=ConnectorName.DATABASE,
        action="db_query",
        target="customers",
        summary="SELECT * FROM customers",
        tier=1,
        reason_codes=["PROTECTED_TABLE"],
        reason_text="The agent asked for private data.",
        scores=SignalScores(location=0.9),
        read_operation=True,
    )


async def test_web_resolution(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    task = asyncio.create_task(_request(runtime))
    await asyncio.sleep(0.05)
    pending = await runtime.approvals.pending()
    assert len(pending) == 1
    assert Resolution.APPROVE_MASKED in pending[0].options
    assert await runtime.approvals.resolve(pending[0].id, Resolution.APPROVE_MASKED)
    outcome = await task
    assert outcome.resolution == Resolution.APPROVE_MASKED
    assert outcome.resolved_by == "web"
    stored = await runtime.approvals.get(outcome.approval_id)
    assert stored is not None and stored.status == "approved_masked"


async def test_timeout_resolves_as_none(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, approval_timeout=1)
    outcome = await _request(runtime)
    assert outcome.resolution is None
    assert outcome.resolved_by == "timeout"
    stored = await runtime.approvals.get(outcome.approval_id)
    assert stored is not None
    assert stored.status == "timeout"
    assert await runtime.approvals.pending() == []


async def test_non_read_has_no_masking_option(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    task = asyncio.create_task(
        runtime.approvals.request(
            session_id="default",
            connector=ConnectorName.COMMAND,
            action="run_command",
            target="rm -rf src",
            summary="rm -rf src",
            tier=3,
            reason_codes=["DESTRUCTIVE_COMMAND"],
            reason_text="The agent wants to delete work.",
            scores=SignalScores(),
            read_operation=False,
        )
    )
    await asyncio.sleep(0.05)
    pending = await runtime.approvals.pending()
    assert pending[0].options == [Resolution.APPROVE, Resolution.DENY]
    await runtime.approvals.resolve(pending[0].id, Resolution.DENY)
    outcome = await task
    assert outcome.resolution == Resolution.DENY


async def test_console_channel_does_not_block_event_loop(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path, interactive=True, approval_timeout=5)

    def fake_input(_prompt: str) -> str:
        time.sleep(0.2)
        return "y"

    monkeypatch.setattr(builtins, "input", fake_input)
    task = asyncio.create_task(_request(runtime))
    ticks = 0
    while not task.done():
        await asyncio.sleep(0.02)
        ticks += 1
    outcome = await task
    assert outcome.resolution == Resolution.APPROVE
    assert outcome.resolved_by == "console"
    assert ticks >= 2
    stored = await runtime.approvals.get(outcome.approval_id)
    assert stored is not None and stored.resolved_by == "console"


async def test_console_disabled_when_not_interactive(tmp_path: Path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path, interactive=False)

    def fail_input(_prompt: str) -> str:
        raise AssertionError("input must not be called")

    monkeypatch.setattr(builtins, "input", fail_input)
    task = asyncio.create_task(_request(runtime))
    await asyncio.sleep(0.05)
    pending = await runtime.approvals.pending()
    await runtime.approvals.resolve(pending[0].id, Resolution.APPROVE)
    outcome = await task
    assert outcome.resolution == Resolution.APPROVE
