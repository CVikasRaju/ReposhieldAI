"""Masking and restore-on-write tests.

Every delta invariant is checked by applying changes to the raw text.
"""

from __future__ import annotations

import sqlite3

import pytest

from backend.core.models import Change, MaskPlan, Span
from backend.engines.code_masker import mask_python, sha256_text
from backend.engines.detectors import DetectContext, detect
from backend.engines.privacy_masker import mask
from backend.engines.restore import restore_file, restore_sql
from backend.engines.table_masker import mask_table
from backend.engines.text_masker import MaskMapStore, mask_text
from scripts.seed_workspace import FILES


def store() -> MaskMapStore:
    return MaskMapStore(sqlite3.connect(":memory:"))


def ctx(target: str = "config/settings.py") -> DetectContext:
    return DetectContext(target=target)


def apply_changes(raw: str, changes: list[Change]) -> str:
    parts: list[str] = []
    cursor = 0
    for change in changes:
        parts.append(raw[cursor : change.raw_start])
        parts.append(change.placeholder)
        cursor = change.raw_end
    parts.append(raw[cursor:])
    return "".join(parts)


def test_text_round_trip_and_offsets() -> None:
    raw = (
        'DATABASE_URL=postgres://admin:Sup3rS3cretPass!@db.internal:5432/prod\n'
        'AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE"\n'
        "contact asha@corp.test or call +1-555-010-1234\n"
    )
    spans = detect(raw, ctx())
    mapping = store()
    result = mask_text(raw, spans=spans, session_id="s1", scope="config/settings.py", store=mapping)
    assert result.sanitized != raw
    assert "AKIAIOSFODNN7EXAMPLE" not in result.sanitized
    assert "asha@corp.test" not in result.sanitized
    assert apply_changes(raw, result.changes) == result.sanitized
    restored = restore_file(
        result.sanitized,
        rel_path="config/settings.py",
        session_id="s1",
        store=mapping,
        current_sha256=sha256_text(raw),
    )
    assert restored.content == raw
    assert not restored.requires_hold


def test_same_original_gets_same_placeholder() -> None:
    raw = "asha@corp.test, asha@corp.test, other@corp.test"
    mapping = store()
    result = mask_text(
        raw,
        spans=detect(raw, ctx("db:customers")),
        session_id="s1",
        scope="db:customers",
        store=mapping,
    )
    emails = [placeholder for placeholder in ("[EMAIL_1]", "[EMAIL_2]")]
    assert emails[0] in result.sanitized
    assert emails[1] in result.sanitized
    assert result.sanitized.count("[EMAIL_1]") == 2
    assert result.sanitized.count("[EMAIL_2]") == 1


def test_overlapping_spans_do_not_corrupt_offsets() -> None:
    raw = "url postgres://admin:hunter2hunter2@db.internal:5432/prod email asha@corp.test"
    spans = detect(raw, ctx())
    assert any(span.kind == "CONNECTION_STRING" for span in spans)
    mapping = store()
    result = mask_text(raw, spans=spans, session_id="s1", scope="f.txt", store=mapping)
    assert apply_changes(raw, result.changes) == result.sanitized
    assert "hunter2hunter2" not in result.sanitized


def test_release_pii_keeps_secrets_hidden() -> None:
    raw = "asha@corp.test AKIAIOSFODNN7EXAMPLE"
    result = mask_text(
        raw,
        spans=detect(raw, ctx()),
        session_id="s1",
        scope="f.txt",
        store=store(),
        release_pii=True,
    )
    assert "asha@corp.test" in result.sanitized
    assert "AKIAIOSFODNN7EXAMPLE" not in result.sanitized


CODE = '''"""Module docstring."""
import os

POLICY = "public"


def decorated(value: int) -> int:
    """Docstring for decorated."""
    first = value + 1
    second = first * 2
    third = second - value
    return third


async def fetch(url: str) -> str:
    """Fetch a URL."""
    async with session() as client:
        response = await client.get(url)
        text = await response.text()
        return text


class Thing:
    """A class."""

    def method(self, value: int) -> int:
        """Method docstring."""
        doubled = value * 2
        tripled = value * 3
        return doubled + tripled


def wrapper() -> None:
    def inner() -> None:
        a = 1
        b = 2
        c = 3
        print(a, b, c)

    inner()
    print("done")
    print("again")


def one_liner(): return 1


class Tabbed:
\tdef method(self):
\t\t"""Tab indented."""
\t\ta = 1
\t\tb = 2
\t\tc = 3
\t\treturn a + b + c
'''


