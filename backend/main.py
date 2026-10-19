"""Start the MCP and dashboard listeners in one process."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from backend.api_app import DashboardDeps, create_api_app
from backend.config import Settings, get_settings
from backend.connectors.command import CommandConnector
from backend.connectors.filesystem import FilesystemConnector
from backend.connectors.network import NetworkConnector
from backend.connectors.sqlite_db import SQLiteConnector
from backend.core.approvals import ApprovalManager
from backend.core.audit import AuditLog, utc_now
from backend.core.events import EventBus
from backend.core.models import SessionRecord
from backend.core.pipeline import Pipeline
from backend.core.sessions import SessionManager
from backend.core.snapshots import SnapshotManager
from backend.engines.behaviour import BehaviourTracker
from backend.engines.content_model import ContentModel
from backend.engines.text_masker import MaskMapStore
from backend.mcp_app import create_mcp_app
from backend.policy.loader import load_policy

logger = logging.getLogger(__name__)


@dataclass
class Gateway:
    """Everything the two listeners share."""

    settings: Settings
    pipeline: Pipeline
    audit: AuditLog
    approvals: ApprovalManager
    sessions: SessionManager
    bus: EventBus
    snapshots: SnapshotManager
    policy: object
    mcp_app: object
    api_app: object


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def build_gateway(
    settings: Settings | None = None, *, interactive_approvals: bool | None = None
) -> Gateway:
    """Construct the policy, storage, connectors and both applications."""
    settings = settings or get_settings()
    _secure_directory(settings.data_abs)
    settings.workspace_abs.mkdir(parents=True, exist_ok=True)

    policy = load_policy(settings.policy_abs, settings.profile)
    database = Path(policy.paths.database)
    if not database.is_absolute():
        database = (Path.cwd() / database).resolve()
    if not database.is_relative_to(settings.workspace_abs):
        raise ValueError("policy.paths.database must be inside the workspace")

    audit = AuditLog(settings.db_path)
    audit.insert_session(
        SessionRecord(
            id="default",
            name="default",
            profile=policy.profile,
            task=policy.task.description or None,
            started_at=utc_now(),
        )
    )
    bus = EventBus()
    behaviour = BehaviourTracker(policy.behaviour, policy.kill_switch)
    sessions = SessionManager(audit, behaviour)
    approvals = ApprovalManager(
        audit, bus, policy.approval, interactive=interactive_approvals
    )
    snapshots = SnapshotManager(audit, settings.data_abs, settings.workspace_abs)
    store = MaskMapStore(audit.connection, audit.thread_lock)
    connectors = {
        "filesystem": FilesystemConnector(policy, settings.workspace_abs),
        "command": CommandConnector(policy, settings.workspace_abs),
        "database": SQLiteConnector(policy, settings.workspace_abs, database),
        "network": NetworkConnector(policy),
    }
    pipeline = Pipeline(
        policy=policy,
        audit=audit,
        sessions=sessions,
        approvals=approvals,
        snapshots=snapshots,
        bus=bus,
        connectors=connectors,
        content_model=ContentModel(),
        behaviour=behaviour,
        store=store,
        workspace_root=settings.workspace_abs,
    )
    mcp_app = create_mcp_app(pipeline)
    api_app = create_api_app(
        DashboardDeps(
            settings=settings,
            policy=policy,
            audit=audit,
            approvals=approvals,
            sessions=sessions,
            snapshots=snapshots,
            bus=bus,
        )
    )
    return Gateway(
        settings=settings,
        pipeline=pipeline,
        audit=audit,
        approvals=approvals,
        sessions=sessions,
        bus=bus,
        snapshots=snapshots,
        policy=policy,
        mcp_app=mcp_app,
        api_app=api_app,
    )


class _QuietServer(uvicorn.Server):
    """Uvicorn server that does not install process signal handlers."""

    def install_signal_handlers(self) -> None:  # pragma: no cover - trivial
        return


async def serve(gateway: Gateway) -> None:
    """Run both listeners until cancelled."""
    settings = gateway.settings
    mcp_server = _QuietServer(
        uvicorn.Config(
            gateway.mcp_app,
            host=settings.mcp_host,
            port=settings.mcp_port,
            log_level=settings.log_level.lower(),
            lifespan="on",
            access_log=False,
        )
    )
    api_server = _QuietServer(
        uvicorn.Config(
            gateway.api_app,
            host=settings.api_host,
            port=settings.api_port,
            log_level=settings.log_level.lower(),
            lifespan="on",
            access_log=False,
        )
    )
    await asyncio.gather(mcp_server.serve(), api_server.serve())


def banner(gateway: Gateway) -> str:
    """Return the startup banner with the token and dashboard URL."""
    settings = gateway.settings
    dashboard = f"http://localhost:5173/#token={settings.approver_token}"
    return (
        "\n"
        "  RepoShield AI is running\n"
        f"  MCP gateway (agent side):  http://{settings.mcp_host}:{settings.mcp_port}/mcp\n"
        f"  Dashboard API (human side): http://{settings.api_host}:{settings.api_port}\n"
        f"  Approver token:            {settings.approver_token}\n"
        f"  Open the dashboard:        {dashboard}\n"
        "  The MCP listener serves only /mcp. The API listener serves only /api/* and /ws.\n"
    )


def main() -> int:
    """Entry point used by `make run`."""
    logging.basicConfig(
        level=os.environ.get("RS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    gateway = build_gateway(settings)
    print(banner(gateway), flush=True)
    try:
        asyncio.run(serve(gateway))
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pass
    finally:
        gateway.audit.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
