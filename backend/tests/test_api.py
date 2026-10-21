"""Integration tests for the dashboard REST API and WebSocket."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
import websockets

from backend.core.audit import verify_chain
from backend.core.models import ActionRequest
from backend.tests.mcp_helpers import (
    call_tool,
    make_gateway,
    mcp_session,
    running_server,
    wait_for_pending,
)

HEADERS = {"X-Approver-Token": "integration-token"}


def _ws_url(base: str) -> str:
    parts = urlsplit(base)
    return f"ws://{parts.hostname}:{parts.port}/ws"


async def test_every_route_requires_the_approver_token(tmp_path: Path) -> None:
    gateway = make_gateway(tmp_path)
    try:
        async with running_server(gateway.api_app) as base:
            async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
                get_routes = [
                    "/api/health",
                    "/api/sessions",
                    "/api/sessions/default",
                    "/api/events",
                    "/api/events/evt_missing",
                    "/api/events/evt_missing/delta",
                    "/api/graph",
                    "/api/approvals",
                    "/api/log/verify",
                    "/api/policy",
                    "/api/metrics/summary",
                ]
                for route in get_routes:
                    assert (await client.get(route)).status_code == 401, route
                    assert (
                        await client.get(route, headers={"X-Approver-Token": "wrong"})
                    ).status_code == 401, route
                post_routes = [
                    "/api/sessions/default/lock",
                    "/api/sessions/default/unlock",
                    "/api/approvals/apr_missing/resolve",
                    "/api/events/evt_missing/rollback",
                ]
                for route in post_routes:
                    response = await client.post(route, json={"resolution": "approve"})
                    assert response.status_code == 401, route

                health = await client.get("/api/health", headers=HEADERS)
                assert health.status_code == 200
                assert health.json()["status"] == "ok"

                preflight = await client.options(
                    "/api/events",
                    headers={
                        "Origin": "http://localhost:5173",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                assert preflight.status_code == 200
                assert (
                    preflight.headers.get("access-control-allow-origin")
                    == "http://localhost:5173"
                )
    finally:
        gateway.audit.close()


async def test_websocket_rejects_missing_or_wrong_token(tmp_path: Path) -> None:
    gateway = make_gateway(tmp_path)
    try:
        async with running_server(gateway.api_app) as base:
            for first in (
                {"type": "auth", "token": "wrong"},
                {"type": "hello", "token": "integration-token"},
            ):
                async with websockets.connect(_ws_url(base)) as websocket:
                    await websocket.send(json.dumps(first))
                    with pytest.raises(websockets.ConnectionClosed):
                        await websocket.recv()
                    assert websocket.close_code == 4401
    finally:
        gateway.audit.close()


async def test_graph_events_delta_metrics_and_policy(tmp_path: Path) -> None:
    gateway = make_gateway(tmp_path)
    try:
        async with running_server(gateway.api_app) as base:
            async with httpx.AsyncClient(base_url=base, timeout=10.0, headers=HEADERS) as client:
                masked = await gateway.pipeline.handle(
                    ActionRequest(
                        session_id="default",
                        connector="filesystem",
                        action="read_file",
                        target="src/pricing/engine.py",
                    )
                )
                blocked = await gateway.pipeline.handle(
                    ActionRequest(
                        session_id="default",
                        connector="filesystem",
                        action="read_file",
                        target=".env",
                    )
                )
                egress = await gateway.pipeline.handle(
                    ActionRequest(
                        session_id="default",
                        connector="network",
                        action="http_request",
                        target="https://collector.local/collect",
                        params={
                            "method": "GET",
                            "url": "https://collector.local/collect",
                        },
                    )
                )
                assert blocked.status == "blocked"
                assert masked.status == "masked"
                assert egress.status == "blocked"

                events = (await client.get("/api/events")).json()
                assert len(events) == 3
                assert events[-1]["id"] == egress.event_id
                assert all("undoable" in event for event in events)

                delta = (await client.get(f"/api/events/{masked.event_id}/delta")).json()
                assert "total = units * rate" in delta["raw"]
                assert "total = units * rate" not in delta["sanitized"]
                assert delta["changes"]

                no_delta = (
                    await client.get(f"/api/events/{blocked.event_id}/delta")
                ).json()
                assert no_delta["raw"] is None

                graph = (await client.get("/api/graph")).json()
                nodes = {node["id"]: node for node in graph["nodes"]}
                assert nodes[".env"]["state"] == "blocked"
                assert nodes["src/pricing/engine.py"]["state"] == "masked"
                assert nodes["src/pricing"]["state"] == "masked"
                assert nodes["src"]["state"] == "masked"
                assert nodes["host:collector.local"]["state"] == "blocked"
                assert nodes["db:customers"]["state"] == "unvisited"
                assert {"source": "src", "target": "src/pricing"} in graph["edges"]

                metrics = (await client.get("/api/metrics/summary")).json()
                assert metrics["counts"]["MASK"] == 1
                assert metrics["counts"]["BLOCK"] == 2
                assert metrics["total"] == 3
                assert metrics["latency_mean_ms"] >= 0

                policy = (await client.get("/api/policy")).json()
                assert policy["profile"] == "developer"
                assert policy["mode"] == "strict"
                assert set(policy["thresholds"]) == {"mask", "hold", "block"}
                assert any(rule["kind"] == "table" for rule in policy["protect"])

                session = (await client.get("/api/sessions/default")).json()
                assert session["counters"]["BLOCK"] == 2
                assert session["total_events"] == 3

                verify = (await client.get("/api/log/verify")).json()
                assert verify["ok"] is True
                assert verify["checked"] == 3
    finally:
        gateway.audit.close()


async def test_lifecycle_lock_unlock_and_missing_resources(tmp_path: Path) -> None:
    gateway = make_gateway(tmp_path)
    try:
        async with running_server(gateway.api_app) as base:
            async with httpx.AsyncClient(base_url=base, timeout=5.0, headers=HEADERS) as client:
                assert (await client.get("/api/sessions/nope")).status_code == 404
                assert (await client.get("/api/events/evt_nope")).status_code == 404
                assert (await client.get("/api/events/evt_nope/delta")).status_code == 404
                assert (await client.post("/api/events/evt_nope/rollback")).status_code == 404
                assert (
                    await client.post(
                        "/api/approvals/apr_nope/resolve", json={"resolution": "approve"}
                    )
                ).status_code == 404
                assert (await client.post("/api/sessions/nope/lock")).status_code == 404

                locked = (await client.post("/api/sessions/default/lock")).json()
                assert locked["status"] == "locked"
                unlocked = (await client.post("/api/sessions/default/unlock")).json()
                assert unlocked["status"] == "active"
    finally:
        gateway.audit.close()


async def test_websocket_messages_and_http_approval(tmp_path: Path) -> None:
    gateway = make_gateway(tmp_path)
    try:
        async with running_server(gateway.mcp_app) as mcp_base:
            async with running_server(gateway.api_app) as api_base:
                async with httpx.AsyncClient(
                    base_url=api_base, timeout=10.0, headers=HEADERS
                ) as client:
                    async with mcp_session(mcp_base) as session:
                        async with websockets.connect(_ws_url(api_base)) as websocket:
                            await websocket.send(
                                json.dumps({"type": "auth", "token": "integration-token"})
                            )
                            hello = json.loads(await asyncio.wait_for(websocket.recv(), 5))
                            assert hello["type"] == "hello"
                            assert hello["pending_approvals"] == []

                            written = await call_tool(
                                session,
                                "write_file",
                                {"path": "docs/api.md", "content": "# API\n"},
                            )
                            assert written["status"] == "ok"

                            held = asyncio.create_task(
                                call_tool(
                                    session,
                                    "run_command",
                                    {"command": "rm -rf src/pricing"},
                                )
                            )
                            approval_id = await wait_for_pending(gateway)
                            pending = (
                                await client.get("/api/approvals?status=pending")
                            ).json()
                            assert pending[0]["id"] == approval_id
                            assert "approve_masked" not in pending[0]["options"]

                            assert (
                                await client.post(
                                    f"/api/approvals/{approval_id}/resolve",
                                    json={"resolution": "approve"},
                                )
                            ).status_code == 200
                            assert (await held)["status"] == "ok"

                            observed: set[str] = set()
                            deadline = asyncio.get_running_loop().time() + 8
                            while asyncio.get_running_loop().time() < deadline:
                                try:
                                    message = json.loads(
                                        await asyncio.wait_for(websocket.recv(), 1)
                                    )
                                except TimeoutError:
                                    break
                                observed.add(message["type"])
                                if message["type"] == "graph.node":
                                    assert "node_id" in message and "state" in message
                                if {
                                    "event.created",
                                    "approval.requested",
                                    "approval.resolved",
                                    "graph.node",
                                } <= observed:
                                    break
                            assert {
                                "event.created",
                                "approval.requested",
                                "approval.resolved",
                                "graph.node",
                            } <= observed

                            locked = await client.post("/api/sessions/default/lock")
                            assert locked.status_code == 200
                            await client.post("/api/sessions/default/unlock")
                            await websocket.send("{}")
                            deadline = asyncio.get_running_loop().time() + 5
                            while asyncio.get_running_loop().time() < deadline:
                                try:
                                    message = json.loads(
                                        await asyncio.wait_for(websocket.recv(), 1)
                                    )
                                except TimeoutError:
                                    break
                                observed.add(message["type"])
                                if "session.updated" in observed:
                                    break
                            assert "session.updated" in observed
    finally:
        gateway.audit.close()


async def test_rollback_file_database_and_workspace(tmp_path: Path) -> None:
    gateway = make_gateway(tmp_path)
    workspace = gateway.settings.workspace_abs
    original_readme = (workspace / "README.md").read_text(encoding="utf-8")
    engine = workspace / "src" / "pricing" / "engine.py"
    database = workspace / "business.db"
    try:
        async with running_server(gateway.mcp_app) as mcp_base:
            async with running_server(gateway.api_app) as api_base:
                async with httpx.AsyncClient(
                    base_url=api_base, timeout=10.0, headers=HEADERS
                ) as client:
                    async with mcp_session(mcp_base) as session:
                        write = await call_tool(
                            session,
                            "write_file",
                            {"path": "README.md", "content": "changed"},
                        )
                        rolled = await client.post(
                            f"/api/events/{write['event_id']}/rollback"
                        )
                        assert rolled.status_code == 200
                        assert rolled.json()["ok"] is True
                        assert (workspace / "README.md").read_text(
                            encoding="utf-8"
                        ) == original_readme
                        original = (await client.get(f"/api/events/{write['event_id']}")).json()
                        assert original["undoable"] is False
                        assert (await client.post(
                            f"/api/events/{write['event_id']}/rollback"
                        )).status_code == 409

                        insert = asyncio.create_task(
                            call_tool(
                                session,
                                "db_query",
                                {
                                    "sql": (
                                        "INSERT INTO customers (id, full_name, email, phone, "
                                        "national_id, city, plan, signup_date, notes) VALUES "
                                        "(998, 'API Test', 'api@corp.test', '+1-555-010-0001', "
                                        "'1234 5678 9012', 'Pune', 'free', '2026-01-01', '')"
                                    )
                                },
                            )
                        )
                        approval_id = await wait_for_pending(gateway)
                        assert (
                            await client.post(
                                f"/api/approvals/{approval_id}/resolve",
                                json={"resolution": "approve"},
                            )
                        ).status_code == 200
                        inserted = await insert
                        assert inserted["status"] == "ok"
                        with closing(sqlite3.connect(database)) as connection:
                            assert connection.execute(
                                "SELECT COUNT(*) FROM customers"
                            ).fetchone()[0] == 51
                        assert (
                            await client.post(
                                f"/api/events/{inserted['event_id']}/rollback"
                            )
                        ).status_code == 200
                        with closing(sqlite3.connect(database)) as connection:
                            assert connection.execute(
                                "SELECT COUNT(*) FROM customers"
                            ).fetchone()[0] == 50

                        destructive = asyncio.create_task(
                            call_tool(
                                session,
                                "run_command",
                                {"command": "rm -rf src/pricing"},
                            )
                        )
                        approval_id = await wait_for_pending(gateway)
                        assert (
                            await client.post(
                                f"/api/approvals/{approval_id}/resolve",
                                json={"resolution": "approve"},
                            )
                        ).status_code == 200
                        removed = await destructive
                        assert removed["status"] == "ok"
                        assert not engine.exists()
                        assert (
                            await client.post(
                                f"/api/events/{removed['event_id']}/rollback"
                            )
                        ).status_code == 200
                        assert engine.exists()

                        events = (await client.get("/api/events")).json()
                        rollbacks = [event for event in events if event["action"] == "rollback"]
                        assert len(rollbacks) == 3
                        assert all(
                            event["reason_codes"] == ["ROLLBACK"] for event in rollbacks
                        )
                        verify = (await client.get("/api/log/verify")).json()
                        assert verify["ok"] is True
    finally:
        gateway.audit.close()


async def test_verify_detects_a_tampered_copy(tmp_path: Path) -> None:
    gateway = make_gateway(tmp_path)
    try:
        await gateway.pipeline.handle(
            ActionRequest(
                session_id="default",
                connector="filesystem",
                action="read_file",
                target="src/app.py",
            )
        )
        source = gateway.settings.db_path
        copy = tmp_path / "tampered.db"
        with closing(sqlite3.connect(source)) as original, closing(
            sqlite3.connect(copy)
        ) as clone:
            original.backup(clone)
            clone.execute("UPDATE events SET reason_text = 'tampered'")
            clone.commit()
        result = verify_chain(copy)
        assert result.ok is False
        assert result.first_bad_seq == 1
        clean = gateway.audit.verify_chain()
        assert clean.ok is True
    finally:
        gateway.audit.close()


async def test_policy_sample_and_create_endpoints(tmp_path: Path) -> None:
    gateway = make_gateway(tmp_path)
    try:
        async with running_server(gateway.api_app) as base:
            async with httpx.AsyncClient(base_url=base, timeout=5.0, headers=HEADERS) as client:
                res = await client.get("/api/policy/sample")
                assert res.status_code == 200
                data = res.json()
                assert data["filename"] == ".aishield"
                assert "profile: developer" in data["yaml"]

                create_res = await client.post("/api/policy/create-sample", json={"overwrite": True})
                assert create_res.status_code == 200
                assert create_res.json()["ok"] is True
    finally:
        gateway.audit.close()
