"""Process watchdog for bypass attempts (Phase 9, optional C11).

This is a best-effort detection layer for local mode. Container isolation is
enforced by docker-compose, not by this module, and the gateway does not start
it automatically.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import psutil

from backend.core.audit import AuditLog
from backend.core.models import (
    ApprovalKind,
    ConnectorName,
    Decision,
    EventRecord,
    FinalOutcome,
    SignalScores,
)
from backend.core.reasons import ReasonCode
from backend.core.sessions import SessionManager

SHELL_NAMES = {"sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh"}
NETWORK_NAMES = {"curl", "wget", "nc", "ncat", "ssh", "scp", "socat"}


def utc_now() -> str:
    """Return the current UTC timestamp in the ledger's ISO format."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def suspicious_children(pid: int) -> list[str]:
    """Return child process names that look like shell or network bypasses."""
    try:
        parent = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []
    hits: list[str] = []
    for child in parent.children(recursive=True):
        try:
            name = child.name().lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name in SHELL_NAMES or name in NETWORK_NAMES:
            hits.append(name)
    return hits


class ProcessMonitor:
    """Monitors child processes for disallowed commands."""

    def __init__(self, audit: AuditLog, sessions: SessionManager, bus: Any) -> None:
        self.audit = audit
        self.sessions = sessions
        self.bus = bus
        self.watchdog_event_id: str | None = None

    async def start_watching(self, session_id: str, pid: int | None = None) -> None:
        """Check one process tree for shell or network children and log a hit."""
        session = await self.sessions.get(session_id)
        if session is None:
            return
        watched = pid if pid is not None else psutil.Process().pid
        hits = suspicious_children(watched)
        if hits:
            await self._raise_watchdog_alert(
                session_id,
                f"spawned disallowed child processes: {', '.join(sorted(set(hits)))}",
                ReasonCode.WATCHDOG_ALERT.value,
            )

    async def _raise_watchdog_alert(
        self,
        session_id: str,
        reason_text: str,
        reason_code: str
    ) -> None:
        """Raise a watchdog alert event."""
        event = EventRecord(
            id=f"evt_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            ts=utc_now(),
            connector=ConnectorName.SYSTEM,
            action="watchdog_alert",
            target="session",
            task_context=None,
            risk_tier=5,
            scores=SignalScores(),
            decision=Decision.BLOCK,
            final_outcome=FinalOutcome.BLOCKED,
            approval=ApprovalKind.AUTO,
            reason_codes=[reason_code],
            reason_text=reason_text,
            detections=[],
            flags=[],
            content_sha256=None,
            delta_sha256=None,
            latency_ms=0.0,
        )
        logged = await self.audit.append_event(event)
        await self.bus.publish("event.created", {"event": logged.model_dump(mode="json")})
        self.watchdog_event_id = logged.id