"""Snapshot and rollback tests for files, databases and workspaces."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from backend.tests.helpers import make_runtime


async def test_file_snapshot_and_one_time_rollback(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    target = runtime.workspace / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("original", encoding="utf-8")
    event = await runtime.pipeline.record_system_event(
        session_id="default", action="prepare", target="src/app.py"
    )
    snapshot = await runtime.snapshots.create_file(
        event_id=event.id, session_id="default", rel_path="src/app.py", abs_path=target
    )
    runtime.audit.insert_snapshot(snapshot)
    assert runtime.audit.snapshots_for_event(event.id)

    target.write_text("changed", encoding="utf-8")
    await runtime.snapshots.rollback(snapshot)
    assert target.read_text(encoding="utf-8") == "original"
    with pytest.raises(ValueError, match="already been rolled back"):
        await runtime.snapshots.rollback(snapshot)


async def test_new_file_rollback_deletes_it(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    target = runtime.workspace / "new.txt"
    event = await runtime.pipeline.record_system_event(
        session_id="default", action="prepare", target="new.txt"
    )
    snapshot = await runtime.snapshots.create_file(
        event_id=event.id, session_id="default", rel_path="new.txt", abs_path=target
    )
    runtime.audit.insert_snapshot(snapshot)
    assert snapshot.existed is False
    target.write_text("created by the agent", encoding="utf-8")
    await runtime.snapshots.rollback(snapshot)
    assert not target.exists()


async def test_database_snapshot_and_rollback(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    database = runtime.workspace / "business.db"
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO items (id, name) VALUES (1, 'one')")
        conn.commit()

    event = await runtime.pipeline.record_system_event(
        session_id="default", action="prepare", target="business.db"
    )
    snapshot = await runtime.snapshots.create_database(
        event_id=event.id, session_id="default", source=database, target="business.db"
    )
    runtime.audit.insert_snapshot(snapshot)
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO items (id, name) VALUES (2, 'two')")
        conn.commit()

    await runtime.snapshots.rollback(snapshot)
    with closing(sqlite3.connect(database)) as conn:
        rows = conn.execute("SELECT id, name FROM items ORDER BY id").fetchall()
    assert rows == [(1, "one")]


async def test_workspace_snapshot_restores_deleted_directory(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    engine = runtime.workspace / "src" / "pricing" / "engine.py"
    engine.parent.mkdir(parents=True)
    engine.write_text("def quote():\n    return 1\n", encoding="utf-8")
    event = await runtime.pipeline.record_system_event(
        session_id="default", action="prepare", target="src/pricing"
    )
    snapshot = await runtime.snapshots.create_workspace(
        event_id=event.id, session_id="default"
    )
    runtime.audit.insert_snapshot(snapshot)
    shutil.rmtree(runtime.workspace / "src")
    await runtime.snapshots.rollback(snapshot)
    assert engine.read_text(encoding="utf-8") == "def quote():\n    return 1\n"

    rollback_event = await runtime.pipeline.record_system_event(
        session_id="default", action="rollback", target="src/pricing"
    )
    assert rollback_event.action == "rollback"
    assert runtime.audit.verify_chain().ok


async def test_undoable_returns_unrolled_snapshot(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    target = runtime.workspace / "a.txt"
    target.write_text("a", encoding="utf-8")
    event = await runtime.pipeline.record_system_event(
        session_id="default", action="prepare", target="a.txt"
    )
    snapshot = await runtime.snapshots.create_file(
        event_id=event.id, session_id="default", rel_path="a.txt", abs_path=target
    )
    runtime.audit.insert_snapshot(snapshot)
    undoable = await runtime.snapshots.undoable(event.id)
    assert undoable is not None and undoable.id == snapshot.id
    await runtime.snapshots.rollback(snapshot)
    assert await runtime.snapshots.undoable(event.id) is None
