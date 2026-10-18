"""Pending approvals with console and web channels.

The first answer wins. A timeout is recorded as a timeout and behaves as a
denial. The console channel is only enabled on an interactive terminal so a
non-interactive demo or test cannot block on stdin.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backend.core.audit import AuditLog, utc_now
from backend.core.events import EventBus
from backend.core.models import ApprovalRecord, ConnectorName, Resolution, SignalScores
from backend.policy.models import ApprovalConfig

_STATUS_BY_RESOLUTION = {
    Resolution.APPROVE: "approved",
    Resolution.APPROVE_MASKED: "approved_masked",
    Resolution.DENY: "denied",
    Resolution.DENY_AND_LOCK: "denied",
}


@dataclass
class _Pending:
    future: asyncio.Future[Resolution | None]
    console_task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class ApprovalOutcome:
    """The answer to a held action."""

    approval_id: str
    resolution: Resolution | None
    resolved_by: str


class ApprovalManager:
    """Freeze a call until a human answers or the timeout fires."""

    def __init__(
        self,
        audit: AuditLog,
        bus: EventBus,
        config: ApprovalConfig,
        *,
        interactive: bool | None = None,
    ) -> None:
        self._audit = audit
        self._bus = bus
        self._config = config
        self._pending: dict[str, _Pending] = {}
        self._lock = asyncio.Lock()
        self._interactive = sys.stdin.isatty() if interactive is None else interactive

    def new_id(self) -> str:
        """Return a new approval id."""
        return f"apr_{uuid.uuid4().hex[:12]}"

    async def request(
        self,
        *,
        session_id: str,
        connector: ConnectorName,
        action: str,
        target: str,
        summary: str,
        tier: int,
        reason_codes: list[str],
        reason_text: str,
        scores: SignalScores,
        read_operation: bool,
    ) -> ApprovalOutcome:
        """Create an approval and wait for an answer.

        A timeout returns a resolution of ``None`` with ``resolved_by="timeout"``.
        """
        created = datetime.now(UTC)
        expires = created + timedelta(seconds=self._config.timeout_seconds)
        options = [Resolution.APPROVE, Resolution.DENY]
        if read_operation:
            options.insert(1, Resolution.APPROVE_MASKED)
        record = ApprovalRecord(
            id=self.new_id(),
            session_id=session_id,
            connector=connector,
            action=action,
            target=target,
            summary=summary[:300],
            tier=tier,
            reason_codes=reason_codes,
            reason_text=reason_text,
            scores=scores,
            options=options,
            status="pending",
            created_at=created.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            expires_at=expires.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Resolution | None] = loop.create_future()
        async with self._lock:
            self._pending[record.id] = _Pending(future=future)
        await asyncio.to_thread(self._audit.insert_approval, record)
        await self._bus.publish("approval.requested", {"approval": record.model_dump(mode="json")})

        if "console" in self._config.channels and self._interactive:
            console_task = asyncio.create_task(self._console_loop(record, read_operation))
            async with self._lock:
                if record.id in self._pending:
                    self._pending[record.id].console_task = console_task

        try:
            resolution = await asyncio.wait_for(
                asyncio.shield(future), timeout=self._config.timeout_seconds
            )
        except TimeoutError:
            await self._finish(record.id, None, "timeout", status="timeout")
            resolution = None
            resolved_by = "timeout"
        else:
            resolved_by = "web"
            stored = await asyncio.to_thread(self._audit.get_approval, record.id)
            if stored and stored.resolved_by:
                resolved_by = stored.resolved_by
        finally:
            await self._cleanup(record.id)
        return ApprovalOutcome(
            approval_id=record.id, resolution=resolution, resolved_by=resolved_by
        )

    async def resolve(
        self, approval_id: str, resolution: Resolution, *, resolved_by: str = "web"
    ) -> bool:
        """Resolve a pending approval. Returns False when it is not pending."""
        async with self._lock:
            pending = self._pending.get(approval_id)
        if pending is None:
            return await asyncio.to_thread(self._resolve_stored, approval_id, resolution, resolved_by)
        return await self._finish(approval_id, resolution, resolved_by)

    def _resolve_stored(self, approval_id: str, resolution: Resolution, resolved_by: str) -> bool:
        stored = self._audit.get_approval(approval_id)
        if stored is None or stored.status != "pending":
            return False
        status = _STATUS_BY_RESOLUTION[resolution]
        return self._audit.resolve_approval(
            approval_id,
            status=status,
            resolved_by=resolved_by,
            resolved_at=utc_now(),
        )

    async def _finish(
        self,
        approval_id: str,
        resolution: Resolution | None,
        resolved_by: str,
        *,
        status: str | None = None,
    ) -> bool:
        async with self._lock:
            pending = self._pending.get(approval_id)
            if pending is not None and pending.future.done():
                return False
            final_status = status or (
                _STATUS_BY_RESOLUTION[resolution] if resolution is not None else "timeout"
            )
            updated = await asyncio.to_thread(
                self._audit.resolve_approval,
                approval_id,
                status=final_status,
                resolved_by=resolved_by,
                resolved_at=utc_now(),
            )
            if not updated:
                return False
            if pending is not None and not pending.future.done():
                pending.future.set_result(resolution)
            if pending is not None and pending.console_task is not None:
                task = pending.console_task
                if task is not asyncio.current_task():
                    task.cancel()
        await self._bus.publish(
            "approval.resolved",
            {
                "approval_id": approval_id,
                "resolution": str(resolution) if resolution is not None else "timeout",
            },
        )
        return True

    async def _cleanup(self, approval_id: str) -> None:
        async with self._lock:
            pending = self._pending.pop(approval_id, None)
        if pending is not None and pending.console_task is not None:
            if pending.console_task is not asyncio.current_task():
                pending.console_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pending.console_task

    async def _console_loop(self, record: ApprovalRecord, read_operation: bool) -> None:
        prompt = (
            f"\n[RepoShield] HOLD  {record.connector}  {record.summary}\n"
            f"  {record.reason_text}\n"
            if read_operation
            else f"\n[RepoShield] HOLD  {record.connector}  {record.summary}\n  {record.reason_text}\n"
        )
        prompt += "  [y] approve   [m] approve with masking   [n] deny   > " if read_operation else (
            "  [y] approve   [n] deny   > "
        )
        try:
            answer = (await asyncio.to_thread(input, prompt)).strip().lower()
        except Exception:
            return
        if answer in {"y", "yes"}:
            await self.resolve(record.id, Resolution.APPROVE, resolved_by="console")
        elif answer in {"m", "mask"} and read_operation:
            await self.resolve(record.id, Resolution.APPROVE_MASKED, resolved_by="console")
        elif answer in {"n", "no"}:
            await self.resolve(record.id, Resolution.DENY, resolved_by="console")

    async def pending(self) -> list[ApprovalRecord]:
        """Return pending approvals, newest first."""
        return await asyncio.to_thread(self._audit.list_approvals, "pending")

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        """Return one approval."""
        return await asyncio.to_thread(self._audit.get_approval, approval_id)

    async def cancel_all(self) -> None:
        """Cancel every pending approval at shutdown."""
        for approval in await self.pending():
            await self._finish(approval.id, None, "shutdown", status="cancelled")
