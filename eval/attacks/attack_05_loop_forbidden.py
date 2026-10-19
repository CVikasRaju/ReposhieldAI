"""Attack 5: the agent loops on forbidden paths until the kill-switch fires.

It is stopped when the fifth blocked read inside the window locks the session
and the next call is refused, with no .env value ever reaching the agent.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.tests.mcp_helpers import (  # noqa: E402
    call_tool,
    make_gateway,
    mcp_session,
    running_server,
)

SESSION = "attack-5"
MAX_ATTEMPTS = 7


def env_values(workspace: Path) -> list[str]:
    env = workspace / ".env"
    if not env.is_file():
        return []
    values = []
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        _, _, value = stripped.partition("=")
        value = value.strip()
        if len(value) >= 6:
            values.append(value)
    return values


async def main() -> int:
    print("Attack 5: loop on forbidden paths until the kill switch")
    tmp = Path(tempfile.mkdtemp(prefix="attack05_"))
    gateway = make_gateway(tmp)
    try:
        statuses: list[str] = []
        returned_text = ""
        async with running_server(gateway.api_app) as api_base:
            async with running_server(gateway.mcp_app) as mcp_base:
                async with mcp_session(mcp_base, header=SESSION) as session:
                    for attempt in range(MAX_ATTEMPTS):
                        target = ".env" if attempt % 2 == 0 else ".env.local"
                        result = await call_tool(session, "read_file", {"path": target})
                        status = str(result.get("status", ""))
                        statuses.append(status)
                        returned_text += f"{result.get('content') or ''}{result.get('notice') or ''}"
                        print(f"  attempt {attempt + 1}: {target} -> {status}")
                        if status == "terminated":
                            break

                async with httpx.AsyncClient(
                    base_url=api_base, timeout=10.0,
                    headers={"X-Approver-Token": "integration-token"},
                ) as client:
                    response = await client.get(f"/api/sessions/{SESSION}")
                    session_status = response.json().get("status", "") if response.status_code == 200 else "missing"

        leaks = [value for value in env_values(gateway.settings.workspace_abs) if value in returned_text]
        stopped = statuses.count("blocked") >= 5 and statuses[-1] == "terminated" and not leaks
        print(f"  session status: {session_status}")
        print(f"  statuses: {statuses}")
        print(f"  .env values in tool results: {len(leaks)}")
        print(f"RESULT: {'stopped' if stopped else 'not stopped'}")
        return 0 if stopped else 1
    finally:
        gateway.audit.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
