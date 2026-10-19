#!/usr/bin/env python3
"""Isolation checks that run inside the agent container (docs/08 part D).

Prints PASS or FAIL for each of the five documented checks and exits non-zero
when any of them fails. This script deliberately imports no backend code: the
agent image contains the MCP client dependencies and the demo agent only.

Environment:
    RS_GATEWAY_HOST  gateway hostname on agent_net (default: gateway)
    RS_API_PORT      dashboard listener port (default: 8000)
    RS_MCP_PORT      MCP listener port (default: 8001)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

GATEWAY_HOST = os.environ.get("RS_GATEWAY_HOST", "gateway")
API_PORT = os.environ.get("RS_API_PORT", "8000")
MCP_PORT = os.environ.get("RS_MCP_PORT", "8001")

# Paths that would exist if the workspace or policy were mounted into the
# agent container. None of them may be present.
WORKSPACE_MARKERS = (
    "/workspace",
    "/app/workspace",
    "/app/.aishield",
    "/app/data/audit.db",
)

CONNECT_TIMEOUT = 3.0


def _get(url: str) -> tuple[int, bytes]:
    """Perform a GET and return (status, body); raises on network failure."""
    request = urllib.request.Request(url, headers={"User-Agent": "verify-isolation"})
    with urllib.request.urlopen(request, timeout=CONNECT_TIMEOUT) as response:
        return int(response.status), response.read(4096)


def check_workspace_not_mounted() -> bool:
    """The workspace, policy and data directory must not exist in this image."""
    present = [path for path in WORKSPACE_MARKERS if Path(path).exists()]
    if present:
        print(f"FAIL: workspace markers present: {', '.join(present)}")
        return False
    print("PASS: workspace, policy and data directory are not mounted")
    return True


def check_no_internet() -> bool:
    """Any outbound HTTP connection must fail."""
    try:
        status, _ = _get("https://example.com")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"PASS: no internet connectivity ({type(exc).__name__})")
        return True
    print(f"FAIL: internet is reachable (status {status})")
    return False


def check_no_collector() -> bool:
    """The exfiltration collector must be unreachable from agent_net."""
    try:
        status, _ = _get("http://collector.local:9999/upload")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"PASS: collector unreachable ({type(exc).__name__})")
        return True
    print(f"FAIL: collector answered with status {status}")
    return False


def check_api_needs_token() -> bool:
    """The dashboard API must answer 401 without the approver token."""
    url = f"http://{GATEWAY_HOST}:{API_PORT}/api/events"
    try:
        status, _ = _get(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            print("PASS: /api/events answers 401 without the approver token")
            return True
        print(f"FAIL: /api/events answered {exc.code}, expected 401")
        return False
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"FAIL: gateway API not reachable ({type(exc).__name__})")
        return False
    print(f"FAIL: /api/events answered {status} without a token, expected 401")
    return False


async def _mcp_list_directory() -> tuple[bool, str]:
    try:
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:  # pragma: no cover - image without the SDK
        return False, f"MCP SDK not installed ({exc})"

    url = f"http://{GATEWAY_HOST}:{MCP_PORT}/mcp"
    try:
        async with httpx.AsyncClient(
            headers={"X-RepoShield-Session": "isolation-check"}, timeout=30.0
        ) as client:
            async with streamable_http_client(url, http_client=client) as (
                read,
                write,
                _get_session_id,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = [tool.name for tool in tools.tools]
                    if "list_directory" not in names:
                        return False, f"list_directory missing from {names}"
                    result = await session.call_tool("list_directory", {"path": "."})
                    body: dict[str, Any] | None = None
                    if result.structuredContent is not None:
                        body = result.structuredContent
                    elif result.content:
                        body = json.loads(result.content[0].text)
                    status = (body or {}).get("status")
                    if status in {"ok", "masked"}:
                        return True, f"list_directory answered status={status}"
                    return False, f"list_directory answered {body}"
    except Exception as exc:  # noqa: BLE001 - report any transport failure
        return False, f"MCP call failed: {type(exc).__name__}: {exc}"


def check_mcp_works() -> bool:
    """The MCP listener must serve the tools, including list_directory."""
    ok, detail = asyncio.run(_mcp_list_directory())
    print(f"{'PASS' if ok else 'FAIL'}: {detail}")
    return ok


CHECKS: tuple[tuple[str, Any], ...] = (
    ("workspace not mounted", check_workspace_not_mounted),
    ("no internet", check_no_internet),
    ("no collector", check_no_collector),
    ("API needs approver token", check_api_needs_token),
    ("MCP list_directory works", check_mcp_works),
)


def main() -> int:
    """Run the five documented isolation checks."""
    results: list[tuple[str, bool]] = []
    for name, check in CHECKS:
        print(f"--- {name}")
        try:
            results.append((name, bool(check())))
        except Exception as exc:  # noqa: BLE001 - a crashed check is a failure
            print(f"ERROR: {name}: {type(exc).__name__}: {exc}")
            results.append((name, False))

    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} isolation checks passed")
    failed = [name for name, ok in results if not ok]
    if failed:
        print("FAILED: " + "; ".join(failed))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
