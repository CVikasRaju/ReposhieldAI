"""The 12-step pipeline against a fake connector."""

from __future__ import annotations

import asyncio
from pathlib import Path

from backend.core.models import (
    ActionError,
    ConnectorName,
    Decision,
    ExecResult,
    Resolution,
    RuleVerdict,
)
from backend.engines.detectors import DetectContext, detect
from backend.engines.text_masker import mask_text
from backend.tests.helpers import (
    FakeConnector,
    make_prepared,
    make_runtime,
    request,
    wait_pending,
)

PROTECTED_TABLE = RuleVerdict(decision=Decision.HOLD, reason_codes=["PROTECTED_TABLE"])


async def test_allow_executes_and_logs(tmp_path: Path) -> None:
    connector = FakeConnector(make_prepared(content="hello world"), ExecResult(content="hello world"))
    runtime = make_runtime(tmp_path, connectors={"filesystem": connector})
    result = await runtime.pipeline.handle(request())
    assert result.status == "ok"
    assert result.content == "hello world"
    assert connector.execute_calls == 1
    events = runtime.audit.list_events()
    assert len(events) == 1
    assert events[0].decision == "ALLOW"
    assert events[0].final_outcome == "executed"
    assert events[0].reason_codes[0] == "ALLOWED"
    assert runtime.audit.verify_chain().ok


async def test_rule_block_never_executes(tmp_path: Path) -> None:
    prepared = make_prepared(
        content=None,
        scope=".env",
        tier=5,
        rule=RuleVerdict(decision=Decision.BLOCK, reason_codes=["PROTECTED_PATH"]),
    )
    connector = FakeConnector(prepared)
    runtime = make_runtime(tmp_path, connectors={"filesystem": connector})
    result = await runtime.pipeline.handle(request(".env"))
    assert result.status == "blocked"
    assert result.content is None
    assert result.notice == "This action is not permitted by the workspace security policy."
    assert connector.execute_calls == 0
    event = runtime.audit.list_events()[0]
    assert event.decision == "BLOCK"
    assert event.final_outcome == "blocked"
    assert "PROTECTED_PATH" in event.reason_codes
    assert runtime.audit.verify_chain().ok


async def test_auto_mask_floor_masks_secret(tmp_path: Path) -> None:
    raw = 'AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"'
    connector = FakeConnector(make_prepared(content=raw), ExecResult(content=raw))
    runtime = make_runtime(tmp_path, connectors={"filesystem": connector})
    result = await runtime.pipeline.handle(request())
    assert result.status == "masked"
    assert result.content is not None
    assert "AKIAIOSFODNN7EXAMPLE" not in result.content
    assert "[MASKED_SECRET_01]" in result.content
    assert "restored automatically" in (result.notice or "")
    event = runtime.audit.list_events()[0]
    assert event.decision == "MASK"
    assert event.final_outcome == "executed_masked"
    assert event.risk_tier == 1
    assert all("AKIAIOSFODNN7EXAMPLE" not in item["preview"] for item in event.detections)
    delta = runtime.audit.get_delta(event.id)
    assert delta is not None
    assert "AKIAIOSFODNN7EXAMPLE" in delta.raw
    assert "AKIAIOSFODNN7EXAMPLE" not in delta.sanitized
    assert runtime.audit.verify_chain().ok


