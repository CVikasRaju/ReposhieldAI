"""Dashboard REST API and WebSocket.

The human-facing listener serves only ``/api/*`` and ``/ws``. Every HTTP
request on the API surface and every WebSocket connection must present the
approver token.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.config import Settings
from backend.core.approvals import ApprovalManager
from backend.core.audit import AuditLog, utc_now
from backend.core.events import EventBus
from backend.core.explain import explain
from backend.core.models import (
    ApprovalKind,
    ApprovalRecord,
    ChainVerification,
    ConnectorName,
    Decision,
    EventRecord,
    FinalOutcome,
    Resolution,
    SignalScores,
)
from backend.core.reasons import ReasonCode
from backend.core.sessions import SessionManager
from backend.core.snapshots import SnapshotManager
from backend.policy.models import Policy
from backend.policy.sample import get_sample_policy_yaml, write_sample_policy

VERSION = "0.1.0"
DASHBOARD_ORIGIN = "http://localhost:5173"
STATE_RANK = {"unvisited": 0, "accessed": 1, "masked": 2, "held": 3, "blocked": 4}


@dataclass(frozen=True)
class DashboardDeps:
    """The gateway services the dashboard needs."""

    settings: Settings
    policy: Policy
    audit: AuditLog
    approvals: ApprovalManager
    sessions: SessionManager
    snapshots: SnapshotManager
    bus: EventBus


class CreatePolicySampleRequest(BaseModel):
    """Request to create or initialize a sample .aishield file."""

    overwrite: bool = False


class ResolveRequest(BaseModel):
    """The human's answer for one approval."""

    resolution: Resolution


def _token_matches(supplied: str, expected: str) -> bool:
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message})


def _state_for_event(event: EventRecord) -> str:
    """Map one event to the graph state it implies."""
    if event.decision == Decision.BLOCK or event.final_outcome in {
        FinalOutcome.BLOCKED,
        FinalOutcome.TERMINATED,
    }:
        return "blocked"
    if event.decision == Decision.HOLD or event.final_outcome in {
        FinalOutcome.DENIED,
        FinalOutcome.TIMEOUT,
    }:
        return "held"
    if event.decision == Decision.MASK or event.final_outcome == FinalOutcome.EXECUTED_MASKED:
        return "masked"
    return "accessed"


def _most_severe(current: str, new: str) -> str:
    return new if STATE_RANK[new] > STATE_RANK[current] else current


def _normalise_rel(target: str) -> str | None:
    value = target.replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    value = value.strip("/")
    if value in {"", "."}:
        return None
    return value


def _event_node_id(event: EventRecord, nodes: dict[str, dict[str, Any]]) -> str | None:
    """Return the graph node an event applies to, when there is one."""
    if event.connector == ConnectorName.FILESYSTEM:
        rel = _normalise_rel(event.target)
        if rel and rel in nodes:
            return rel
        return None
    if event.connector == ConnectorName.DATABASE:
        table = event.target.strip()
        node_id = f"db:{table}"
        return node_id if node_id in nodes else None
    if event.connector == ConnectorName.NETWORK:
        host = event.target.strip()
        node_id = f"host:{host}"
        return node_id if node_id in nodes else None
    return None