def test_code_masks_bodies_but_keeps_signatures_and_docstrings() -> None:
    mapping = store()
    result = mask_python(
        CODE,
        spans=[],
        mask_logic=True,
        session_id="s1",
        scope="src/auth/login.py",
        store=mapping,
        min_body_lines=3,
        base_sha256=sha256_text(CODE),
    )
    assert not result.flags
    assert "pass  # [MASKED_LOGIC_BLOCK_01]" in result.sanitized
    assert "def decorated" in result.sanitized
    assert "Docstring for decorated." in result.sanitized
    assert "return third" not in result.sanitized
    assert "async def fetch" in result.sanitized
    assert "class Thing" in result.sanitized
    assert "def one_liner(): return 1" in result.sanitized
    assert "\t\tpass  # [MASKED_LOGIC_BLOCK_" in result.sanitized
    assert apply_changes(CODE, result.changes) == result.sanitized

    restored = restore_file(
        result.sanitized,
        rel_path="src/auth/login.py",
        session_id="s1",
        store=mapping,
        current_sha256=sha256_text(CODE),
    )
    assert restored.content == CODE


def test_code_masking_skips_nested_functions_inside_a_masked_block() -> None:
    mapping = store()
    result = mask_python(
        CODE,
        spans=[],
        mask_logic=True,
        session_id="s1",
        scope="f.py",
        store=mapping,
        min_body_lines=3,
    )
    assert "def inner" not in result.sanitized
    assert "print(a, b, c)" not in result.sanitized


def test_code_masker_reports_syntax_errors() -> None:
    result = mask_python(
        "def broken(:\n    pass\n",
        spans=[],
        mask_logic=True,
        session_id="s1",
        scope="broken.py",
        store=store(),
    )
    assert result.flags == ["UNMASKABLE_FORMAT"]
    assert result.sanitized == "def broken(:\n    pass\n"


def test_removed_marker_requires_hold() -> None:
    mapping = store()
    result = mask_python(
        CODE,
        spans=[],
        mask_logic=True,
        session_id="s1",
        scope="f.py",
        store=mapping,
        min_body_lines=3,
        base_sha256=sha256_text(CODE),
    )
    marker_lines = [line for line in result.sanitized.splitlines() if "MASKED_LOGIC_BLOCK_01" in line]
    tampered = result.sanitized.replace(marker_lines[0] + "\n", "")
    restored = restore_file(
        tampered,
        rel_path="f.py",
        session_id="s1",
        store=mapping,
        current_sha256=sha256_text(CODE),
    )
    assert "MASKED_BLOCK_REMOVED" in restored.flags
    assert restored.requires_hold


def test_stale_base_requires_hold() -> None:
    mapping = store()
    result = mask_python(
        CODE,
        spans=[],
        mask_logic=True,
        session_id="s1",
        scope="f.py",
        store=mapping,
        min_body_lines=3,
        base_sha256=sha256_text(CODE),
    )
    restored = restore_file(
        result.sanitized,
        rel_path="f.py",
        session_id="s1",
        store=mapping,
        current_sha256=sha256_text(CODE + "# changed\n"),
    )
    assert "STALE_MASKED_BASE" in restored.flags
    assert restored.requires_hold


def test_indentation_change_is_reapplied() -> None:
    mapping = store()
    result = mask_python(
        CODE,
        spans=[],
        mask_logic=True,
        session_id="s1",
        scope="f.py",
        store=mapping,
        min_body_lines=3,
        base_sha256=sha256_text(CODE),
    )
    marker = "    pass  # [MASKED_LOGIC_BLOCK_01]"
    assert marker in result.sanitized
    indented = result.sanitized.replace(marker, "        pass  # [MASKED_LOGIC_BLOCK_01]")
    restored = restore_file(
        indented,
        rel_path="f.py",
        session_id="s1",
        store=mapping,
        current_sha256=sha256_text(CODE),
    )
    assert restored.content == CODE.replace(
        "    first = value + 1\n    second = first * 2\n    third = second - value\n    return third\n",
        "        first = value + 1\n        second = first * 2\n        third = second - value\n        return third\n",
    )


def test_unknown_placeholder_is_flagged_but_left_alone() -> None:
    restored = restore_file(
        "value [EMAIL_99] here",
        rel_path="f.py",
        session_id="s1",
        store=store(),
    )
    assert "unknown_placeholder" in restored.flags
    assert "[EMAIL_99]" in restored.content


@pytest.mark.parametrize(
    "raw",
    [
        "plain text with no findings",
        "asha@corp.test and meera@corp.test",
        "AKIAIOSFODNN7EXAMPLE",
        "postgres://admin:Sup3rS3cretPass!@db.internal:5432/prod",
        "card 4111 1111 1111 1111 and ssn 123-45-6789",
        "line one\nline two asha@corp.test\nline three",
        "email inside url postgres://x:y@host/db?to=asha@corp.test",
        "" ,
    ],
)
def test_delta_offsets_over_many_inputs(raw: str) -> None:
    mapping = store()
    result = mask_text(
        raw,
        spans=detect(raw, ctx("src/app.py")),
        session_id="s1",
        scope="src/app.py",
        store=mapping,
    )
    assert apply_changes(raw, result.changes) == result.sanitized


