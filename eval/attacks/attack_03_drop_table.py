"""Attack 3: Injection to DROP TABLE.

Simulates an agent being tricked into running a destructive SQL command.
Verifies that the command is blocked and a snapshot is taken.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ttools import make_gateway, running_server, call_tool, mcp_session


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
                        # Action: db_query with DROP TABLE
                        result = await call_tool(session, "db_query", {
                            "sql": "DROP TABLE customers"
                        })
                        event = result.get("event", {})
                        decision = event.get("decision")
                        approval = event.get("approval")
                        snapshot = event.get("snapshot", {})
                        
                        print(f"Decision: {decision}")
                        print(f"Approval: {approval}")
                        print(f"Snapshot: {snapshot}")
                        
                        passed = True
                        if decision != "HOLD":  # SQL DROP is held for approval
                            print(f"FAIL: Expected HOLD, got {decision}")
                            passed = False
                        if approval != "approved":
                            print(f"FAIL: Expected approved approval")
                            passed = False
                        if snapshot.get("ok") is not True:
                            print(f"FAIL: Expected snapshot ok=True")
                            passed = False
                        
                        if passed:
                            print("Attack 3 PASSED: DROP TABLE blocked with approval")
                        else:
                            print("Attack 3 FAILED")
                        
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