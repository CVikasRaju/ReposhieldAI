"""Shared test fixtures for the core pipeline tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.core.approvals import ApprovalManager
from backend.core.audit import AuditLog, utc_now
from backend.core.events import EventBus
from backend.core.models import (
    ActionRequest,
    ApprovalKind,
    ConnectorName,
    Decision,
    EventRecord,
    ExecResult,
    ExecutionPlan,
    FinalOutcome,
    Prepared,
    RuleVerdict,
    SessionRecord,
    SignalScores,
)
from backend.core.pipeline import Pipeline
from backend.core.sessions import SessionManager
from backend.core.snapshots import SnapshotManager
from backend.engines.behaviour import BehaviourTracker
from backend.engines.content_model import ContentModel
from backend.engines.text_masker import MaskMapStore
from backend.policy.loader import load_policy
from backend.policy.models import ApprovalConfig, Policy

ROOT = Path(__file__).resolve().parents[2]


class FakeConnector:
    """A connector whose prepare and execute results are controlled by a test."""

    name = "filesystem"

    def __init__(
        self,
        prepared: Prepared | None = None,
        result: ExecResult | None = None,
        *,
        prepare_hook: Callable[[ActionRequest], Prepared] | None = None,
        prepare_error: Exception | None = None,
        execute_error: Exception | None = None,
    ) -> None:
        self.prepared = prepared or make_prepared()
        self.result = result
        self.prepare_hook = prepare_hook
        self.prepare_error = prepare_error
        self.execute_error = execute_error
        self.prepare_calls = 0
        self.execute_calls = 0
        self.last_plan: ExecutionPlan | None = None

    async def prepare(self, req: ActionRequest) -> Prepared:
        self.prepare_calls += 1
        if self.prepare_error:
            raise self.prepare_error
        if self.prepare_hook:
            return self.prepare_hook(req)
        return self.prepared.model_copy(
            update={
                "connector": req.connector,
                "action": req.action,
                "target": req.target or self.prepared.target,
            }
        )

    async def execute(self, prepared: Prepared, plan: ExecutionPlan) -> ExecResult:
        self.execute_calls += 1
        self.last_plan = plan
        if self.execute_error:
            raise self.execute_error
        return self.result or ExecResult(content=prepared.content)


@dataclass
class Runtime:
    tmp: Path
    workspace: Path
    data: Path
    policy: Policy
    audit: AuditLog
    bus: EventBus
    behaviour: BehaviourTracker
    sessions: SessionManager
    approvals: ApprovalManager
    snapshots: SnapshotManager
    store: MaskMapStore
    pipeline: Pipeline
    connectors: dict[str, FakeConnector] = field(default_factory=dict)


def make_prepared(
    *,
    content: str | None = "hello world",
    content_kind: str = "text",
    scope: str = "src/app.py",
    action: str = "read_file",
    tier: int = 0,
    read_operation: bool = True,
    rule: RuleVerdict | None = None,
    meta: dict[str, Any] | None = None,
    write_content: str | None = None,
    needs_snapshot: bool = False,
    snapshot_kind: str | None = None,
    snapshot_target: str = "",
) -> Prepared:
    return Prepared(
        connector=ConnectorName.FILESYSTEM,
        action=action,
        target=scope,
        display_target=scope,
        scope=scope,
        tier=tier,
        read_operation=read_operation,
        content=content,
        content_kind=content_kind,  # type: ignore[arg-type]
        write_content=write_content,
        rule=rule or RuleVerdict(),
        meta=meta or {},
        needs_snapshot=needs_snapshot,
        snapshot_kind=snapshot_kind,  # type: ignore[arg-type]
        snapshot_target=snapshot_target,
    )


def make_runtime(
    tmp_path: Path,
    *,
    policy: Policy | None = None,
    approval_timeout: int = 30,
    interactive: bool = False,
    connectors: dict[str, FakeConnector] | None = None,
) -> Runtime:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    base_policy = policy or load_policy(ROOT / ".aishield")
    base_policy = base_policy.model_copy(
        update={"approval": ApprovalConfig(timeout_seconds=approval_timeout)}
    )
    audit = AuditLog(data / "audit.db")
    audit.insert_session(
        SessionRecord(id="default", name="default", profile=base_policy.profile, started_at=utc_now())
    )
    bus = EventBus()
    behaviour = BehaviourTracker(base_policy.behaviour, base_policy.kill_switch)
    sessions = SessionManager(audit, behaviour)
    approvals = ApprovalManager(audit, bus, base_policy.approval, interactive=interactive)
    snapshots = SnapshotManager(audit, data, workspace)
    store = MaskMapStore(audit.connection, audit.thread_lock)
    registry: dict[str, FakeConnector] = connectors or {"filesystem": FakeConnector()}
    pipeline = Pipeline(
        policy=base_policy,
        audit=audit,
        sessions=sessions,
        approvals=approvals,
        snapshots=snapshots,
        bus=bus,
        connectors=registry,
        content_model=ContentModel(tmp_path / "missing-model.joblib"),
        behaviour=behaviour,
        store=store,
        workspace_root=workspace,
    )
    return Runtime(
        tmp=tmp_path,
        workspace=workspace,
        data=data,
        policy=base_policy,
        audit=audit,
        bus=bus,
        behaviour=behaviour,
        sessions=sessions,
        approvals=approvals,
        snapshots=snapshots,
        store=store,
        pipeline=pipeline,
        connectors=registry,
    )


def request(
    target: str = "src/app.py",
    *,
    connector: ConnectorName = ConnectorName.FILESYSTEM,
    action: str = "read_file",
) -> ActionRequest:
    return ActionRequest(
        session_id="default",
        connector=connector,
        action=action,
        target=target,
    )


async def wait_pending(runtime: Runtime, timeout: float = 3.0) -> str:
    """Wait for a pending approval and return its id."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        pending = await runtime.approvals.pending()
        if pending:
            return pending[0].id
        await asyncio.sleep(0.01)
    raise AssertionError("no pending approval appeared")


def make_event(index: int, session_id: str = "default") -> EventRecord:
    return EventRecord(
        id=f"evt_{index:012x}",
        session_id=session_id,
        ts=utc_now(),
        connector=ConnectorName.SYSTEM,
        action="test",
        target="target",
        task_context="test",
        risk_tier=0,
        scores=SignalScores(),
        decision=Decision.ALLOW,
        final_outcome=FinalOutcome.EXECUTED,
        approval=ApprovalKind.AUTO,
        reason_codes=["ALLOWED"],
        reason_text="Allowed.",
    )
