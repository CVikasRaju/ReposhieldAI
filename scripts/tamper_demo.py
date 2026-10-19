"""Tamper demo: verify audit chain tamper detection works."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from backend.core.audit import verify_chain


def main() -> int:
    db_path = Path("backend/data/audit.db")
    if not db_path.exists():
        print("ERROR: audit.db not found. Run `make seed` first.", file=sys.stderr)
        return 1

    # Copy the database to a temp file
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        import shutil
        shutil.copy2(str(db_path), str(tmp_path))

        # Read the original and tampers a middle event's reason_text
        import sqlite3
        with closing(sqlite3.connect(str(tmp_path))) as conn:
            row = conn.execute(
                "SELECT seq, event_id, reason_text FROM events ORDER BY seq LIMIT 1 OFFSET 5"
            ).fetchone()
            if row is None:
                print("ERROR: Not enough events in the database.", file=sys.stderr)
                return 1

            seq, event_id, original_reason = row
            # Tamper with the reason_text
            conn.execute("UPDATE events SET reason_text = 'tampered' WHERE seq = ?", (seq,))
            conn.commit()

        # Verify the chain on the tampered copy
        result = verify_chain(tmp_path)

        if result.ok is False:
            print(f"SUCCESS: Tampering detected at entry #{result.first_bad_seq}")
            print(f"  Chain broken: ok={result.ok}, first_bad_seq={result.first_bad_seq}")
            return 0
        else:
            print("FAILURE: Tampering was not detected! The chain is still valid.", file=sys.stderr)
            return 1

    finally:
        # Clean up the temp file
        try:
            tmp_path.unlink()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())