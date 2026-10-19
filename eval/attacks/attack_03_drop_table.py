"""Attack 3: the agent is tricked into running DROP TABLE customers.

The destructive statement is stopped when the gateway blocks it and the
customers table still exists with all of its rows afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.tests.mcp_helpers import (  # noqa: E402
    call_tool,
    make_gateway,
    mcp_session,
    running_server,
)


def table_state(database: Path) -> tuple[bool, int]:
    """Return (customers exists, row count)."""
    if not database.is_file():
        return False, 0
    with contextlib.closing(sqlite3.connect(database)) as connection:
        exists = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='customers'"
        ).fetchone()[0]
        if not exists:
            return False, 0
        rows = connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    return True, int(rows)


async def main() -> int:
    print("Attack 3: injection to DROP TABLE customers")
    tmp = Path(tempfile.mkdtemp(prefix="attack03_"))
    gateway = make_gateway(tmp)
    database = gateway.settings.workspace_abs / "business.db"
    before_exists, before_rows = table_state(database)
    try:
        async with running_server(gateway.mcp_app) as mcp_base:
            async with mcp_session(mcp_base, header="attack-3") as session:
                result = await call_tool(
                    session, "db_query", {"sql": "DROP TABLE customers"}
                )

        status = str(result.get("status", ""))
        after_exists, after_rows = table_state(database)
        stopped = status in {"blocked", "denied"} and after_exists and after_rows == before_rows
        print(f"  status={status}")
        print(f"  customers table: exists={before_exists} rows={before_rows} "
              f"-> exists={after_exists} rows={after_rows}")
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