async def test_hold_approve_masked_masks_pii(tmp_path: Path) -> None:
    prepared = make_prepared(
        content="customer asha@corp.test asked",
        scope="db:customers",
        action="db_query",
        tier=1,
        rule=PROTECTED_TABLE,
        meta={"location_weight": 0.9, "summary": "SELECT * FROM customers"},
    )
    connector = FakeConnector(prepared, ExecResult(content=prepared.content))
    runtime = make_runtime(tmp_path, connectors={"database": connector})
    task = asyncio.create_task(runtime.pipeline.handle(request("customers", connector=ConnectorName.DATABASE, action="db_query")))
    approval_id = await wait_pending(runtime)
    pending = (await runtime.approvals.pending())[0]
    assert "PROTECTED_TABLE" in pending.reason_codes
    assert Resolution.APPROVE_MASKED in pending.options
    assert await runtime.approvals.resolve(approval_id, Resolution.APPROVE_MASKED)
    result = await task
    assert result.status == "masked"
    assert "asha@corp.test" not in (result.content or "")
    assert "[EMAIL_1]" in (result.content or "")
    event = runtime.audit.list_events()[0]
    assert event.decision == "HOLD"
    assert event.approval == "approved_masked"
    assert event.final_outcome == "executed_masked"
    assert "HUMAN_EDITED" in event.reason_codes
    stored = await runtime.approvals.get(approval_id)
    assert stored is not None and stored.status == "approved_masked"
    assert runtime.audit.verify_chain().ok


async def test_hold_approve_releases_pii_but_not_secrets(tmp_path: Path) -> None:
    prepared = make_prepared(
        content="asha@corp.test AKIAIOSFODNN7EXAMPLE",
        scope="db:customers",
        action="db_query",
        tier=1,
        rule=PROTECTED_TABLE,
        meta={"location_weight": 0.9},
    )
    connector = FakeConnector(prepared, ExecResult(content=prepared.content))
    runtime = make_runtime(tmp_path, connectors={"database": connector})
    task = asyncio.create_task(runtime.pipeline.handle(request("customers", connector=ConnectorName.DATABASE, action="db_query")))
    approval_id = await wait_pending(runtime)
    assert await runtime.approvals.resolve(approval_id, Resolution.APPROVE)
    result = await task
    assert result.status == "masked"
    assert "asha@corp.test" in (result.content or "")
    assert "AKIAIOSFODNN7EXAMPLE" not in (result.content or "")
    event = runtime.audit.list_events()[0]
    assert event.approval == "approved"
    assert "HUMAN_APPROVED" in event.reason_codes


async def test_hold_timeout_denies(tmp_path: Path) -> None:
    prepared = make_prepared(
        content="customer data",
        scope="db:customers",
        action="db_query",
        tier=1,
        rule=PROTECTED_TABLE,
    )
    connector = FakeConnector(prepared, ExecResult(content="customer data"))
    runtime = make_runtime(tmp_path, approval_timeout=1, connectors={"database": connector})
    result = await runtime.pipeline.handle(request("customers", connector=ConnectorName.DATABASE, action="db_query"))
    assert result.status == "denied"
    assert connector.execute_calls == 0
    event = runtime.audit.list_events()[0]
    assert event.approval == "timeout"
    assert event.final_outcome == "timeout"
    assert "APPROVAL_TIMEOUT" in event.reason_codes
    assert await runtime.approvals.pending() == []


async def test_first_answer_wins(tmp_path: Path) -> None:
    prepared = make_prepared(
        content="customer data",
        scope="db:customers",
        action="db_query",
        tier=1,
        rule=PROTECTED_TABLE,
    )
    connector = FakeConnector(prepared, ExecResult(content="customer data"))
    runtime = make_runtime(tmp_path, connectors={"database": connector})
    task = asyncio.create_task(runtime.pipeline.handle(request("customers", connector=ConnectorName.DATABASE, action="db_query")))
    approval_id = await wait_pending(runtime)
    assert await runtime.approvals.resolve(approval_id, Resolution.APPROVE)
    assert not await runtime.approvals.resolve(approval_id, Resolution.DENY)
    await task
    stored = await runtime.approvals.get(approval_id)
    assert stored is not None
    assert stored.status == "approved"
    assert stored.resolved_by == "web"


async def test_deny_and_lock_locks_session(tmp_path: Path) -> None:
    prepared = make_prepared(
        content="customer data",
        scope="db:customers",
        action="db_query",
        tier=1,
        rule=PROTECTED_TABLE,
    )
    connector = FakeConnector(prepared, ExecResult(content="customer data"))
    runtime = make_runtime(tmp_path, connectors={"database": connector})
    task = asyncio.create_task(runtime.pipeline.handle(request("customers", connector=ConnectorName.DATABASE, action="db_query")))
    approval_id = await wait_pending(runtime)
    assert await runtime.approvals.resolve(approval_id, Resolution.DENY_AND_LOCK)
    result = await task
    assert result.status == "denied"
    session = await runtime.sessions.get("default")
    assert session is not None and session.status == "locked"


