"""Snapshot creation and rollback."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import uuid
from pathlib import Path

from backend.core.audit import AuditLog, utc_now
from backend.core.models import SnapshotRecord

MAX_WORKSPACE_BYTES = 50 * 1024 * 1024


class SnapshotTooLargeError(RuntimeError):
    """Raised when a workspace snapshot would exceed the size limit."""


class SnapshotManager:
    """Create snapshots outside the workspace and restore them once."""

    def __init__(self, audit: AuditLog, data_dir: Path, workspace_root: Path) -> None:
        self._audit = audit
        self._data_dir = Path(data_dir)
        self._workspace = Path(workspace_root)
        self._root = self._data_dir / "snapshots"
        self._root.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        """Return a new snapshot id."""
        return f"snp_{uuid.uuid4().hex[:12]}"

    async def create_file(
        self,
        *,
        event_id: str,
        session_id: str,
        rel_path: str,
        abs_path: Path,
    ) -> SnapshotRecord:
        """Snapshot a single file before a write or delete."""
        return await asyncio.to_thread(
            self._create_file_sync, event_id, session_id, rel_path, abs_path
        )

    def _create_file_sync(
        self, event_id: str, session_id: str, rel_path: str, abs_path: Path
    ) -> SnapshotRecord:
        directory = self._root / event_id
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "file"
        existed = abs_path.is_file()
        if existed:
            shutil.copy2(abs_path, destination)
        return SnapshotRecord(
            id=self.new_id(),
            event_id=event_id,
            session_id=session_id,
            kind="file",
            target=rel_path,
            snapshot_path=str(destination),
            existed=existed,
            created_at=utc_now(),
        )

    async def create_database(
        self, *, event_id: str, session_id: str, source: Path, target: str
    ) -> SnapshotRecord:
        """Snapshot a SQLite database with the backup API."""
        return await asyncio.to_thread(
            self._create_database_sync, event_id, session_id, source, target
        )

    def _create_database_sync(
        self, event_id: str, session_id: str, source: Path, target: str
    ) -> SnapshotRecord:
        directory = self._root / event_id
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "database.db"
        source_conn = sqlite3.connect(source)
        destination_conn = sqlite3.connect(destination)
        try:
            source_conn.backup(destination_conn)
            destination_conn.commit()
        finally:
            source_conn.close()
            destination_conn.close()
        return SnapshotRecord(
            id=self.new_id(),
            event_id=event_id,
            session_id=session_id,
            kind="database",
            target=target,
            snapshot_path=str(destination),
            existed=True,
            created_at=utc_now(),
        )

    async def create_workspace(self, *, event_id: str, session_id: str) -> SnapshotRecord:
        """Snapshot the whole workspace before an approved destructive command."""
        return await asyncio.to_thread(self._create_workspace_sync, event_id, session_id)

    def _create_workspace_sync(self, event_id: str, session_id: str) -> SnapshotRecord:
        total = 0
        for path in self._workspace.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
                if total > MAX_WORKSPACE_BYTES:
                    raise SnapshotTooLargeError("workspace exceeds 50 MB")
        directory = self._root / event_id
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "workspace"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(self._workspace, destination)
        return SnapshotRecord(
            id=self.new_id(),
            event_id=event_id,
            session_id=session_id,
            kind="workspace",
            target="",
            snapshot_path=str(destination),
            existed=True,
            created_at=utc_now(),
        )

    async def rollback(self, snapshot: SnapshotRecord) -> None:
        """Restore a snapshot. Each snapshot can be rolled back once."""
        current = None
        if snapshot.event_id:
            for candidate in await self.for_event(snapshot.event_id):
                if candidate.id == snapshot.id:
                    current = candidate
                    break
        if (current and current.rolled_back_at) or snapshot.rolled_back_at:
            raise ValueError("snapshot has already been rolled back")
        await asyncio.to_thread(self._rollback_sync, snapshot)
        await asyncio.to_thread(self._audit.mark_snapshot_rolled_back, snapshot.id)

    def _rollback_sync(self, snapshot: SnapshotRecord) -> None:
        source = Path(snapshot.snapshot_path)
        if snapshot.kind == "file":
            target = self._workspace / snapshot.target
            if snapshot.existed:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            elif target.exists():
                target.unlink()
            return
        if snapshot.kind == "database":
            target = self._workspace / snapshot.target
            target.parent.mkdir(parents=True, exist_ok=True)
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(str(target) + suffix)
                if candidate.exists():
                    candidate.unlink()
            source_conn = sqlite3.connect(source)
            destination_conn = sqlite3.connect(target)
            try:
                source_conn.backup(destination_conn)
                destination_conn.commit()
            finally:
                source_conn.close()
                destination_conn.close()
            return
        if snapshot.kind == "workspace":
            for child in self._workspace.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            for child in source.iterdir():
                destination = self._workspace / child.name
                if child.is_dir():
                    shutil.copytree(child, destination)
                else:
                    shutil.copy2(child, destination)
            return
        raise ValueError(f"unknown snapshot kind {snapshot.kind}")

    async def for_event(self, event_id: str) -> list[SnapshotRecord]:
        """Return snapshots for an event."""
        return await asyncio.to_thread(self._audit.snapshots_for_event, event_id)

    async def undoable(self, event_id: str) -> SnapshotRecord | None:
        """Return a snapshot that has not been rolled back."""
        for snapshot in await self.for_event(event_id):
            if snapshot.rolled_back_at is None:
                return snapshot
        return None