@pytest.mark.parametrize(("rel_path", "content"), list(FILES.items()))
@pytest.mark.parametrize("use_code", [False, True])
def test_round_trip_every_seed_file(rel_path: str, content: str, use_code: bool) -> None:
    if use_code and not rel_path.endswith(".py"):
        return
    mapping = store()
    spans = detect(content, ctx(rel_path))
    if use_code:
        result = mask_python(
            content,
            spans=spans,
            mask_logic=True,
            session_id="s1",
            scope=rel_path,
            store=mapping,
            min_body_lines=3,
            base_sha256=sha256_text(content),
        )
    else:
        result = mask_text(
            content,
            spans=spans,
            session_id="s1",
            scope=rel_path,
            store=mapping,
            base_sha256=sha256_text(content),
        )
    restored = restore_file(
        result.sanitized,
        rel_path=rel_path,
        session_id="s1",
        store=mapping,
        current_sha256=sha256_text(content),
    )
    assert restored.content == content, rel_path


def test_privacy_masker_dispatch() -> None:
    plan = MaskPlan(spans=detect("token AKIAIOSFODNN7EXAMPLE", ctx()), mask_logic=False)
    result = mask(
        "token AKIAIOSFODNN7EXAMPLE",
        kind="text",
        scope="f.txt",
        session_id="s1",
        detections=[],
        plan=plan,
        store=store(),
    )
    assert "AKIAIOSFODNN7EXAMPLE" not in result.sanitized
    unsupported = mask(
        "not python",
        kind="text",
        scope="f.txt",
        session_id="s1",
        detections=[],
        plan=MaskPlan(mask_logic=True),
        store=store(),
    )
    assert unsupported.flags == ["UNMASKABLE_FORMAT"]


def table_result():
    columns = ["id", "full_name", "email", "phone", "national_id", "notes"]
    rows = [
        [1, "Asha Rao", "asha@corp.test", "+1-555-010-1234", "1234 5678 9012", "Prefers email."],
        [2, "Rohan Iyer", "asha@corp.test", "+1-555-010-5678", "1234 5678 9012", "Card on file 4111 1111 1111 1111"],
        [3, "Meera Bose", "meera@corp.test", "+1-555-010-9012", None, None],
    ]
    return columns, rows


def test_table_masking_placeholders_and_offsets() -> None:
    columns, rows = table_result()
    result = mask_table(
        table="customers",
        columns=columns,
        rows=rows,
        protected_columns=["full_name", "email", "phone", "national_id"],
        session_id="s1",
        store=store(),
        plan=MaskPlan(),
        detect_cell=lambda text: detect(text, ctx("db:customers")),
    )
    assert result.rows[0][1] == "[NAME_1]"
    assert result.rows[0][2] == "[EMAIL_1]"
    assert result.rows[0][3] == "[PHONE_1]"
    assert result.rows[0][4] == "[NATIONAL_ID_1]"
    assert result.rows[1][2] == "[EMAIL_1]"
    assert result.rows[2][2] == "[EMAIL_2]"
    assert result.rows[1][5] == "Card on file [MASKED_CARD_1]"
    assert result.rows[2][4] is None
    assert result.rows[0][0] == 1
    assert apply_changes(result.raw_render, result.changes) == result.sanitized
    assert "asha@corp.test" in result.raw_render
    assert "asha@corp.test" not in result.sanitized
    assert "4111 1111 1111 1111" not in result.sanitized


def test_table_masking_release_pii() -> None:
    columns, rows = table_result()
    result = mask_table(
        table="customers",
        columns=columns,
        rows=rows,
        protected_columns=["full_name", "email", "phone", "national_id"],
        session_id="s1",
        store=store(),
        plan=MaskPlan(release_pii=True),
        detect_cell=lambda text: detect(text, ctx("db:customers")),
    )
    assert result.rows[0][1] == "Asha Rao"
    assert result.rows[0][2] == "asha@corp.test"
    assert result.rows[1][5] == "Card on file 4111 1111 1111 1111"


def test_table_typed_placeholder_flag() -> None:
    result = mask_table(
        table="customers",
        columns=["id", "account"],
        rows=[[1, 987654321]],
        protected_columns=["account"],
        session_id="s1",
        store=store(),
        plan=MaskPlan(),
    )
    assert result.rows[0][1] == "[ACCOUNT_1]"
    assert "typed_placeholders" in result.flags


def test_sql_literal_restore() -> None:
    mapping = store()
    raw = "asha@corp.test"
    masked = mask_text(
        raw,
        spans=[Span(kind="EMAIL", category="pii", start=0, end=len(raw), confidence=0.5, preview="a***@****")],
        session_id="s1",
        scope="db:customers",
        store=mapping,
    )
    sql = f"SELECT * FROM customers WHERE email = '{masked.sanitized}'"
    restored = restore_sql(sql, table="customers", session_id="s1", store=mapping)
    assert "asha@corp.test" in restored.content
    unknown = restore_sql("SELECT 1", table="customers", session_id="s1", store=mapping)
    assert unknown.content == "SELECT 1"
