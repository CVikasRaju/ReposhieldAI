"""Hash-chained audit ledger and persistence helpers.

The chain is appended under one asyncio lock so concurrent requests keep a
single linear history. Each append and its snapshot row share a transaction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.core.models import (
    ApprovalRecord,
    ChainVerification,
    DeltaRecord,
    EventRecord,
    SessionRecord,
    SignalScores,
    SnapshotRecord,
)

GENESIS = "0" * 64
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def utc_now() -> str:
    """Return the current UTC time as ISO-8601 with Z."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(row: dict[str, Any]) -> bytes:
    """Canonical JSON for a row, excluding seq and hash."""
    body = {key: value for key, value in row.items() if key not in ("seq", "hash")}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_hash(prev_hash: str, row: dict[str, Any]) -> str:
    """Compute the chain hash for a row."""
    return hashlib.sha256(prev_hash.encode("ascii") + canonical(row)).hexdigest()


class AuditLog:
    """SQLite-backed ledger and table access."""

    def __init__(self, db_path: Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._thread_lock = threading.RLock()
        self._async_lock = asyncio.Lock()
        with self._thread_lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            self._conn.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        """The shared connection, used by the mask map store."""
        return self._conn

    @property
    def thread_lock(self) -> threading.RLock:
        """The lock guarding the shared connection."""
        return self._thread_lock

    def close(self) -> None:
        """Close the underlying connection."""
        with self._thread_lock:
            self._conn.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """Execute a write statement and commit."""
        with self._thread_lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        """Fetch one row."""
        with self._thread_lock:
            return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Fetch all rows."""
        with self._thread_lock:
            return list(self._conn.execute(sql, params).fetchall())

    def _serialise_event(self, event: EventRecord) -> dict[str, Any]:
        return {
            "id": event.id,
            "session_id": event.session_id,
            "ts": event.ts,
            "connector": str(event.connector),
            "action": event.action,
            "target": event.target,
            "task_context": event.task_context,
            "risk_tier": event.risk_tier,
            "scores_json": json.dumps(event.scores.model_dump(), sort_keys=True, separators=(",", ":")),
            "decision": str(event.decision),
            "final_outcome": str(event.final_outcome),
            "approval": str(event.approval),
            "reason_codes_json": json.dumps(event.reason_codes, separators=(",", ":")),
            "reason_text": event.reason_text,
            "detections_json": json.dumps(event.detections, separators=(",", ":"), ensure_ascii=False),
            "flags_json": json.dumps(event.flags, separators=(",", ":")),
            "content_sha256": event.content_sha256,
            "delta_sha256": event.delta_sha256,
            "latency_ms": event.latency_ms,
        }

    async def append_event(
        self,
        event: EventRecord,
        *,
        delta: DeltaRecord | None = None,
        snapshot: SnapshotRecord | None = None,
    ) -> EventRecord:
        """Append an event to the chain, optionally with its delta and snapshot."""
        async with self._async_lock:
            return await asyncio.to_thread(self._append_event_sync, event, delta, snapshot)

    def _append_event_sync(
        self,
        event: EventRecord,
        delta: DeltaRecord | None,
        snapshot: SnapshotRecord | None,
    ) -> EventRecord:
        with self._thread_lock, self._conn:
            last = self._conn.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = str(last["hash"]) if last else GENESIS
            row = self._serialise_event(event)
            row["prev_hash"] = prev_hash
            row["hash"] = compute_hash(prev_hash, row)
            columns = list(row.keys())
            placeholders = ", ".join("?" for _ in columns)
            cursor = self._conn.execute(
                f"INSERT INTO events ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(row[column] for column in columns),
            )
            seq = int(cursor.lastrowid or 0)
            if delta is not None:
                self._conn.execute(
                    "INSERT INTO deltas (event_id, raw_text, sanitized_text, changes_json) VALUES (?, ?, ?, ?)",
                    (
                        event.id,
                        delta.raw,
                        delta.sanitized,
                        json.dumps([change.model_dump() for change in delta.changes], separators=(",", ":")),
                    ),
                )
            if snapshot is not None:
                self._insert_snapshot_sync(snapshot, event.id)
            self._conn.execute(
                "INSERT INTO chain_head (id, seq, hash) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET seq=excluded.seq, hash=excluded.hash",
                (seq, row["hash"]),
            )
        return event.model_copy(update={"seq": seq, "prev_hash": prev_hash, "hash": row["hash"]})

    def _insert_snapshot_sync(self, snapshot: SnapshotRecord, event_id: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO snapshots "
            "(id, event_id, session_id, kind, target, snapshot_path, existed, created_at, rolled_back_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot.id,
                event_id,
                snapshot.session_id,
                snapshot.kind,
                snapshot.target,
                snapshot.snapshot_path,
                1 if snapshot.existed else 0,
                snapshot.created_at,
                snapshot.rolled_back_at,
            ),
        )

    # ------------------------------------------------------------------ events

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            seq=int(row["seq"]),
            id=str(row["id"]),
            session_id=str(row["session_id"]),
            ts=str(row["ts"]),
            connector=str(row["connector"]),
            action=str(row["action"]),
            target=str(row["target"]),
            task_context=row["task_context"],
            risk_tier=int(row["risk_tier"]),
            scores=SignalScores(**json.loads(row["scores_json"])),
            decision=str(row["decision"]),
            final_outcome=str(row["final_outcome"]),
            approval=str(row["approval"]),
            reason_codes=list(json.loads(row["reason_codes_json"])),
            reason_text=str(row["reason_text"]),
            detections=list(json.loads(row["detections_json"])),
            flags=list(json.loads(row["flags_json"])),
            content_sha256=row["content_sha256"],
            delta_sha256=row["delta_sha256"],
            latency_ms=row["latency_ms"],
            prev_hash=str(row["prev_hash"]),
            hash=str(row["hash"]),
        )

    def list_events(
        self,
        *,
        session_id: str | None = None,
        limit: int = 100,
        after_seq: int | None = None,
        decision: str | None = None,
    ) -> list[EventRecord]:
        """Return the newest events, ordered oldest to newest."""
        conditions: list[str] = []
        params: list[Any] = []
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if after_seq is not None:
            conditions.append("seq > ?")
            params.append(after_seq)
        if decision:
            conditions.append("decision = ?")
            params.append(decision.upper())
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.fetchall(
            f"SELECT * FROM (SELECT * FROM events {where} ORDER BY seq DESC LIMIT ?) ORDER BY seq ASC",
            (*params, max(1, limit)),
        )
        return [self._row_to_event(row) for row in rows]

    def get_event(self, event_id: str) -> EventRecord | None:
        """Return one event by id."""
        row = self.fetchone("SELECT * FROM events WHERE id = ?", (event_id,))
        return self._row_to_event(row) if row else None

    def get_delta(self, event_id: str) -> DeltaRecord | None:
        """Return the raw and sanitized delta for an event."""
        row = self.fetchone("SELECT * FROM deltas WHERE event_id = ?", (event_id,))
        if not row:
            return None
        return DeltaRecord(
            event_id=str(row["event_id"]),
            raw=str(row["raw_text"]),
            sanitized=str(row["sanitized_text"]),
            changes=list(json.loads(row["changes_json"])),
        )

    def metrics(self, session_id: str | None = None) -> dict[str, Any]:
        """Counts per decision and latency statistics."""
        where = "WHERE session_id = ?" if session_id else ""
        params: tuple[Any, ...] = (session_id,) if session_id else ()
        counts = {row["decision"]: int(row["count"]) for row in self.fetchall(
            f"SELECT decision, COUNT(*) AS count FROM events {where} GROUP BY decision", params
        )}
        latencies = [
            float(row["latency_ms"])
            for row in self.fetchall(
                f"SELECT latency_ms FROM events {where} AND latency_ms IS NOT NULL"
                if session_id
                else "SELECT latency_ms FROM events WHERE latency_ms IS NOT NULL",
                params,
            )
        ]
        latencies.sort()
        mean = sum(latencies) / len(latencies) if latencies else 0.0
        if latencies:
            index = min(len(latencies) - 1, max(0, int(round(0.95 * (len(latencies) - 1)))))
            p95 = latencies[index]
        else:
            p95 = 0.0
        return {
            "session_id": session_id,
            "counts": {
                "ALLOW": counts.get("ALLOW", 0),
                "MASK": counts.get("MASK", 0),
                "HOLD": counts.get("HOLD", 0),
                "BLOCK": counts.get("BLOCK", 0),
            },
            "total": sum(counts.values()),
            "latency_mean_ms": round(mean, 3),
            "latency_p95_ms": round(p95, 3),
        }

    # ---------------------------------------------------------------- sessions

    def insert_session(self, session: SessionRecord) -> None:
        """Insert a session row."""
        self.execute(
            "INSERT OR IGNORE INTO sessions "
            "(id, name, profile, task, status, locked_reason, agent_pid, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.id,
                session.name,
                session.profile,
                session.task,
                session.status,
                session.locked_reason,
                session.agent_pid,
                session.started_at,
                session.ended_at,
            ),
        )

    def get_session(self, session_id: str) -> SessionRecord | None:
        """Return one session."""
        row = self.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return self._row_to_session(row) if row else None

    def list_sessions(self) -> list[SessionRecord]:
        """Return all sessions, newest first."""
        return [
            self._row_to_session(row)
            for row in self.fetchall("SELECT * FROM sessions ORDER BY started_at DESC, id DESC")
        ]

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            id=str(row["id"]),
            name=row["name"],
            profile=str(row["profile"]),
            task=row["task"],
            status=str(row["status"]),
            locked_reason=row["locked_reason"],
            agent_pid=row["agent_pid"],
            started_at=str(row["started_at"]),
            ended_at=row["ended_at"],
        )

    def update_session(
        self,
        session_id: str,
        *,
        status: str,
        locked_reason: str | None = None,
        ended_at: str | None = None,
    ) -> None:
        """Update a session's status."""
        self.execute(
            "UPDATE sessions SET status = ?, locked_reason = ?, ended_at = COALESCE(?, ended_at) WHERE id = ?",
            (status, locked_reason, ended_at, session_id),
        )

    # --------------------------------------------------------------- approvals

    def insert_approval(self, approval: ApprovalRecord) -> None:
        """Insert a pending approval."""
        self.execute(
            "INSERT INTO approvals "
            "(id, session_id, connector, action, target, summary, tier, reason_codes_json, reason_text, "
            "scores_json, options_json, status, resolved_by, created_at, expires_at, resolved_at, event_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                approval.id,
                approval.session_id,
                str(approval.connector),
                approval.action,
                approval.target,
                approval.summary,
                approval.tier,
                json.dumps(approval.reason_codes, separators=(",", ":")),
                approval.reason_text,
                json.dumps(approval.scores.model_dump(), separators=(",", ":")),
                json.dumps([str(option) for option in approval.options], separators=(",", ":")),
                approval.status,
                approval.resolved_by,
                approval.created_at,
                approval.expires_at,
                approval.resolved_at,
                approval.event_id,
            ),
        )

    @staticmethod
    def _row_to_approval(row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            id=str(row["id"]),
            session_id=str(row["session_id"]),
            connector=str(row["connector"]),
            action=str(row["action"]),
            target=str(row["target"]),
            summary=str(row["summary"]),
            tier=int(row["tier"]),
            reason_codes=list(json.loads(row["reason_codes_json"])),
            reason_text=str(row["reason_text"]),
            scores=SignalScores(**json.loads(row["scores_json"])),
            options=list(json.loads(row["options_json"])),
            status=str(row["status"]),
            resolved_by=row["resolved_by"],
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
            resolved_at=row["resolved_at"],
            event_id=row["event_id"],
        )

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        """Return one approval."""
        row = self.fetchone("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return self._row_to_approval(row) if row else None

    def list_approvals(self, status: str | None = None) -> list[ApprovalRecord]:
        """Return approvals, newest first."""
        if status:
            rows = self.fetchall(
                "SELECT * FROM approvals WHERE status = ? ORDER BY created_at DESC", (status,)
            )
        else:
            rows = self.fetchall("SELECT * FROM approvals ORDER BY created_at DESC")
        return [self._row_to_approval(row) for row in rows]

    def resolve_approval(
        self,
        approval_id: str,
        *,
        status: str,
        resolved_by: str,
        resolved_at: str,
        event_id: str | None = None,
    ) -> bool:
        """Mark a pending approval resolved. Returns False if it was not pending."""
        with self._thread_lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE approvals SET status = ?, resolved_by = ?, resolved_at = ?, "
                "event_id = COALESCE(?, event_id) WHERE id = ? AND status = 'pending'",
                (status, resolved_by, resolved_at, event_id, approval_id),
            )
            return cursor.rowcount == 1

    def attach_approval_event(self, approval_id: str, event_id: str) -> None:
        """Link a resolved approval to its event."""
        self.execute("UPDATE approvals SET event_id = ? WHERE id = ?", (event_id, approval_id))

    # --------------------------------------------------------------- snapshots

    def insert_snapshot(self, snapshot: SnapshotRecord) -> None:
        """Insert a snapshot row linked to an existing event."""
        with self._thread_lock, self._conn:
            self._insert_snapshot_sync(snapshot, snapshot.event_id or "")

    def snapshots_for_event(self, event_id: str) -> list[SnapshotRecord]:
        """Return snapshots linked to an event."""
        rows = self.fetchall("SELECT * FROM snapshots WHERE event_id = ? ORDER BY created_at", (event_id,))
        return [self._row_to_snapshot(row) for row in rows]

    def snapshots_for_session(self, session_id: str) -> list[SnapshotRecord]:
        """Return snapshots for a session."""
        rows = self.fetchall(
            "SELECT * FROM snapshots WHERE session_id = ? ORDER BY created_at", (session_id,)
        )
        return [self._row_to_snapshot(row) for row in rows]

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row) -> SnapshotRecord:
        return SnapshotRecord(
            id=str(row["id"]),
            event_id=row["event_id"],
            session_id=str(row["session_id"]),
            kind=str(row["kind"]),
            target=str(row["target"]),
            snapshot_path=str(row["snapshot_path"]),
            existed=bool(row["existed"]),
            created_at=str(row["created_at"]),
            rolled_back_at=row["rolled_back_at"],
        )

    def mark_snapshot_rolled_back(self, snapshot_id: str) -> None:
        """Mark one snapshot as rolled back."""
        self.execute(
            "UPDATE snapshots SET rolled_back_at = ? WHERE id = ? AND rolled_back_at IS NULL",
            (utc_now(), snapshot_id),
        )

    # --------------------------------------------------------------- integrity

    def verify_chain(self, db_path: Path | None = None) -> ChainVerification:
        """Recompute the hash chain and compare it with the stored head."""
        return verify_chain(db_path or self.path)