async def test_action_error_is_not_a_policy_block(tmp_path: Path) -> None:
    connector = FakeConnector(prepare_error=ActionError("File not found: README.md"))
    runtime = make_runtime(tmp_path, connectors={"filesystem": connector})
    result = await runtime.pipeline.handle(request())
    assert result.status == "error"
    assert "File not found" in (result.notice or "")
    event = runtime.audit.list_events()[0]
    assert event.final_outcome == "error"
    assert event.reason_codes == ["ACTION_FAILED"]
    assert event.decision == "ALLOW"


async def test_prepare_exception_fails_closed(tmp_path: Path) -> None:
    connector = FakeConnector(prepare_error=RuntimeError("SECRET AKIAIOSFODNN7EXAMPLE"))
    runtime = make_runtime(tmp_path, connectors={"filesystem": connector})
    result = await runtime.pipeline.handle(request())
    assert result.status == "blocked"
    assert result.notice == "This action is not permitted by the workspace security policy."
    event = runtime.audit.list_events()[0]
    assert event.decision == "BLOCK"
    assert event.reason_codes == ["INTERNAL_ERROR"]
    assert "AKIAIOSFODNN7EXAMPLE" not in event.model_dump_json()


async def test_execute_exception_fails_closed(tmp_path: Path) -> None:
    connector = FakeConnector(
        make_prepared(content="hello"), execute_error=RuntimeError("boom")
    )
    runtime = make_runtime(tmp_path, connectors={"filesystem": connector})
    result = await runtime.pipeline.handle(request())
    assert result.status == "blocked"
    event = runtime.audit.list_events()[0]
    assert event.reason_codes == ["INTERNAL_ERROR"]
    assert runtime.audit.verify_chain().ok


async def test_kill_switch_after_five_blocks(tmp_path: Path) -> None:
    def hook(req):
        return make_prepared(
            content=None,
            scope=req.target,
            tier=5,
            rule=RuleVerdict(decision=Decision.BLOCK, reason_codes=["PROTECTED_PATH"]),
        )

    connector = FakeConnector(prepare_hook=hook)
    runtime = make_runtime(tmp_path, connectors={"filesystem": connector})
    for index in range(5):
        result = await runtime.pipeline.handle(request(f".env{index}"))
        assert result.status == "blocked"
    session = await runtime.sessions.get("default")
    assert session is not None and session.status == "locked"
    assert runtime.behaviour.blocked_in_window("default") == 5
    terminated = await runtime.pipeline.handle(request(".env"))
    assert terminated.status == "terminated"
    assert runtime.behaviour.blocked_in_window("default") == 5
    events = runtime.audit.list_events(limit=50)
    assert any(event.reason_codes == ["KILL_SWITCH"] for event in events)
    assert any(event.reason_codes == ["SESSION_LOCKED"] for event in events)
    assert runtime.audit.verify_chain().ok


async def test_human_denials_do_not_trigger_kill_switch(tmp_path: Path) -> None:
    def hook(req):
        if req.target == "deny":
            return make_prepared(
                content="customer data",
                scope="db:customers",
                action="db_query",
                tier=1,
                rule=PROTECTED_TABLE,
            )
        return make_prepared(
            content=None,
            scope=req.target,
            tier=5,
            rule=RuleVerdict(decision=Decision.BLOCK, reason_codes=["PROTECTED_PATH"]),
        )

    connector = FakeConnector(prepare_hook=hook)
    runtime = make_runtime(tmp_path, connectors={"filesystem": connector, "database": connector})
    for index in range(4):
        await runtime.pipeline.handle(request(f".env{index}"))
    task = asyncio.create_task(
        runtime.pipeline.handle(request("deny", connector=ConnectorName.DATABASE, action="db_query"))
    )
    approval_id = await wait_pending(runtime)
    await runtime.approvals.resolve(approval_id, Resolution.DENY)
    await task
    session = await runtime.sessions.get("default")
    assert session is not None and session.status == "active"
    assert runtime.behaviour.blocked_in_window("default") == 4
    await runtime.pipeline.handle(request(".env5"))
    session = await runtime.sessions.get("default")
    assert session is not None and session.status == "locked"


