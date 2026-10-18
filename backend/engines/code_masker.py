"""Python code masking.

The stdlib ``ast`` module is used only to locate line ranges. Source is never
regenerated with ``ast.unparse``.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass

from backend.core.models import Change, MapEntry, MaskResult, Span
from backend.engines.text_masker import MaskMapStore, merge_spans, reason_for_span, span_placeholder_spec


@dataclass(frozen=True)
class Block:
    """A function body range to replace with a pass marker."""

    first: int
    last: int
    indent: str
    original: str


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def find_blocks(source: str, min_body_lines: int) -> list[Block]:
    """Return the top-most function bodies with at least min_body_lines lines."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    blocks: list[Block] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            is_function = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            if is_function:
                body = child.body
                start_index = 1 if body and _is_docstring(body[0]) else 0
                statements = body[start_index:]
                if statements:
                    first = statements[0].lineno
                    last = child.end_lineno or first
                    if first > child.lineno and (last - first + 1) >= min_body_lines:
                        indent = _leading_ws(lines[first - 1])
                        blocks.append(
                            Block(first, last, indent, "".join(lines[first - 1 : last]))
                        )
                        continue
            visit(child)

    visit(tree)
    return blocks


def line_offsets(source: str) -> list[int]:
    """Return the absolute offset of the start of each line."""
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _logic_replacement(block: Block, placeholder: str) -> str:
    newline = "\n" if block.original.endswith("\n") else ""
    return f"{block.indent}pass  # {placeholder}{newline}"


def mask_python(
    raw: str,
    *,
    spans: list[Span],
    mask_logic: bool,
    session_id: str,
    scope: str,
    store: MaskMapStore,
    min_body_lines: int = 3,
    base_sha256: str | None = None,
    release_pii: bool = False,
) -> MaskResult:
    """Mask function bodies and detector spans in Python source."""
    if not mask_logic and not spans:
        return MaskResult(sanitized=raw)

    edits: list[tuple[int, int, str, str, str, str]] = []
    # (raw_start, raw_end, replacement, change_kind, reason_code, placeholder)
    entries: list[MapEntry] = []
    flags: list[str] = []
    block_ranges: list[tuple[int, int]] = []

    if mask_logic:
        try:
            blocks = find_blocks(raw, min_body_lines)
        except SyntaxError:
            return MaskResult(sanitized=raw, flags=["UNMASKABLE_FORMAT"])
        offsets = line_offsets(raw)
        for block in blocks:
            placeholder = store.allocate(
                session_id=session_id,
                scope=scope,
                family="MASKED_LOGIC_BLOCK",
                original=block.original,
                pad=2,
                numbering="scope",
                original_indent=block.indent,
                base_sha256=base_sha256,
            )
            start = offsets[block.first - 1]
            end = offsets[block.last]
            replacement = _logic_replacement(block, placeholder)
            edits.append((start, end, replacement, "LOGIC_BLOCK", "SENSITIVE_LOCATION", placeholder))
            block_ranges.append((start, end))
            entries.append(
                MapEntry(
                    scope=scope,
                    placeholder=placeholder,
                    kind="MASKED_LOGIC_BLOCK",
                    original=block.original,
                    original_indent=block.indent,
                    base_sha256=base_sha256,
                )
            )

    selected = [
        span
        for span in merge_spans(spans, len(raw))
        if not (release_pii and span.category != "secret")
        and not any(span.start < end and span.end > start for start, end in block_ranges)
    ]
    for span in selected:
        family, pad, numbering = span_placeholder_spec(span)
        original = raw[span.start : span.end]
        placeholder = store.allocate(
            session_id=session_id,
            scope=scope,
            family=family,
            original=original,
            pad=pad,
            numbering=numbering,
            base_sha256=base_sha256,
        )
        edits.append(
            (
                span.start,
                span.end,
                placeholder,
                span.kind,
                reason_for_span(span),
                placeholder,
            )
        )
        entries.append(
            MapEntry(
                scope=scope,
                placeholder=placeholder,
                kind=family,
                original=original,
                base_sha256=base_sha256,
            )
        )

    if not edits:
        return MaskResult(sanitized=raw, flags=flags)

    edits.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    parts: list[str] = []
    changes: list[Change] = []
    cursor_raw = 0
    cursor_san = 0
    for start, end, replacement, kind, reason, _placeholder in edits:
        if start < cursor_raw:
            continue
        prefix = raw[cursor_raw:start]
        parts.append(prefix)
        cursor_san += len(prefix)
        parts.append(replacement)
        changes.append(
            Change(
                raw_start=start,
                raw_end=end,
                san_start=cursor_san,
                san_end=cursor_san + len(replacement),
                kind=kind,
                reason_code=reason,
                placeholder=replacement,
            )
        )
        cursor_san += len(replacement)
        cursor_raw = end
    parts.append(raw[cursor_raw:])

    deduped: dict[tuple[str, str], MapEntry] = {(entry.scope, entry.placeholder): entry for entry in entries}
    return MaskResult(
        sanitized="".join(parts),
        changes=changes,
        map_entries=list(deduped.values()),
        flags=flags,
    )


def sha256_text(text: str) -> str:
    """Return the SHA-256 hex digest of text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