def verify_chain(db_path: Path) -> ChainVerification:
    """Recompute the hash chain in a fresh connection."""
    connection = sqlite3.connect(Path(db_path))
    connection.row_factory = sqlite3.Row
    try:
        try:
            rows = connection.execute("SELECT * FROM events ORDER BY seq ASC").fetchall()
            head = connection.execute("SELECT seq, hash FROM chain_head WHERE id = 1").fetchone()
        except sqlite3.DatabaseError as exc:
            return ChainVerification(ok=False, checked=0, reason=f"database_error: {exc}")
        previous_hash = GENESIS
        checked = 0
        last_seq: int | None = None
        for row in rows:
            row_dict = dict(row)
            sequence = int(row_dict["seq"])
            if row_dict["prev_hash"] != previous_hash:
                return ChainVerification(
                    ok=False, checked=checked, first_bad_seq=sequence, reason="prev_hash_mismatch"
                )
            hash_row = {key: value for key, value in row_dict.items() if key not in ("seq", "hash")}
            if compute_hash(previous_hash, hash_row) != row_dict["hash"]:
                return ChainVerification(
                    ok=False, checked=checked, first_bad_seq=sequence, reason="hash_mismatch"
                )
            previous_hash = str(row_dict["hash"])
            last_seq = sequence
            checked += 1
        if head is None:
            if checked:
                return ChainVerification(
                    ok=False, checked=checked, first_bad_seq=last_seq, reason="head_mismatch"
                )
            return ChainVerification(ok=True, checked=0)
        if last_seq != int(head["seq"]) or previous_hash != str(head["hash"]):
            return ChainVerification(
                ok=False, checked=checked, first_bad_seq=last_seq, reason="head_mismatch"
            )
        return ChainVerification(ok=True, checked=checked)
    finally:
        connection.close()
