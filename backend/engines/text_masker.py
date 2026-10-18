"""Structure-preserving text masking and placeholder persistence."""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import UTC, datetime

from backend.core.models import Change, MapEntry, MaskResult, Span
from backend.engines.detectors import SECRET_KINDS

_MASK_MAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS mask_map (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id    TEXT NOT NULL,
  scope         TEXT NOT NULL,
  placeholder   TEXT NOT NULL,
  kind          TEXT NOT NULL,
  original      TEXT NOT NULL,
  original_indent TEXT,
  base_sha256   TEXT,
  created_at    TEXT NOT NULL,
  UNIQUE (session_id, scope, placeholder)
)
"""


def span_placeholder_spec(span: Span) -> tuple[str, int, str]:
    """Return (family, zero-padding, numbering scope) for a detector span."""
    if span.kind in SECRET_KINDS or span.category == "secret":
        return "MASKED_SECRET", 2, "scope"
    if span.kind == "EMAIL":
        return "EMAIL", 0, "session"
    if span.kind == "PHONE":
        return "PHONE", 0, "session"
    if span.kind == "US_SSN":
        return "MASKED_SSN", 0, "session"
    if span.kind == "CREDIT_CARD":
        return "MASKED_CARD", 0, "session"
    if span.kind in {"AADHAAR", "PAN"}:
        return "NATIONAL_ID", 0, "session"
    if span.kind == "IBAN":
        return "ACCOUNT", 0, "session"
    return "MASKED_PII", 0, "session"


def column_placeholder_family(column: str) -> str:
    """Map a protected column name to its placeholder family."""
    lowered = column.lower()
    if lowered in {"full_name", "name"}:
        return "NAME"
    if "email" in lowered:
        return "EMAIL"
    if "phone" in lowered:
        return "PHONE"
    if "national_id" in lowered or "aadhaar" in lowered:
        return "NATIONAL_ID"
    if "account" in lowered or "iban" in lowered:
        return "ACCOUNT"
    if "card" in lowered:
        return "MASKED_CARD"
    if "ssn" in lowered:
        return "MASKED_SSN"
    safe = re.sub(r"[^A-Z0-9]+", "_", column.upper()).strip("_")
    return f"MASKED_{safe or 'PII'}"


def reason_for_span(span: Span) -> str:
    """Return the plain delta reason code for a masked span."""
    return "SECRET_DETECTED" if span.category == "secret" else "PII_DETECTED"


class MaskMapStore:
    """Persistence for placeholder-to-original mappings.

    The store takes a sqlite connection so tests can use an in-memory database.
    Placeholders are stable for the session: the same original and kind always
    receives the same token.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(_MASK_MAP_SCHEMA)
            self._conn.commit()

    def allocate(
        self,
        *,
        session_id: str,
        scope: str,
        family: str,
        original: str,
        pad: int = 0,
        numbering: str = "session",
        original_indent: str | None = None,
        base_sha256: str | None = None,
    ) -> str:
        """Return the stable placeholder for a value and persist it."""
        with self._lock:
            if numbering == "scope":
                row = self._conn.execute(
                    "SELECT placeholder FROM mask_map WHERE session_id=? AND scope=? AND kind=? AND original=?",
                    (session_id, scope, family, original),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT placeholder FROM mask_map WHERE session_id=? AND kind=? AND original=? "
                    "ORDER BY id LIMIT 1",
                    (session_id, family, original),
                ).fetchone()
            if row:
                return str(row[0])

            if numbering == "scope":
                rows = self._conn.execute(
                    "SELECT placeholder FROM mask_map WHERE session_id=? AND scope=? AND kind=?",
                    (session_id, scope, family),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT placeholder FROM mask_map WHERE session_id=? AND kind=?",
                    (session_id, family),
                ).fetchall()
            highest = 0
            for (existing,) in rows:
                match = re.fullmatch(r"\[" + re.escape(family) + r"_(\d+)\]", str(existing))
                if match:
                    highest = max(highest, int(match.group(1)))
            number = highest + 1
            placeholder = f"[{family}_{number:0{pad}d}]" if pad else f"[{family}_{number}]"
            self._conn.execute(
                "INSERT OR IGNORE INTO mask_map "
                "(session_id, scope, placeholder, kind, original, original_indent, base_sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    scope,
                    placeholder,
                    family,
                    original,
                    original_indent,
                    base_sha256,
                    datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                ),
            )
            self._conn.commit()
            return placeholder

    def entries_for_scope(self, session_id: str, scope: str) -> list[MapEntry]:
        """Return all placeholder mappings for one scope."""
        rows = self._conn.execute(
            "SELECT scope, placeholder, kind, original, original_indent, base_sha256 "
            "FROM mask_map WHERE session_id=? AND scope=? ORDER BY id",
            (session_id, scope),
        ).fetchall()
        return [
            MapEntry(
                scope=str(row[0]),
                placeholder=str(row[1]),
                kind=str(row[2]),
                original=str(row[3]),
                original_indent=row[4],
                base_sha256=row[5],
            )
            for row in rows
        ]

    def entries_for_session(self, session_id: str) -> list[MapEntry]:
        """Return every mapping for a session."""
        rows = self._conn.execute(
            "SELECT scope, placeholder, kind, original, original_indent, base_sha256 "
            "FROM mask_map WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [
            MapEntry(
                scope=str(row[0]),
                placeholder=str(row[1]),
                kind=str(row[2]),
                original=str(row[3]),
                original_indent=row[4],
                base_sha256=row[5],
            )
            for row in rows
        ]


