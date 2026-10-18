"""Audit chain append, concurrency and tamper detection."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from backend.core.audit import GENESIS, canonical, compute_hash, verify_chain
from backend.tests.helpers import make_event, make_runtime


def backup_database(source: Path, destination: Path) -> None:
    source_conn = sqlite3.connect(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
    finally:
        source_conn.close()
        destination_conn.close()


def test_canonical_and_hash_are_stable() -> None:
    row = {"id": "evt_1", "target": "a", "prev_hash": GENESIS, "hash": "ignored"}
    assert canonical(row) == b'{"id":"evt_1","prev_hash":"' + GENESIS.encode() + b'","target":"a"}'
    assert compute_hash(GENESIS, row) == compute_hash(GENESIS, row)


def test_append_and_verify(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    assert runtime.audit.verify_chain().ok

    async def run() -> None:
        for index in range(3):
            await runtime.audit.append_event(make_event(index + 1))

    asyncio.run(run())
    result = verify_chain(runtime.audit.path)
    assert result.ok and result.checked == 3
    events = runtime.audit.list_events()
    assert [event.seq for event in events] == [1, 2, 3]
    assert events[0].prev_hash == GENESIS
    assert events[1].prev_hash == events[0].hash


def test_concurrent_appends_stay_linear(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    async def run() -> None:
        await asyncio.gather(*(runtime.audit.append_event(make_event(index)) for index in range(1, 101)))

    asyncio.run(run())
    result = runtime.audit.verify_chain()
    assert result.ok and result.checked == 100
    events = runtime.audit.list_events(limit=200)
    assert [event.seq for event in events] == list(range(1, 101))
    for previous, current in zip(events, events[1:], strict=False):
        assert current.prev_hash == previous.hash


def test_modified_field_is_detected(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    async def run() -> None:
        for index in range(1, 4):
            await runtime.audit.append_event(make_event(index))

    asyncio.run(run())
    copy = tmp_path / "tampered.db"
    backup_database(runtime.audit.path, copy)
    with sqlite3.connect(copy) as conn:
        conn.execute("UPDATE events SET reason_text = 'changed' WHERE seq = 2")
        conn.commit()
    result = verify_chain(copy)
    assert not result.ok
    assert result.reason == "hash_mismatch"
    assert result.first_bad_seq == 2
    assert runtime.audit.verify_chain().ok


def test_deleted_tail_is_detected(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    async def run() -> None:
        for index in range(1, 4):
            await runtime.audit.append_event(make_event(index))

    asyncio.run(run())
    copy = tmp_path / "truncated.db"
    backup_database(runtime.audit.path, copy)
    with sqlite3.connect(copy) as conn:
        conn.execute("DELETE FROM events WHERE seq = 3")
        conn.commit()
    result = verify_chain(copy)
    assert not result.ok
    assert result.reason == "head_mismatch"


def test_reordered_rows_are_detected(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    async def run() -> None:
        for index in range(1, 4):
            await runtime.audit.append_event(make_event(index))

    asyncio.run(run())
    copy = tmp_path / "reordered.db"
    backup_database(runtime.audit.path, copy)
    with sqlite3.connect(copy) as conn:
        conn.execute("UPDATE events SET seq = 99 WHERE seq = 2")
        conn.execute("UPDATE events SET seq = 2 WHERE seq = 3")
        conn.execute("UPDATE events SET seq = 3 WHERE seq = 99")
        conn.commit()
    result = verify_chain(copy)
    assert not result.ok
    assert result.reason == "prev_hash_mismatch"


def test_removed_chain_head_is_detected(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    async def run() -> None:
        await runtime.audit.append_event(make_event(1))

    asyncio.run(run())
    copy = tmp_path / "nohead.db"
    backup_database(runtime.audit.path, copy)
    with sqlite3.connect(copy) as conn:
        conn.execute("DELETE FROM chain_head")
        conn.commit()
    result = verify_chain(copy)
    assert not result.ok
    assert result.reason == "head_mismatch"
