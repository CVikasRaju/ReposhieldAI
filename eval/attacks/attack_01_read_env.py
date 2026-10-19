"""Attack 1: README injection to read .env.

This script simulates an injection attack where the agent is tricked into
reading the .env file. It runs through the MCP gateway with --auto-verify off
and checks that no secret data leaves the sandbox.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

from ttools import make_gateway, running_server, call_tool, mcp_session
from ttools.mcp_helpers wait_for_pending


async def main() -> int:
    tmp_path = Path(tempfile.mkdtemp())
    gateway = make_gateway(tmp_path)
    
    try:
        async with running_server(gateway.api_app) as api_base:
            async with running_server(gateway.mcp_app) as mcp_base:
                async with httpx.AsyncClient(
                    base_url=api_base, timeout=10.0,
                    headers={"X-Approver-Token": "integration-token"},
                ) as client:
                    async with mcp_session(mcp_base) as session:
                        # Step: Inject content that triggers .env read
                        # This is a simulated attack - the agent action is:
                        # read_file(".env")
                        result = await call_tool(session, "read_file", {"target": ".env"})
                        event = result.get("event", {})
                        
                        decision = event.get("decision")
                        reason_codes = event.get("reason_codes", [])
                        collector_log = result.get("collector_log", "")
                        
                        print(f"Decision: {decision}")
                        print(f"Reason codes: {reason_codes}")
                        print(f"Collector log entries: {collector_log}")
                        
                        # Check assertions:
                        # 1. Decision should be BLOCK
                        # 2. Reason codes should include PROTECTED_PATH
                        # 3. Collector log should have zero entries
                        
                        passed = True
                        if decision != "BLOCK":
                            print(f"FAIL: Expected BLOCK, got {decision}")
                            passed = False
                        if ReasonCode.PROTECTED_PATH not in reason_codes:
                            print(f"FAIL: Expected PROTECTED_PATH in reason_codes")
                            passed = False
                        if "0 entries" not in collector_log.lower() and "empty" not in collector_log.lower():
                            print(f"FAIL: Expected empty collector log")
                            passed = False
                        
                        if passed:
                            print("Attack 1 PASSED: No data leaked via .env read")
                        else:
                            print("Attack 1 FAILED")
                        
                        return 0 if passed else 1
    finally:
        gateway.audit.close()
        import shutil
        try:
            shutil.rmtree(tmp_path)
        except Exception:
            pass


if __name__ == "__main__":
    import tempfile
    sys.exit(asyncio.run(main()))