def _workspace_nodes(workspace: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Build directory and file nodes for the workspace tree."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    if not workspace.is_dir():
        return nodes, edges
    for path in sorted(workspace.rglob("*")):
        rel = path.relative_to(workspace).as_posix()
        parts = rel.split("/")
        if not parts or parts[0] in {"data"} or parts[-1] == ".aishield":
            continue
        if rel == "business.db":
            continue
        node_type = "dir" if path.is_dir() else "file"
        parent = str(PurePosixPath(rel).parent)
        parent_id = None if parent in {"", "."} else parent
        nodes[rel] = {
            "id": rel,
            "label": path.name,
            "type": node_type,
            "parent": parent_id,
            "state": "unvisited",
            "last_ts": None,
        }
        if parent_id is not None:
            edges.append({"source": parent_id, "target": rel})
    return nodes, edges


def _database_nodes(database: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Build database and table nodes from the SQLite schema."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    if not database.is_file():
        return nodes, edges
    database_id = f"db:{database.name}"
    nodes[database_id] = {
        "id": database_id,
        "label": database.name,
        "type": "database",
        "parent": None,
        "state": "unvisited",
        "last_ts": None,
    }
    try:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        return nodes, edges
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
    except sqlite3.Error:
        tables = []
    finally:
        connection.close()
    for table in tables:
        node_id = f"db:{table}"
        nodes[node_id] = {
            "id": node_id,
            "label": table,
            "type": "table",
            "parent": database_id,
            "state": "unvisited",
            "last_ts": None,
        }
        edges.append({"source": database_id, "target": node_id})
    return nodes, edges


def _host_nodes(events: list[EventRecord]) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Build external-host nodes from network events."""
    nodes: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.connector != ConnectorName.NETWORK:
            continue
        host = event.target.strip()
        if not host:
            continue
        node_id = f"host:{host}"
        nodes.setdefault(
            node_id,
            {
                "id": node_id,
                "label": host,
                "type": "host",
                "parent": None,
                "state": "unvisited",
                "last_ts": None,
            },
        )
    return nodes, []


def build_graph(
    workspace: Path,
    database: Path,
    events: list[EventRecord],
) -> dict[str, list[dict[str, Any]]]:
    """Build the knowledge graph and apply the most severe event per node."""
    nodes, edges = _workspace_nodes(workspace)
    db_nodes, db_edges = _database_nodes(database)
    nodes.update(db_nodes)
    edges.extend(db_edges)
    host_nodes, _ = _host_nodes(events)
    nodes.update(host_nodes)

    for event in events:
        node_id = _event_node_id(event, nodes)
        if node_id is None:
            continue
        node = nodes[node_id]
        node["state"] = _most_severe(node["state"], _state_for_event(event))
        if node["last_ts"] is None or event.ts > node["last_ts"]:
            node["last_ts"] = event.ts

    # Directories and databases reflect their most severe child.
    children: dict[str, list[str]] = {}
    for node in nodes.values():
        parent = node.get("parent")
        if parent:
            children.setdefault(parent, []).append(node["id"])

    def aggregate(node_id: str, seen: set[str]) -> str:
        if node_id in seen:
            return nodes[node_id]["state"]
        seen.add(node_id)
        state = nodes[node_id]["state"]
        for child in children.get(node_id, []):
            state = _most_severe(state, aggregate(child, seen))
        nodes[node_id]["state"] = state
        return state

    for node_id in list(nodes):
        aggregate(node_id, set())

    ordered = sorted(nodes.values(), key=lambda item: (item["type"], item["id"]))
    return {"nodes": ordered, "edges": edges}


def create_api_app(deps: DashboardDeps) -> FastAPI:
    """Build the human-facing FastAPI application."""
    app = FastAPI(
        title="RepoShield Dashboard API",
        version=VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def require_token(request: Request, call_next: Any) -> Any:
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("X-Approver-Token", "")
            if not _token_matches(supplied, deps.settings.approver_token):
                return JSONResponse(
                    status_code=401,
                    content={"error": "unauthorized", "message": "Invalid or expired token."},
                )
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[DASHBOARD_ORIGIN],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            content = exc.detail
        else:
            content = {"error": "error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(RequestValidationError)
    async def validation_exception(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request", "message": "The request body is not valid."},
        )

    async def session_payload(session_id: str) -> dict[str, Any]:
        session = await asyncio.to_thread(deps.audit.get_session, session_id)
        if session is None:
            raise _error(404, "not_found", "Session not found.")
        metrics = await asyncio.to_thread(deps.audit.metrics, session_id)
        payload = session.model_dump(mode="json")
        payload["counters"] = metrics["counts"]
        payload["total_events"] = metrics["total"]
        payload["latency_mean_ms"] = metrics["latency_mean_ms"]
        return payload

    def event_dto(event: EventRecord, snapshots: list[Any]) -> dict[str, Any]:
        payload = event.model_dump(mode="json")
        payload["undoable"] = any(snapshot.rolled_back_at is None for snapshot in snapshots)
        return payload

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Liveness and version."""
        return {"status": "ok", "version": VERSION}

    @app.get("/api/sessions")
    async def list_sessions() -> list[dict[str, Any]]:
        """List sessions, newest first."""
        sessions = await asyncio.to_thread(deps.audit.list_sessions)
        return [session.model_dump(mode="json") for session in sessions]

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        """One session with counters."""
        return await session_payload(session_id)

    @app.post("/api/sessions/{session_id}/lock")
    async def lock_session(session_id: str) -> dict[str, Any]:
        """Lock a session manually."""
        session = await asyncio.to_thread(deps.audit.get_session, session_id)
        if session is None:
            raise _error(404, "not_found", "Session not found.")
        if session.status == "ended":
            raise _error(409, "conflict", "The session has ended.")
        await deps.sessions.lock(session_id, "manual")
        updated = await session_payload(session_id)
        await deps.bus.publish("session.updated", {"session": updated})
        return updated

    @app.post("/api/sessions/{session_id}/unlock")
    async def unlock_session(session_id: str) -> dict[str, Any]:
        """Unlock a session manually."""
        session = await asyncio.to_thread(deps.audit.get_session, session_id)
        if session is None:
            raise _error(404, "not_found", "Session not found.")
        if session.status == "ended":
            raise _error(409, "conflict", "The session has ended.")
        await deps.sessions.unlock(session_id)
        updated = await session_payload(session_id)
        await deps.bus.publish("session.updated", {"session": updated})
        return updated

    @app.get("/api/events")
    async def list_events(
        session_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
        after_seq: int | None = Query(default=None, ge=0),
        decision: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return events, oldest first, newest window last."""
        if decision is not None and decision.upper() not in {"ALLOW", "MASK", "HOLD", "BLOCK"}:
            raise _error(400, "invalid_request", "Unknown decision filter.")
        events = await asyncio.to_thread(
            deps.audit.list_events,
            session_id=session_id,
            limit=limit,
            after_seq=after_seq,
            decision=decision,
        )
        result = []
        for event in events:
            snapshots = await asyncio.to_thread(deps.audit.snapshots_for_event, event.id)
            result.append(event_dto(event, snapshots))
        return result

    @app.get("/api/events/{event_id}")
    async def get_event(event_id: str) -> dict[str, Any]:
        """Return one event."""
        event = await asyncio.to_thread(deps.audit.get_event, event_id)
        if event is None:
            raise _error(404, "not_found", "Event not found.")
        snapshots = await asyncio.to_thread(deps.audit.snapshots_for_event, event.id)
        return event_dto(event, snapshots)

    @app.get("/api/events/{event_id}/delta")
    async def get_delta(event_id: str) -> dict[str, Any]:
        """Return raw and sanitized content with change spans."""
        event = await asyncio.to_thread(deps.audit.get_event, event_id)
        if event is None:
            raise _error(404, "not_found", "Event not found.")
        delta = await asyncio.to_thread(deps.audit.get_delta, event_id)
        if delta is None:
            return {"event_id": event_id, "raw": None, "sanitized": None, "changes": []}
        return delta.model_dump(mode="json")

    @app.get("/api/graph")
    async def graph(session_id: str | None = None) -> dict[str, Any]:
        """Return graph nodes and edges with per-node state."""
        events = await asyncio.to_thread(
            deps.audit.list_events, session_id=session_id, limit=10_000
        )
        database = Path(deps.policy.paths.database)
        if not database.is_absolute():
            database = (Path.cwd() / database).resolve()
        return await asyncio.to_thread(
            build_graph, deps.settings.workspace_abs, database, events
        )

    @app.get("/api/approvals")
    async def list_approvals(status: str | None = None) -> list[dict[str, Any]]:
        """List approvals, newest first."""
        approvals = await asyncio.to_thread(deps.audit.list_approvals, status)
        return [approval.model_dump(mode="json") for approval in approvals]

    @app.post("/api/approvals/{approval_id}/resolve")
    async def resolve_approval(approval_id: str, body: ResolveRequest) -> dict[str, Any]:
        """Resolve a pending approval with the human's answer."""
        record: ApprovalRecord | None = await asyncio.to_thread(
            deps.audit.get_approval, approval_id
        )
        if record is None:
            raise _error(404, "not_found", "Approval not found.")
        if record.status != "pending":
            raise _error(409, "conflict", "This approval is no longer pending.")
        if body.resolution == Resolution.APPROVE_MASKED and body.resolution not in record.options:
            raise _error(400, "invalid_request", "Masking is not available for this action.")
        resolved = await deps.approvals.resolve(
            approval_id, body.resolution, resolved_by="web"
        )
        if not resolved:
            raise _error(409, "conflict", "This approval is no longer pending.")
        updated = await asyncio.to_thread(deps.audit.get_approval, approval_id)
        return {
            "ok": True,
            "approval": updated.model_dump(mode="json") if updated else None,
        }

    @app.post("/api/events/{event_id}/rollback")
    async def rollback(event_id: str) -> dict[str, Any]:
        """Restore the snapshot for one event and log the rollback."""
        event = await asyncio.to_thread(deps.audit.get_event, event_id)
        if event is None:
            raise _error(404, "not_found", "Event not found.")
        snapshot = await deps.snapshots.undoable(event.id)
        if snapshot is None:
            raise _error(409, "conflict", "There is nothing to undo for this event.")
        try:
            await deps.snapshots.rollback(snapshot)
        except (ValueError, OSError) as exc:
            raise _error(409, "conflict", f"The snapshot could not be restored: {exc}") from exc

        target = snapshot.target or event.target
        rollback_event = EventRecord(
            id=f"evt_{secrets.token_hex(6)}",
            session_id=event.session_id,
            ts=utc_now(),
            connector=ConnectorName.SYSTEM,
            action="rollback",
            target=target[:300],
            task_context=deps.policy.task.description or None,
            risk_tier=0,
            scores=SignalScores(),
            decision=Decision.ALLOW,
            final_outcome=FinalOutcome.EXECUTED,
            approval=ApprovalKind.AUTO,
            reason_codes=[ReasonCode.ROLLBACK.value],
            reason_text=explain(ReasonCode.ROLLBACK.value, target=target),
            detections=[],
            flags=[],
            latency_ms=0.0,
        )
        try:
            logged = await deps.audit.append_event(rollback_event)
        except Exception as exc:  # pragma: no cover - storage failure
            raise _error(500, "internal_error", "The rollback could not be logged.") from exc
        await deps.bus.publish("event.created", {"event": logged.model_dump(mode="json")})
        await deps.bus.publish("snapshot.rolled_back", {"event_id": event.id})
        return {
            "ok": True,
            "event_id": logged.id,
            "rolled_back_event_id": event.id,
            "snapshot_id": snapshot.id,
        }

    @app.get("/api/log/verify")
    async def verify_log() -> dict[str, Any]:
        """Recompute the whole hash chain."""
        result: ChainVerification = await asyncio.to_thread(deps.audit.verify_chain)
        return result.model_dump(mode="json")

    @app.get("/api/policy")
    async def get_policy() -> dict[str, Any]:
        """Sanitised, read-only view of the active policy."""
        thresholds = deps.policy.thresholds()
        return {
            "profile": deps.policy.profile,
            "mode": deps.policy.mode,
            "task": deps.policy.task.model_dump(mode="json"),
            "thresholds": thresholds.model_dump(mode="json"),
            "protect": [rule.model_dump(mode="json") for rule in deps.policy.protect],
            "approval": {
                "timeout_seconds": deps.policy.approval.timeout_seconds,
                "channels": list(deps.policy.approval.channels),
            },
            "limits": deps.policy.limits.model_dump(mode="json"),
            "policy_path": str(deps.settings.policy_abs),
            "file_exists": deps.settings.policy_abs.exists(),
        }

    @app.get("/api/policy/sample")
    async def get_policy_sample() -> dict[str, Any]:
        """Return the sample .aishield template and file status."""
        return {
            "filename": ".aishield",
            "yaml": get_sample_policy_yaml(),
            "exists": deps.settings.policy_abs.exists(),
            "policy_path": str(deps.settings.policy_abs),
        }

    @app.post("/api/policy/create-sample")
    async def create_policy_sample(
        body: CreatePolicySampleRequest | None = None,
    ) -> dict[str, Any]:
        """Create or initialize a sample .aishield policy in the workspace."""
        overwrite = body.overwrite if body else False
        written = await asyncio.to_thread(
            write_sample_policy, deps.settings.policy_abs, overwrite=overwrite
        )
        return {
            "ok": True,
            "created": written,
            "message": (
                "Policy file written successfully."
                if written
                else "Policy file already exists."
            ),
            "path": str(deps.settings.policy_abs),
        }

    @app.get("/api/metrics/summary")
    async def metrics_summary(session_id: str | None = None) -> dict[str, Any]:
        """Counts per decision and pipeline latency."""
        return await asyncio.to_thread(deps.audit.metrics, session_id)

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            first = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
        except (TimeoutError, WebSocketDisconnect, json.JSONDecodeError, ValueError):
            await websocket.close(code=4401)
            return
        supplied = first.get("token") if isinstance(first, dict) else ""
        if (
            not isinstance(first, dict)
            or first.get("type") != "auth"
            or not _token_matches(str(supplied or ""), deps.settings.approver_token)
        ):
            await websocket.close(code=4401)
            return

        pending = await asyncio.to_thread(deps.audit.list_approvals, "pending")
        await websocket.send_json(
            {
                "type": "hello",
                "pending_approvals": [
                    approval.model_dump(mode="json") for approval in pending
                ],
            }
        )
        queue = await deps.bus.subscribe()

        async def sender() -> None:
            while True:
                message = await queue.get()
                kind = message.get("type", "")
                payload = message.get("payload", {})
                await websocket.send_json({"type": kind, **payload})
                if kind == "event.created":
                    event_data = payload.get("event") or {}
                    try:
                        event = EventRecord(**event_data)
                    except (TypeError, ValueError):
                        continue
                    graph = await asyncio.to_thread(
                        deps.audit.list_events,
                        session_id=event.session_id,
                        limit=10_000,
                    )
                    database = Path(deps.policy.paths.database)
                    if not database.is_absolute():
                        database = (Path.cwd() / database).resolve()
                    current = await asyncio.to_thread(
                        build_graph, deps.settings.workspace_abs, database, graph
                    )
                    node_id = _event_node_id(event, {n["id"]: n for n in current["nodes"]})
                    if node_id is not None:
                        node = next(n for n in current["nodes"] if n["id"] == node_id)
                        await websocket.send_json(
                            {
                                "type": "graph.node",
                                "node_id": node_id,
                                "state": node["state"],
                                "last_ts": node["last_ts"],
                            }
                        )

        async def receiver() -> None:
            while True:
                await websocket.receive_text()

        sender_task = asyncio.create_task(sender())
        receiver_task = asyncio.create_task(receiver())
        try:
            _done, pending_tasks = await asyncio.wait(
                {sender_task, receiver_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending_tasks:
                task.cancel()
        finally:
            await deps.bus.unsubscribe(queue)
            for task in (sender_task, receiver_task):
                if not task.done():
                    task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(sender_task, receiver_task, return_exceptions=True)

    return app
