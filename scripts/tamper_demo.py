"""Tamper demo: prove the audit chain detects tampering on a COPY of the ledger.

The real ledger is never modified. Its SHA-256 is printed before and after and
must be identical; the script exits non-zero when it is not.

Usage: make tamper-demo    (the ledger needs at least one event, so run make demo first)
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root

from backend.config import get_settings  # noqa: E402
from backend.core.audit import verify_chain  # noqa: E402


def sha256_of(path: Path) -> str:
    """Return the SHA-256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_ledger(source: Path, target: Path) -> None:
    """Copy the database including any WAL sidecars the live gateway holds."""
    shutil.copy2(source, target)
    for suffix in ("-wal", "-shm"):
        sidecar = source.with_name(source.name + suffix)
        if sidecar.is_file():
            shutil.copy2(sidecar, target.with_name(target.name + suffix))


def tamper_copy(db_path: Path, tmp_path: Path) -> tuple[bool, int]:
    """Copy the ledger, tamper with a middle entry and verify the copy.

    Returns (detected, entry_number).
    """
    copy_ledger(db_path, tmp_path)
    with closing(sqlite3.connect(tmp_path)) as copy:
        copy.execute("UPDATE events SET reason_text = 'tampered' WHERE seq = ?", (_middle_seq(db_path),))
        copy.commit()
    result = verify_chain(tmp_path)
    return (not result.ok), result.first_bad_seq


def _middle_seq(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as source:
        sequences = [row[0] for row in source.execute("SELECT seq FROM events ORDER BY seq")]
    if not sequences:
        raise SystemExit("ERROR: the ledger has no events yet. Run `make demo` first.")
    return sequences[len(sequences) // 2]


def main() -> int:
    db_path = get_settings().db_path
    if not db_path.is_file():
        print(f"ERROR: {db_path} does not exist. Run `make run` and `make demo` first.", file=sys.stderr)
        return 1

    before = sha256_of(db_path)
    handle, tmp_name = tempfile.mkstemp(suffix=".db", prefix="audit_tamper_")
    os.close(handle)
    tmp_path = Path(tmp_name)
    tmp_path.unlink(missing_ok=True)

    detected = False
    entry = -1
    try:
        detected, entry = tamper_copy(db_path, tmp_path)
    finally:
        for path in (tmp_path, *[tmp_path.with_name(tmp_path.name + suffix) for suffix in ("-wal", "-shm")]):
            path.unlink(missing_ok=True)

    after = sha256_of(db_path)
    print(f"  {db_path.name} SHA-256 before: {before}")
    print(f"  {db_path.name} SHA-256 after:  {after}")
    if before != after:
        print("ERROR: the real ledger changed during the demo.", file=sys.stderr)
        return 1

    if not detected:
        print("FAILURE: tampering of the copy was NOT detected.", file=sys.stderr)
        return 1
    print(f"SUCCESS: tampering detected at entry #{entry} on the COPY.")
    print("  The real ledger was not modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
