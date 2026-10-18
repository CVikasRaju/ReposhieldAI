"""Session creation, locking and unlocking."""

from __future__ import annotations

import asyncio

from backend.core.audit import AuditLog, utc_now
from backend.core.models import SessionRecord
from backend.engines.behaviour import BehaviourTracker


class SessionManager:
    """Create sessions on first sight and track their status."""

    def __init__(self, audit: AuditLog, behaviour: BehaviourTracker) -> None:
        self._audit = audit
        self._behaviour = behaviour

    async def get_or_create(
        self, session_id: str, *, profile: str, task: str | None = None
    ) -> SessionRecord:
        """Return a session, creating an active row on first sight."""
        existing = await asyncio.to_thread(self._audit.get_session, session_id)
        if existing is not None:
            return existing
        record = SessionRecord(
            id=session_id,
            name=session_id,
            profile=profile,
            task=task,
            status="active",
            started_at=utc_now(),
        )
        await asyncio.to_thread(self._audit.insert_session, record)
        return record

    async def get(self, session_id: str) -> SessionRecord | None:
        """Return one session."""
        return await asyncio.to_thread(self._audit.get_session, session_id)

    async def list(self) -> list[SessionRecord]:
        """Return all sessions, newest first."""
        return await asyncio.to_thread(self._audit.list_sessions)

    async def lock(self, session_id: str, reason: str) -> None:
        """Lock a session."""
        await asyncio.to_thread(
            self._audit.update_session, session_id, status="locked", locked_reason=reason
        )

    async def unlock(self, session_id: str) -> None:
        """Unlock a session and clear its behaviour window."""
        await asyncio.to_thread(
            self._audit.update_session, session_id, status="active", locked_reason=None
        )
        self._behaviour.reset(session_id)

    async def end(self, session_id: str) -> None:
        """End a session."""
        await asyncio.to_thread(
            self._audit.update_session,
            session_id,
            status="ended",
            ended_at=utc_now(),
        )

    async def is_locked(self, session_id: str) -> bool:
        """Return True when the session is locked."""
        session = await self.get(session_id)
        return bool(session and session.status == "locked")
