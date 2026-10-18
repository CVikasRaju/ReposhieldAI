"""Integration helpers: seeded gateway and a real MCP server."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from backend.config import Settings
from backend.core.models import Resolution
from backend.main import Gateway, build_gateway
from backend.policy.loader import load_policy
from backend.policy.profiles import deep_merge
from scripts.seed_db import seed_database
from scripts.seed_workspace import seed_workspace

ROOT = Path(__file__).resolve().parents[2]


def make_gateway(
    tmp_path: Path,
    *,
    overrides: dict[str, Any] | None = None,
    approval_timeout: int = 5,
) -> Gateway:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    seed_workspace(workspace)
    seed_database(workspace / "business.db")

    data = load_policy(ROOT / ".aishield").model_dump(mode="json")
    data["paths"]["workspace"] = str(workspace)
    data["paths"]["database"] = str(workspace / "business.db")
    data["approval"]["timeout_seconds"] = approval_timeout
    if overrides:
        data = deep_merge(data, overrides)
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    settings = Settings(
        workspace=workspace,
        policy_path=policy_file,
        data_dir=tmp_path / "data",
        approver_token="integration-token",
        mcp_port=0,
        api_port=0,
    )
    return build_gateway(settings, interactive_approvals=False)


@asynccontextmanager
async def running_server(app: Any) -> AsyncIterator[str]:
    """Run one ASGI app on an ephemeral port and yield its base URL."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    task = asyncio.create_task(server.serve())
    for _ in range(500):
        if server.started:
            break
        await asyncio.sleep(0.01)
    if not server.started:
        server.should_exit = True
        await task
        raise RuntimeError("server did not start")
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


@asynccontextmanager
async def mcp_session(base_url: str, header: str = "integration") -> AsyncIterator[ClientSession]:
    """Open an SDK client session against a running gateway."""
    async with httpx.AsyncClient(
        headers={"X-RepoShield-Session": header}, timeout=30.0
    ) as client:
        async with streamable_http_client(f"{base_url}/mcp", http_client=client) as (
            read,
            write,
            _get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def call_tool(session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool and return its structured result."""
    result = await session.call_tool(name, arguments)
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


async def wait_for_pending(gateway: Gateway, timeout: float = 5.0) -> str:
    """Wait for an approval and return its id."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        pending = await gateway.approvals.pending()
        if pending:
            return pending[0].id
        await asyncio.sleep(0.01)
    raise AssertionError("no pending approval appeared")


async def resolve_pending(
    gateway: Gateway,
    task: asyncio.Task[dict[str, Any]],
    resolution: Resolution = Resolution.APPROVE,
) -> dict[str, Any]:
    """Wait for a call, approving it if it is held, then return its result."""
    while not task.done():
        pending = await gateway.approvals.pending()
        if pending:
            assert await gateway.approvals.resolve(
                pending[0].id, resolution, resolved_by="web"
            )
            break
        await asyncio.sleep(0.01)
    return await task
