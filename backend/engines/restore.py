"""Restore-on-write for files and SQL string literals."""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from backend.core.models import MapEntry, RestoreResult
from backend.engines.text_masker import MaskMapStore

LOGIC_MARKER = re.compile(
    r"^(?P<indent>[ \t]*)pass  # \[MASKED_LOGIC_BLOCK_(?P<n>\d+)\][ \t]*(?P<newline>\r?\n|$)",
    re.MULTILINE,
)
PLACEHOLDER_TOKEN = re.compile(r"\[(?:MASKED_[A-Z0-9_]+|[A-Z][A-Z0-9_]*_\d+)\]")


def _reindent(original: str, original_indent: str | None, new_indent: str) -> tuple[str, bool]:
    """Re-indent an original block, returning (text, ok)."""
    if original_indent is None or new_indent == original_indent:
        return original, True
    lines = original.splitlines(keepends=True)
    result: list[str] = []
    for line in lines:
        if not line.strip():
            result.append(line)
            continue
        if not line.startswith(original_indent):
            return original, False
        result.append(new_indent + line[len(original_indent) :])
    return "".join(result), True


def restore_file(
    content: str,
    *,
    rel_path: str,
    session_id: str,
    store: MaskMapStore,
    current_sha256: str | None = None,
) -> RestoreResult:
    """Replace known placeholders and logic markers in a proposed write."""
    entries = store.entries_for_scope(session_id, rel_path) + store.entries_for_scope(
        session_id, "global"
    )
    flags: list[str] = []
    reason_codes: list[str] = []
    requires_hold = False

    logic_entries = [entry for entry in entries if entry.kind == "MASKED_LOGIC_BLOCK"]
    if logic_entries:
        stale = any(
            entry.base_sha256 is not None
            and current_sha256 is not None
            and entry.base_sha256 != current_sha256
            for entry in logic_entries
        )
        if stale:
            flags.append("STALE_MASKED_BASE")
            reason_codes.append("STALE_MASKED_BASE")
            requires_hold = True

    by_placeholder = {entry.placeholder: entry for entry in logic_entries}
    seen_markers: set[str] = set()

    def replace_marker(match: re.Match[str]) -> str:
        placeholder = f"[MASKED_LOGIC_BLOCK_{match.group('n')}]"
        entry = by_placeholder.get(placeholder)
        if entry is None:
            return match.group(0)
        seen_markers.add(placeholder)
        new_indent = match.group("indent")
        restored, ok = _reindent(entry.original, entry.original_indent, new_indent)
        if not ok:
            flags.append("UNMASKABLE_FORMAT")
            reason_codes.append("UNMASKABLE_FORMAT")
            return match.group(0)
        newline = match.group("newline")
        if newline and not restored.endswith(("\n", "\r")):
            restored += newline
        return restored

    restored_content = LOGIC_MARKER.sub(replace_marker, content)

    missing = [entry.placeholder for entry in logic_entries if entry.placeholder not in seen_markers]
    if missing:
        flags.append("MASKED_BLOCK_REMOVED")
        reason_codes.append("MASKED_BLOCK_REMOVED")
        requires_hold = True

    value_entries: list[MapEntry] = [entry for entry in entries if entry.kind != "MASKED_LOGIC_BLOCK"]
    for entry in sorted(value_entries, key=lambda item: len(item.placeholder), reverse=True):
        if entry.placeholder in restored_content:
            restored_content = restored_content.replace(entry.placeholder, entry.original)

    known = {entry.placeholder for entry in entries}
    if any(token.group(0) not in known for token in PLACEHOLDER_TOKEN.finditer(restored_content)):
        flags.append("unknown_placeholder")

    deduped_flags = list(dict.fromkeys(flags))
    deduped_reasons = list(dict.fromkeys(reason_codes))
    return RestoreResult(
        content=restored_content,
        flags=deduped_flags,
        reason_codes=deduped_reasons,
        requires_hold=requires_hold,
    )


def restore_sql(
    sql: str,
    *,
    table: str,
    session_id: str,
    store: MaskMapStore,
) -> RestoreResult:
    """Replace SQL string literals that equal a known database placeholder."""
    entries = store.entries_for_scope(session_id, f"db:{table}") + store.entries_for_scope(
        session_id, "global"
    )
    mapping = {entry.placeholder: entry.original for entry in entries}
    if not mapping:
        return RestoreResult(content=sql)
    try:
        tree = sqlglot.parse_one(sql, dialect="sqlite")
    except Exception:
        return RestoreResult(content=sql, flags=["unparseable_sql"])
    replaced = False
    for literal in list(tree.find_all(exp.Literal)):
        if literal.is_string and literal.this in mapping:
            literal.replace(exp.Literal.string(mapping[literal.this]))
            replaced = True
    if not replaced:
        return RestoreResult(content=sql)
    return RestoreResult(content=tree.sql(dialect="sqlite"))
