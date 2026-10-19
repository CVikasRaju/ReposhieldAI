"""Process watchdog for bypass attempts (Phase 9)."""

import os
import subprocess
import uuid
from datetime import UTC, datetime
from typing import Any

from backend.core.audit import AuditLog
from backend.core.models import Decision, EventRecord, FinalOutcome, ReasonCode
from backend.core.sessions import SessionManager


def utc_now() -> float:
    """Return current UTC timestamp in seconds."""
    return datetime.now(UTC).timestamp()


class ProcessMonitor:
    """Monitors child processes for disallowed commands."""

    def __init__(self, audit: AuditLog, sessions: SessionManager, bus: Any) -> None:
        self.audit = audit
        self.sessions = sessions
        self.bus = bus
        self.watchdog_event_id: str | None = None

    async def start_watching(self, session_id: str) -> None:
        """Start monitoring the process group for the given session."""
        try:
            session = await self.sessions.get(session_id)
            if session is None:
                return

            # Set up a handler to monitor subprocesses
            # This is a best-effort monitor; true container-level isolation
            # is handled by Docker/Kubernetes
            pid = os.getpid()

            # Check for any sh or curl children
            try:
                result = subprocess.run(
                    ["pgrep", "-P", str(pid), "sh", "curl"],
                    capture_output=True, text=True, timeout=5
                )
                if result.stdout.strip():
                    # Watchdog alert triggered
                    await self._raise_watchdog_alert(
                        session_id,
                        "spawned sh or curl child process",
                        ReasonCode.WATCHDOG_ALERT
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass  # pgrep not available or no children
        except Exception:
            pass  # Fail silently

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
            connector=("system",),
            action="watchdog_alert",
            target="session",
            task_context=None,
            risk_tier=5,
            scores=(),
            decision=Decision.BLOCK,
            final_outcome=FinalOutcome.BLOCKED,
            approval=("auto",),
            reason_codes=[reason_code],
            reason_text=reason_text,
            detections=[],
            flags=[],
            content_sha256=None,
            delta_sha256=None,
            latency_ms=0.0,
        )
        try:
            logged = await self.audit.append_event(event)
            await self.bus.publish("event.created", {"event": logged.model_dump(mode="json")})
            self.watchdog_event_id = logged.id
        except Exception:
            pass


def watchdog_decorator(func):
    """Decorator to wrap test functions with watchdog monitoring."""
    async def wrapper(*args, **kwargs):
        from backend.core.audit import AuditLog
        from backend.core.events import EventBus
        from backend.core.sessions import SessionManager

        audit = AuditLog(":memory:")
        sessions = SessionManager()
        bus = EventBus()
        monitor = ProcessMonitor(audit, sessions, bus)

        # Run the decorated function
        try:
            result = await func(*args, **kwargs)
            # Check for watchdog alerts after the function completes
            await monitor.start_watching(kwargs.get("session_id", "default"))
            return result
        except Exception:
            # Log any watchdog events that were raised
            if monitor.watchdog_event_id:
                pass  # Event already logged
            raise

    return wrapper