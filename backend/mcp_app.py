"""The agent-facing MCP server.

Only the MCP endpoint is mounted. Every tool call goes through the pipeline.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from typing import Any

from fastapi import FastAPI
from mcp.server.fastmcp import Context, FastMCP

from backend.core.models import ActionRequest, ConnectorName, ToolResult
from backend.core.pipeline import Pipeline

_SESSION_INFO: ContextVar[tuple[str | None, str | None]] = ContextVar(
    "reposhield_session_info", default=(None, None)
)


class SessionContextMiddleware:
    """Capture the agent session header and MCP transport session id."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        token = _SESSION_INFO.set(
            (headers.get("x-reposhield-session"), headers.get("mcp-session-id"))
        )
        try:
            await self.app(scope, receive, send)
        finally:
            _SESSION_INFO.reset(token)


@contextmanager
def session_context(header: str | None = None, transport: str | None = None) -> Iterator[None]:
    """Set the session context for tests."""
    token: Token[tuple[str | None, str | None]] = _SESSION_INFO.set((header, transport))
    try:
        yield
    finally:
        _SESSION_INFO.reset(token)


def get_session_id(ctx: Context | None = None) -> str:
    """Resolve the session id: header, then transport id, then default."""
    header, transport = _SESSION_INFO.get()
    return header or transport or "default"


def create_mcp_app(pipeline: Pipeline) -> FastAPI:
    """Build the MCP-only FastAPI application."""
    mcp = FastMCP(
        "RepoShield AI",
        instructions=(
            "Every action passes through the RepoShield safety gateway. "
            "Blocked actions do not explain which policy matched."
        ),
        streamable_http_path="/mcp",
        stateless_http=False,
        log_level="WARNING",
    )

    async def run(request: ActionRequest) -> dict[str, Any]:
        result: ToolResult = await pipeline.handle(request)
        return result.model_dump(mode="json", exclude_none=True)

    @mcp.tool()
    async def list_directory(ctx: Context, path: str = ".") -> dict[str, Any]:
        """List one workspace directory. Protected entries are hidden."""
        return await run(
            ActionRequest(
                session_id=get_session_id(ctx),
                connector=ConnectorName.FILESYSTEM,
                action="list_directory",
                target=path,
            )
        )

    @mcp.tool()
    async def read_file(ctx: Context, path: str) -> dict[str, Any]:
        """Read a text file from the workspace, with sensitive parts masked."""
        return await run(
            ActionRequest(
                session_id=get_session_id(ctx),
                connector=ConnectorName.FILESYSTEM,
                action="read_file",
                target=path,
            )
        )

    @mcp.tool()
    async def write_file(ctx: Context, path: str, content: str) -> dict[str, Any]:
        """Write a file. Masked placeholders are restored automatically."""
        return await run(
            ActionRequest(
                session_id=get_session_id(ctx),
                connector=ConnectorName.FILESYSTEM,
                action="write_file",
                target=path,
                params={"content": content},
            )
        )

    @mcp.tool()
    async def delete_file(ctx: Context, path: str) -> dict[str, Any]:
        """Delete one file after approval. Directories are not deletable."""
        return await run(
            ActionRequest(
                session_id=get_session_id(ctx),
                connector=ConnectorName.FILESYSTEM,
                action="delete_file",
                target=path,
            )
        )

    @mcp.tool()
    async def run_command(ctx: Context, command: str) -> dict[str, Any]:
        """Run one allowlisted command without a shell."""
        return await run(
            ActionRequest(
                session_id=get_session_id(ctx),
                connector=ConnectorName.COMMAND,
                action="run_command",
                target=command,
                params={"command": command},
            )
        )

    @mcp.tool()
    async def db_schema(ctx: Context) -> dict[str, Any]:
        """Return the database tables and columns, flagging protected columns."""
        return await run(
            ActionRequest(
                session_id=get_session_id(ctx),
                connector=ConnectorName.DATABASE,
                action="db_schema",
                target="schema",
            )
        )

    @mcp.tool()
    async def db_query(ctx: Context, sql: str) -> dict[str, Any]:
        """Run one SQLite statement. Writes and protected tables may be held."""
        return await run(
            ActionRequest(
                session_id=get_session_id(ctx),
                connector=ConnectorName.DATABASE,
                action="db_query",
                target=sql,
                params={"sql": sql},
            )
        )

    @mcp.tool()
    async def http_request(
        ctx: Context,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an HTTP request through the egress gate."""
        body_text = json.dumps(body) if isinstance(body, dict) else (body or "")
        return await run(
            ActionRequest(
                session_id=get_session_id(ctx),
                connector=ConnectorName.NETWORK,
                action="http_request",
                target=url,
                params={"method": method, "url": url, "headers": headers or {}, "body": body_text},
            )
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="RepoShield MCP",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount("/", SessionContextMiddleware(mcp.streamable_http_app()))
    return app
