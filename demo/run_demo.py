"""Run the docs/08 scripted demo: eight steps, human approvals and assertions.

Flags:
    --pause          wait for Enter before every step (presenter mode)
    --auto-approve   resolve held approvals through the dashboard API
    --assert         check the documented events after every step and at the end

The script reuses a running gateway when RS_APPROVER_TOKEN authenticates against
it, otherwise it starts a fresh in-process gateway. It starts (or reuses) the
evil collector, drives demo/simulated_agent.py over MCP, then reads
GET /api/events and data/collector.log to verify the eight documented
assertions against real state.

Usage:
    make demo
    python demo/run_demo.py --pause
    python demo/run_demo.py --auto-approve --assert
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import os
import secrets
import socket
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import Settings  # noqa: E402
from backend.main import build_gateway  # noqa: E402
from demo import evil_collector  # noqa: E402
from demo.simulated_agent import DEMO_STEPS, SimulatedAgent  # noqa: E402
from scripts.seed_workspace import seed_workspace  # noqa: E402

COLLECTOR_PORT = 9999


# --------------------------------------------------------------------------- helpers
class Checks:
    """Collect pass/fail results for the --assert mode."""

    def __init__(self) -> None:
        self.passed = 0
        self.failures: list[str] = []

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            print(f"    [ok]   {label}")
        else:
            text = f"{label} - {detail}" if detail else label
            self.failures.append(text)
            print(f"    [FAIL] {text}")
        return ok


def _port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) != 0


async def _start_app(app: Any, port: int) -> tuple[str, asyncio.Task[None], uvicorn.Server]:
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="on", access_log=False
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    task = asyncio.create_task(server.serve())
    for _ in range(500):
        if server.started:
            break
        await asyncio.sleep(0.01)
    if not server.started:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await task
        raise RuntimeError(f"the server did not start on port {port}")
    actual_port = server.servers[0].sockets[0].getsockname()[1]
    return f"http://127.0.0.1:{actual_port}", task, server


async def _stop_app(task: asyncio.Task[None], server: uvicorn.Server) -> None:
    server.should_exit = True
    with contextlib.suppress(Exception):
        await task


async def _probe_health(base: str, token: str) -> int | None:
    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
            response = await client.get(
                f"{base}/api/health", headers={"X-Approver-Token": token}
            )
        except httpx.HTTPError:
            return None
        return response.status_code


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def env_strings(path: Path) -> list[str]:
    """Return every key and value string stored in a .env style file."""
    if not path.is_file():
        return []
    values: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values.extend([key.strip(), value.strip()])
    return [item for item in values if len(item) >= 6]


def raw_customer_emails(database: Path) -> list[str]:
    if not database.is_file():
        return []
    with contextlib.closing(sqlite3.connect(database)) as connection:
        rows = connection.execute(
            "SELECT DISTINCT email FROM customers WHERE email IS NOT NULL"
        ).fetchall()
    return [str(row[0]) for row in rows if row[0]]


def linked_snapshots(db_path: Path, event_id: str) -> int:
    if not db_path.is_file():
        return 0
    with contextlib.closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM snapshots WHERE event_id = ?", (event_id,)
        ).fetchone()
    return int(row[0]) if row else 0


async def fetch_events(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await client.get("/api/events", params={"limit": 1000})
    response.raise_for_status()
    return list(response.json())


def find_event(
    events: list[dict[str, Any]], *, reason_code: str | None = None, **want: Any
) -> dict[str, Any] | None:
    """Return the newest event matching every given field (and reason code)."""
    for event in reversed(events):
        if reason_code is not None:
            codes = [str(code) for code in event.get("reason_codes") or []]
            if reason_code not in codes:
                continue
        if all(event.get(key) == value for key, value in want.items()):
            return event
    return None


def find_event_containing(events: list[dict[str, Any]], key: str, needle: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if needle in str(event.get(key, "")):
            return event
    return None


# --------------------------------------------------------------------------- gateway
@dataclass
class GatewayHandle:
    """Where the demo talks to the gateway, and what it started."""

    api_base: str
    mcp_url: str
    token: str
    external: bool
    settings: Settings
    gateway: Any = None
    servers: list[tuple[asyncio.Task[None], uvicorn.Server]] = field(default_factory=list)
    log_path: Path | None = None
    log_handler: logging.Handler | None = None

    async def close(self) -> None:
        for task, server in self.servers:
            await _stop_app(task, server)
        self.servers.clear()
        if self.log_handler is not None:
            logging.getLogger().removeHandler(self.log_handler)
            self.log_handler.close()
            self.log_handler = None
        if self.gateway is not None:
            self.gateway.audit.close()
            self.gateway = None


async def setup_gateway() -> GatewayHandle:
    """Reuse a running gateway or start a fresh one in this process."""
    probe = Settings()
    api_base = f"http://{probe.api_host}:{probe.api_port}"
    env_token = os.environ.get("RS_APPROVER_TOKEN", "").strip()

    # Test candidate tokens against the running gateway at port 8000
    candidates: list[str] = []
    if env_token:
        candidates.append(env_token)
    for default_cand in ["reposhield-approver-token", "approver-token"]:
        if default_cand not in candidates:
            candidates.append(default_cand)

    for cand in candidates:
        status = await _probe_health(api_base, cand)
        if status == 200:
            print(f"[gateway] connected to running gateway at {api_base}")
            print(f"[dashboard] live dashboard active at http://localhost:5173 (token: {cand})")
            return GatewayHandle(
                api_base=api_base,
                mcp_url=f"http://{probe.mcp_host}:{probe.mcp_port}/mcp",
                token=cand,
                external=True,
                settings=probe,
            )

    token = env_token or "reposhield-approver-token"
    api_port = probe.api_port if _port_free(probe.api_port) else 0
    mcp_port = probe.mcp_port if _port_free(probe.mcp_port) else 0
    settings = Settings(approver_token=token, api_port=api_port, mcp_port=mcp_port)
    gateway = build_gateway(settings, interactive_approvals=False)

    log_path = settings.data_abs / "demo_gateway.log"
    log_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log_handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(log_handler)

    handle = GatewayHandle(
        api_base="",
        mcp_url="",
        token=token,
        external=False,
        settings=settings,
        gateway=gateway,
        log_path=log_path,
        log_handler=log_handler,
    )
    try:
        api_base, api_task, api_server = await _start_app(gateway.api_app, api_port)
        mcp_base, mcp_task, mcp_server = await _start_app(gateway.mcp_app, mcp_port)
    except Exception:
        await handle.close()
        raise
    handle.api_base = api_base
    handle.mcp_url = f"{mcp_base}/mcp"
    handle.servers = [(api_task, api_server), (mcp_task, mcp_server)]
    print(f"[gateway] started a fresh gateway: API {api_base}, MCP {handle.mcp_url}")
    print(f"[dashboard] open http://localhost:5173/#token={token}")
    return handle


async def setup_collector(log_path: Path) -> tuple[asyncio.AbstractServer | None, int]:
    """Start the evil collector in-process unless something already listens."""
    previous = 0
    if log_path.is_file():
        previous = sum(1 for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip())
        log_path.write_text("", encoding="utf-8")
    if _port_free(COLLECTOR_PORT):
        server = await evil_collector.serve(port=COLLECTOR_PORT, log_path=log_path)
        print(f"[collector] listening on :{COLLECTOR_PORT}, log {log_path}")
    else:
        server = None
        print(f"[collector] already listening on :{COLLECTOR_PORT}, reusing it")
    if previous:
        print(f"[collector] truncated {previous} old log entries at start")
    return server, previous


async def auto_approver(client: httpx.AsyncClient, started_at: str) -> None:
    """Resolve every approval created by this demo run through the API."""
    answered: set[str] = set()
    while True:
        try:
            response = await client.get("/api/approvals", params={"status": "pending"})
            for record in response.json():
                approval_id = str(record.get("id", ""))
                if not approval_id or approval_id in answered:
                    continue
                if str(record.get("created_at", "")) < started_at:
                    continue
                options = [str(item) for item in record.get("options") or []]
                resolution = "approve_masked" if "approve_masked" in options else "approve"
                answered.add(approval_id)
                answer = await client.post(
                    f"/api/approvals/{approval_id}/resolve", json={"resolution": resolution}
                )
                if answer.status_code == 200:
                    print(f"  [auto-approve] {approval_id}: {resolution}")
                else:
                    print(f"  [auto-approve] {approval_id}: HTTP {answer.status_code}")
        except Exception as exc:  # keep polling; the held call reports the real failure
            print(f"  [auto-approve] {type(exc).__name__}: {exc}")
        await asyncio.sleep(0.05)


# --------------------------------------------------------------------------- checks
async def check_step(
    number: int,
    checks: Checks,
    client: httpx.AsyncClient,
    outcome: Any,
    context: dict[str, Any],
) -> None:
    """Check one demo step against GET /api/events (real state)."""
    events = await fetch_events(client)
    statuses = [str(item.get("status")) for item in outcome.results]

    if number == 1:
        checks.check(
            "step 1 decisions: list and two benign reads allowed, pricing engine masked",
            statuses == ["ok", "ok", "ok", "masked"],
            f"statuses={statuses}",
        )
    elif number == 2:
        event = find_event(events, action="read_file", target=".env", decision="BLOCK")
        checks.check("step 2 read .env is BLOCK with PROTECTED_PATH", event is not None, "no event")
        if event:
            checks.check(
                "step 2 reason code PROTECTED_PATH",
                "PROTECTED_PATH" in event.get("reason_codes", []),
                str(event.get("reason_codes")),
            )
    elif number == 3:
        event = find_event(events, action="db_query", decision="HOLD")
        checks.check("step 3 customers query is HOLD", event is not None, "no event")
        if event:
            checks.check(
                "step 3 approval is approved_masked",
                event.get("approval") == "approved_masked",
                str(event.get("approval")),
            )
        content = str(outcome.results[-1].get("content") or "") if outcome.results else ""
        emails = context.get("emails", [])
        leaked = [email for email in emails if email in content]
        checks.check(
            "step 3 result contains no raw customer email",
            not leaked,
            f"leaked={leaked[:3]}",
        )
    elif number == 4:
        event = find_event_containing(events, "target", "rm -rf src/pricing")
        checks.check("step 4 destructive command recorded", event is not None, "no event")
        if event:
            checks.check(
                "step 4 approval is approved",
                event.get("approval") == "approved",
                str(event.get("approval")),
            )
            checks.check(
                "step 4 has a linked snapshot",
                linked_snapshots(context["data_db"], event["id"]) > 0,
                "no snapshot row for the event",
            )
    elif number == 5:
        event = find_event(events, action="http_request", decision="BLOCK")
        checks.check("step 5 egress is BLOCK", event is not None, "no event")
        if event:
            checks.check(
                "step 5 reason code EGRESS_DENIED",
                "EGRESS_DENIED" in event.get("reason_codes", []),
                str(event.get("reason_codes")),
            )
    elif number == 6:
        event = find_event(events, action="run_command", decision="BLOCK")
        checks.check("step 6 shell pipeline is BLOCK", event is not None, "no event")
        if event:
            checks.check(
                "step 6 reason code SHELL_OPERATORS",
                "SHELL_OPERATORS" in event.get("reason_codes", []),
                str(event.get("reason_codes")),
            )
    elif number == 7:
        checks.check(
            "step 7 statuses are blocked, blocked, terminated",
            statuses == ["blocked", "blocked", "terminated"],
            f"statuses={statuses}",
        )
        session = (await client.get("/api/sessions/default")).json()
        checks.check(
            "step 7 kill-switch locked the session",
            session.get("status") == "locked",
            str(session.get("status")),
        )


async def presenter_step(
    checks: Checks, client: httpx.AsyncClient, args: argparse.Namespace, context: dict[str, Any]
) -> None:
    """Step 8: verify the log, run the tamper demo on a copy, undo step 4."""
    if args.pause:
        input("\nPress Enter to run step 8: verify log, tamper demo, undo ...")

    verify = (await client.get("/api/log/verify")).json()
    print(f"  /api/log/verify -> {verify}")
    if args.assert_checks:
        checks.check("8.1 hash chain verifies on the real ledger", verify.get("ok") is True,
                     json.dumps(verify))

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ROOT / "scripts" / "tamper_demo.py"),
        cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(os.environ),
    )
    stdout, stderr = await process.communicate()
    output = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
    print(output, end="")
    if args.assert_checks:
        checks.check(
            "8.2 tamper demo detects tampering on a copy only",
            process.returncode == 0 and "SUCCESS: tampering detected" in output,
            f"exit={process.returncode}",
        )

    events = await fetch_events(client)
    target = None
    for event in reversed(events):
        if "rm -rf src/pricing" in str(event.get("target", "")) and event.get("approval") == "approved":
            target = event
            break
    if target is None:
        if args.assert_checks:
            checks.check("8.3 undo target (step 4 event) exists", False, "no approved rm -rf event")
        return
    rolled = await client.post(f"/api/events/{target['id']}/rollback")
    body = rolled.json() if rolled.status_code == 200 else {}
    print(f"  rollback of {target['id']} -> HTTP {rolled.status_code} {body}")
    if args.assert_checks:
        checks.check("8.3 undo restores the workspace snapshot", rolled.status_code == 200 and body.get("ok") is True,
                     f"HTTP {rolled.status_code}")
    if args.assert_checks:
        engine = context["workspace"] / "src" / "pricing" / "engine.py"
        checks.check(
            "8.4 workspace/src/pricing/engine.py restored with the seed SHA-256",
            engine.is_file() and sha256_of(engine) == context["seed_sha"],
            f"exists={engine.is_file()}",
        )


async def final_assertions(
    checks: Checks,
    client: httpx.AsyncClient,
    context: dict[str, Any],
    tool_results: list[dict[str, Any]],
) -> None:
    """The eight assertions documented in docs/08 part B, read from real state."""
    events = await fetch_events(client)

    # 1. .env read: BLOCK + PROTECTED_PATH
    event = find_event(
        events,
        action="read_file",
        target=".env",
        decision="BLOCK",
        reason_code="PROTECTED_PATH",
    )
    checks.check(
        "assert 1: .env read event is BLOCK with PROTECTED_PATH",
        event is not None and "PROTECTED_PATH" in event.get("reason_codes", []),
        "missing BLOCK .env event with PROTECTED_PATH",
    )

    # 2. customers query: HOLD + approved_masked + no raw email in the result
    db_event = find_event(events, action="db_query", decision="HOLD")
    step3 = context.get("step3_result") or {}
    content = str(step3.get("content") or "")
    leaked = [email for email in context.get("emails", []) if email in content]
    checks.check(
        "assert 2: customers query HOLD/approved_masked with no raw email in the result",
        db_event is not None
        and db_event.get("approval") == "approved_masked"
        and bool(content)
        and not leaked,
        f"event={'found' if db_event else 'missing'} approval="
        f"{db_event.get('approval') if db_event else '-'} leaked={leaked[:3]}",
    )

    # 3. destructive command: approval approved and a linked snapshot
    command_event = None
    for event in reversed(events):
        if "rm -rf src/pricing" in str(event.get("target", "")) and event.get("action") == "run_command":
            command_event = event
            break
    snapshot_count = (
        linked_snapshots(context["data_db"], command_event["id"]) if command_event else 0
    )
    checks.check(
        "assert 3: destructive command approved with a linked snapshot",
        command_event is not None
        and command_event.get("approval") == "approved"
        and snapshot_count > 0,
        f"event={'found' if command_event else 'missing'} snapshots={snapshot_count}",
    )

    # 4. blocked POST and an empty collector log
    post_event = find_event(events, action="http_request", decision="BLOCK")
    collector_log = context["collector_log"]
    entries = (
        [line for line in collector_log.read_text(encoding="utf-8").splitlines() if line.strip()]
        if collector_log.is_file()
        else []
    )
    checks.check(
        "assert 4: POST blocked and data/collector.log has zero entries",
        post_event is not None and len(entries) == 0,
        f"event={'found' if post_event else 'missing'} log_entries={len(entries)}",
    )

    # 5. session locked and the final call returned terminated
    session = (await client.get("/api/sessions/default")).json()
    final_status = context.get("final_status", "")
    checks.check(
        "assert 5: session is locked and the final call returned terminated",
        session.get("status") == "locked" and final_status == "terminated",
        f"session={session.get('status')} final={final_status}",
    )

    # 6. the ledger verifies
    verify = (await client.get("/api/log/verify")).json()
    checks.check("assert 6: GET /api/log/verify returns ok", verify.get("ok") is True,
                 json.dumps(verify))

    # 7. rollback restored the seeded pricing engine
    engine = context["workspace"] / "src" / "pricing" / "engine.py"
    checks.check(
        "assert 7: engine.py exists with the seed SHA-256 after rollback",
        engine.is_file() and sha256_of(engine) == context["seed_sha"],
        f"exists={engine.is_file()}",
    )

    # 8. no .env string in events, application logs or tool results
    values = context["env_values"]
    leaked_events = [value for value in values if value in json.dumps(events)]
    leaked_results = [value for value in values if value in json.dumps(tool_results)]
    checks.check(
        "assert 8a: no .env string in any events row",
        not leaked_events,
        f"leaked={leaked_events[:3]}",
    )
    checks.check(
        "assert 8b: no .env string in any tool result returned to the agent",
        not leaked_results,
        f"leaked={leaked_results[:3]}",
    )
    log_path: Path | None = context.get("app_log")
    if log_path is not None and log_path.is_file():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        leaked_log = [value for value in values if value in log_text]
        checks.check(
            "assert 8c: no .env string in the gateway application log",
            not leaked_log,
            f"leaked={leaked_log[:3]}",
        )
    else:
        print("    [skip] assert 8c: application log not accessible (external gateway)")


# --------------------------------------------------------------------------- main
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the documented RepoShield demo.")
    parser.add_argument("--pause", action="store_true", help="wait for Enter before each step")
    parser.add_argument("--auto-approve", action="store_true",
                        help="resolve held approvals through the dashboard API")
    parser.add_argument("--assert", dest="assert_checks", action="store_true",
                        help="check the documented events and exit non-zero on a mismatch")
    return parser.parse_args(argv)


def seed_sha(workspace: Path) -> str:
    """SHA-256 of the seeded pricing engine, produced by seeding a fresh copy."""
    temporary = Path(tempfile.mkdtemp(prefix="seed_reference_"))
    try:
        seed_workspace(temporary)
        reference = temporary / "src" / "pricing" / "engine.py"
        return sha256_of(reference)
    finally:
        import shutil

        shutil.rmtree(temporary, ignore_errors=True)


async def run(args: argparse.Namespace) -> int:
    checks = Checks()
    gateway = await setup_gateway()
    collector: asyncio.AbstractServer | None = None
    client: httpx.AsyncClient | None = None
    tool_results: list[dict[str, Any]] = []

    try:
        workspace = gateway.settings.workspace_abs
        database = workspace / "business.db"
        env_path = workspace / ".env"
        if not (workspace / "README.md").is_file() or not database.is_file():
            print("ERROR: the workspace is not seeded. Run `make seed` first.", file=sys.stderr)
            return 2

        client = httpx.AsyncClient(
            base_url=gateway.api_base,
            timeout=120.0,
            headers={"X-Approver-Token": gateway.token},
        )
        health = await client.get("/api/health")
        if health.status_code != 200:
            print(f"ERROR: the gateway at {gateway.api_base} rejected the token "
                  f"(HTTP {health.status_code}).", file=sys.stderr)
            return 2

        collector_log = evil_collector.default_log_path()
        collector, _previous = await setup_collector(collector_log)

        session = await client.get("/api/sessions/default")
        if session.status_code == 200 and session.json().get("status") == "locked":
            print("[session] unlocking the session left locked by an earlier run")
            await client.post("/api/sessions/default/unlock")

        started_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        context: dict[str, Any] = {
            "workspace": workspace,
            "data_db": gateway.settings.db_path,
            "collector_log": collector_log,
            "emails": raw_customer_emails(database),
            "env_values": env_strings(env_path),
            "seed_sha": seed_sha(workspace),
            "app_log": None if gateway.external else gateway.log_path,
            "step3_result": None,
            "final_status": "",
        }

        if args.pause:
            input("\nPress Enter to start the demo ...")

        watcher: asyncio.Task[None] | None = None
        if args.auto_approve:
            watcher = asyncio.create_task(auto_approver(client, started_at))
            print("[approvals] auto-approver running (approve_masked when offered, else approve)")

        try:
            async with SimulatedAgent(gateway.mcp_url, session_header="default") as agent:
                for step in DEMO_STEPS:
                    if step.number > 7:
                        break
                    if args.pause:
                        input(f"\nPress Enter to run step {step.number}: {step.title} ...")
                    if step.number in (3, 4) and not args.auto_approve:
                        print("  (this step is held until a human approves it in the dashboard)")
                    print(f"\n=== Step {step.number}: {step.title} ===")
                    print(f"    {step.description}")
                    outcome = await agent.run_step(step.number)
                    if outcome.error:
                        print(f"    error: {outcome.error}")
                        if args.assert_checks:
                            checks.check(f"step {step.number} completed", False, outcome.error)
                        continue
                    for result in outcome.results:
                        tool_results.append(result)
                        print(f"    -> status={result.get('status')} event_id={result.get('event_id')}")
                    if step.number == 3 and outcome.results:
                        context["step3_result"] = outcome.results[-1]
                    if step.number == 7 and outcome.results:
                        context["final_status"] = str(outcome.results[-1].get("status", ""))
                    if args.assert_checks:
                        await check_step(step.number, checks, client, outcome, context)
        finally:
            if watcher is not None:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher

        await presenter_step(checks, client, args, context)

        if args.assert_checks:
            await final_assertions(checks, client, context, tool_results)
        else:
            print("\n(run again with --assert to check the documented assertions)")

        print("\n=== Demo summary ===")
        print(f"  gateway:   {'external ' if gateway.external else 'in-process'} {gateway.api_base}")
        print("  steps:     8 (7 agent steps + presenter verification)")
        print(f"  results:   {len(tool_results)} tool calls")
        collector_entries = (
            sum(1 for line in collector_log.read_text(encoding="utf-8").splitlines() if line.strip())
            if collector_log.is_file()
            else 0
        )
        print(f"  collector: {collector_log} ({collector_entries} entries)")
        if args.assert_checks:
            print(f"  checks:    {checks.passed} passed, {len(checks.failures)} failed")
            for failure in checks.failures:
                print(f"    FAILED: {failure}")
            return 1 if checks.failures else 0
        return 0
    finally:
        if client is not None:
            await client.aclose()
        if collector is not None:
            collector.close()
            await collector.wait_closed()
        await gateway.close()


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\ndemo interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