async def test_write_restore_applies_placeholder(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    raw = "asha@corp.test"
    masked = mask_text(
        raw,
        spans=detect(raw, DetectContext(target="src/app.py")),
        session_id="default",
        scope="src/app.py",
        store=runtime.store,
    )
    assert masked.sanitized == "[EMAIL_1]"
    content = "hello [EMAIL_1]"
    prepared = make_prepared(
        content=content,
        write_content=content,
        action="write_file",
        read_operation=False,
        scope="src/app.py",
        meta={"current_sha256": "abc"},
    )
    connector = FakeConnector(prepared, ExecResult(content=None))
    runtime = make_runtime(tmp_path, connectors={"filesystem": connector})
    # The second runtime has a different audit database, so seed its map too.
    raw = "asha@corp.test"
    masked = mask_text(
        raw,
        spans=detect(raw, DetectContext(target="src/app.py")),
        session_id="default",
        scope="src/app.py",
        store=runtime.store,
    )
    assert masked.sanitized == "[EMAIL_1]"
    result = await runtime.pipeline.handle(request(action="write_file"))
    assert result.status == "ok"
    event = runtime.audit.list_events()[0]
    delta = runtime.audit.get_delta(event.id)
    assert delta is not None
    assert "[EMAIL_1]" in delta.raw
    assert "asha@corp.test" in delta.sanitized


async def test_stale_masked_base_escalates_to_hold(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    placeholder = runtime.store.allocate(
        session_id="default",
        scope="src/app.py",
        family="MASKED_LOGIC_BLOCK",
        original="    return 1\n",
        pad=2,
        numbering="scope",
        original_indent="    ",
        base_sha256="old-hash",
    )
    content = f"def f():\n    pass  # {placeholder}\n"
    prepared = make_prepared(
        content=content,
        write_content=content,
        action="write_file",
        read_operation=False,
        scope="src/app.py",
        meta={"current_sha256": "new-hash"},
    )
    connector = FakeConnector(prepared, ExecResult(content=None))
    runtime.connectors["filesystem"] = connector
    task = asyncio.create_task(runtime.pipeline.handle(request(action="write_file")))
    approval_id = await wait_pending(runtime)
    pending = (await runtime.approvals.pending())[0]
    assert "STALE_MASKED_BASE" in pending.reason_codes
    assert Resolution.APPROVE_MASKED not in pending.options
    await runtime.approvals.resolve(approval_id, Resolution.DENY)
    result = await task
    assert result.status == "denied"
    event = runtime.audit.list_events()[0]
    assert "HUMAN_DENIED" in event.reason_codes
    assert connector.execute_calls == 0


async def test_system_event_is_recorded(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    event = await runtime.pipeline.record_system_event(
        session_id="default", action="rollback", target="src/pricing"
    )
    assert event.action == "rollback"
    assert runtime.audit.get_event(event.id) is not None
    assert runtime.audit.verify_chain().ok


async def test_error_message_does_not_leak_secret(tmp_path: Path) -> None:
    connector = FakeConnector(prepare_error=ActionError("failed on AKIAIOSFODNN7EXAMPLE"))
    runtime = make_runtime(tmp_path, connectors={"filesystem": connector})
    result = await runtime.pipeline.handle(request())
    assert "AKIAIOSFODNN7EXAMPLE" not in (result.notice or "")
    event = runtime.audit.list_events()[0]
    assert "AKIAIOSFODNN7EXAMPLE" not in event.model_dump_json()
