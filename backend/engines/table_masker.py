"""Mask database result sets by protected column and by detector span."""

from __future__ import annotations

from collections.abc import Callable

from backend.core.models import Change, MapEntry, MaskPlan, Span, TableMaskResult
from backend.engines.text_masker import MaskMapStore, column_placeholder_family, mask_text


def _cell_text(value: object) -> str:
    return "" if value is None else str(value)


def mask_table(
    *,
    table: str,
    columns: list[str],
    rows: list[list[object]],
    protected_columns: list[str],
    session_id: str,
    store: MaskMapStore,
    plan: MaskPlan,
    detect_cell: Callable[[str], list[Span]] | None = None,
    truncated: bool = False,
) -> TableMaskResult:
    """Mask protected columns and detected spans in a result set.

    Placeholders are consistent for the session, so the same email in two rows
    receives the same token. The rendered raw and sanitized text share exact
    offsets, which is what the delta view uses.
    """
    protected = {name.lower() for name in protected_columns}
    scope = f"db:{table}"
    raw_render = ",".join(columns) + "\n"
    san_render = ",".join(columns) + "\n"
    changes: list[Change] = []
    entries: dict[tuple[str, str], MapEntry] = {}
    masked_rows: list[list[object]] = []
    typed_placeholders = False

    def append_cell(raw_cell: str, san_cell: str, local: list[Change]) -> None:
        nonlocal raw_render, san_render
        raw_start = len(raw_render)
        san_start = len(san_render)
        for change in local:
            changes.append(
                change.model_copy(
                    update={
                        "raw_start": raw_start + change.raw_start,
                        "raw_end": raw_start + change.raw_end,
                        "san_start": san_start + change.san_start,
                        "san_end": san_start + change.san_end,
                    }
                )
            )
        raw_render += raw_cell
        san_render += san_cell

    for row in rows:
        masked_row: list[object] = []
        for index, column in enumerate(columns):
            value = row[index] if index < len(row) else None
            raw_cell = _cell_text(value)
            is_protected = column.lower() in protected and not plan.release_pii
            if value is None:
                masked_row.append(None)
                append_cell("", "", [])
            elif is_protected:
                family = column_placeholder_family(column)
                placeholder = store.allocate(
                    session_id=session_id,
                    scope=scope,
                    family=family,
                    original=raw_cell,
                    numbering="session",
                )
                if not isinstance(value, str):
                    typed_placeholders = True
                change = Change(
                    raw_start=0,
                    raw_end=len(raw_cell),
                    san_start=0,
                    san_end=len(placeholder),
                    kind=family,
                    reason_code="PROTECTED_TABLE",
                    placeholder=placeholder,
                )
                masked_row.append(placeholder)
                append_cell(raw_cell, placeholder, [change])
                entries[(scope, placeholder)] = MapEntry(
                    scope=scope,
                    placeholder=placeholder,
                    kind=family,
                    original=raw_cell,
                )
            elif isinstance(value, str) and detect_cell is not None:
                spans = detect_cell(value)
                result = mask_text(
                    value,
                    spans=spans,
                    session_id=session_id,
                    scope=scope,
                    store=store,
                    release_pii=plan.release_pii,
                )
                masked_row.append(result.sanitized)
                append_cell(raw_cell, result.sanitized, result.changes)
                for entry in result.map_entries:
                    entries[(entry.scope, entry.placeholder)] = entry
            else:
                masked_row.append(value)
                append_cell(raw_cell, raw_cell, [])
            if index < len(columns) - 1:
                raw_render += ","
                san_render += ","
        raw_render += "\n"
        san_render += "\n"
        masked_rows.append(masked_row)

    flags = ["typed_placeholders"] if typed_placeholders else []
    return TableMaskResult(
        sanitized=san_render,
        raw_render=raw_render,
        changes=changes,
        map_entries=list(entries.values()),
        flags=flags,
        columns=columns,
        rows=masked_rows,
        truncated=truncated,
    )