def merge_spans(spans: list[Span], content_length: int) -> list[Span]:
    """Drop out-of-range spans and resolve overlaps: confidence first, then length."""
    valid = [
        span
        for span in spans
        if 0 <= span.start < span.end <= content_length
    ]
    ordered = sorted(valid, key=lambda span: (span.start, -span.confidence, -(span.end - span.start)))
    merged: list[Span] = []
    for span in ordered:
        if merged and span.start < merged[-1].end:
            current = merged[-1]
            if (span.confidence, span.end - span.start) > (
                current.confidence,
                current.end - current.start,
            ):
                merged[-1] = span
            continue
        merged.append(span)
    return merged


def mask_text(
    raw: str,
    *,
    spans: list[Span],
    session_id: str,
    scope: str,
    store: MaskMapStore,
    base_sha256: str | None = None,
    release_pii: bool = False,
    reason_override: str | None = None,
) -> MaskResult:
    """Replace spans with stable placeholders and record exact offsets."""
    selected = [
        span
        for span in merge_spans(spans, len(raw))
        if not (release_pii and span.category != "secret")
    ]
    if not selected:
        return MaskResult(sanitized=raw)

    sanitized_parts: list[str] = []
    changes: list[Change] = []
    entries: dict[tuple[str, str], MapEntry] = {}
    cursor_raw = 0
    cursor_san = 0

    for span in selected:
        prefix = raw[cursor_raw : span.start]
        sanitized_parts.append(prefix)
        cursor_san += len(prefix)

        original = raw[span.start : span.end]
        family, pad, numbering = span_placeholder_spec(span)
        placeholder = store.allocate(
            session_id=session_id,
            scope=scope,
            family=family,
            original=original,
            pad=pad,
            numbering=numbering,
            base_sha256=base_sha256,
        )
        sanitized_parts.append(placeholder)
        changes.append(
            Change(
                raw_start=span.start,
                raw_end=span.end,
                san_start=cursor_san,
                san_end=cursor_san + len(placeholder),
                kind=span.kind,
                reason_code=reason_override or reason_for_span(span),
                placeholder=placeholder,
            )
        )
        cursor_san += len(placeholder)
        entries[(scope, placeholder)] = MapEntry(
            scope=scope,
            placeholder=placeholder,
            kind=family,
            original=original,
            base_sha256=base_sha256,
        )
        cursor_raw = span.end

    sanitized_parts.append(raw[cursor_raw:])
    return MaskResult(
        sanitized="".join(sanitized_parts),
        changes=changes,
        map_entries=list(entries.values()),
    )
