# 06. Masking and Restore-on-Write

Masked output is **for comprehension, not execution**. Never claim it compiles or runs.

## 1. Entry point

`engines/privacy_masker.py`:

```python
def mask(content: str, *, kind: ContentKind, scope: str, session_id: str,
         detections: list[Span], plan: MaskPlan) -> MaskResult: ...
# MaskResult: sanitized: str, changes: list[Change], map_entries: list[MapEntry], flags: list[str]
```

`ContentKind`: `python`, `text` (any other text file), `table` (database result), `command_output`.

`MaskPlan` says what to mask:

| Field | Meaning |
|---|---|
| `spans` | Detector spans to replace. At minimum all auto-mask spans. When the decision is MASK, also medium spans. When approval was `approve_masked`, all spans at or above `ignore_below`. |
| `mask_logic` | True when the decision came from location or content signals and the file is Python. Triggers function-body masking. |
| `protected_columns` | For tables. |
| `release_pii` | True when a human chose `approve`. PII and financial spans are skipped. Secrets are never skipped. |

If `mask_logic` is needed and the content is not Python, or Python fails to parse, return flag `UNMASKABLE_FORMAT` and the pipeline escalates to HOLD.

## 2. Placeholder formats

| Kind | Format | Numbering |
|---|---|---|
| Secret | `[MASKED_SECRET_01]` | Per scope, in order of appearance |
| Python logic block | `pass  # [MASKED_LOGIC_BLOCK_01]` | Per file, in order |
| Name, email, phone, national ID, account | `[NAME_1]`, `[EMAIL_1]`, `[PHONE_1]`, `[NATIONAL_ID_1]`, `[ACCOUNT_1]` | Per session and kind |
| Other protected column | `[MASKED_<COLUMN>_1]` | Per session and column |
| Card, SSN, other PII or financial | `[MASKED_CARD_1]`, `[MASKED_SSN_1]`, `[MASKED_PII_1]` | Per session and kind |

**Consistency:** the same original value in the same session and kind always gets the same placeholder, so the agent can still see that two rows share an email.

## 3. Text masker (`text_masker.py`)

1. Take the spans to mask. Sort by `start`.
2. **Merge overlaps:** keep the span with higher confidence, then the longer one.
3. Build the output left to right. For each span, emit the preceding text, then the placeholder. Track `raw_start/raw_end` and `san_start/san_end` as you go, so offsets are correct without a second pass.
4. Record a `MapEntry` for each distinct placeholder.

Test: applying `changes` to `raw` reproduces `sanitized`.

## 4. Python code masker (`code_masker.py`)

Uses the stdlib `ast` module only to **locate** line ranges. It does not regenerate source, because `ast.unparse` would drop comments and formatting.

```python
def find_blocks(source: str, min_body_lines: int) -> list[Block]:
    tree = ast.parse(source)                       # SyntaxError -> UNMASKABLE_FORMAT
    lines = source.splitlines(keepends=True)
    blocks = []

    def visit(node, inside_masked: bool):
        for child in ast.iter_child_nodes(node):
            is_func = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            if is_func and not inside_masked:
                body = child.body
                start_idx = 1 if _is_docstring(body[0]) else 0
                stmts = body[start_idx:]
                if stmts:
                    first = stmts[0].lineno
                    last = child.end_lineno
                    if first > child.lineno and (last - first + 1) >= min_body_lines:
                        indent = _leading_ws(lines[first - 1])
                        blocks.append(Block(first, last, indent, "".join(lines[first-1:last])))
                        continue          # do not descend: nested functions are inside this block
            visit(child, inside_masked)

    visit(tree, False)
    return blocks
```

`stmts[0].lineno` is the first statement, so comments before it stay visible. Decorated functions work because the body range is independent of decorators.

Replacement: for each block (processed bottom-up so line numbers stay valid), replace lines `first..last` with one line:

```
{indent}pass  # [MASKED_LOGIC_BLOCK_NN]
```

Keep: signatures, decorators, docstrings, class definitions, imports, module-level names. Mask: bodies of functions and methods with at least `min_body_lines` lines of body after the docstring. One-line functions (`def f(): return x`) are skipped.

Secret spans inside code (API keys in assignments) are replaced by the text masker **after** block masking, on the remaining text, with `[MASKED_SECRET_NN]`.

For every block, store a `mask_map` row:
`scope=<relpath>`, `placeholder=[MASKED_LOGIC_BLOCK_NN]`, `original=<verbatim lines>`, `original_indent=<indent>`, `base_sha256=<sha256 of the file as read>`.

## 5. Table masker (`table_masker.py`)

Input: `columns: list[str]`, `rows: list[list[Any]]`, `protected_columns`, `plan`.

1. For each protected column (match by result column name, case-insensitive), replace every non-null value with its typed placeholder. Numeric values in protected columns also become placeholder strings. Note the type change in the tool notice.
2. For every other string cell, run detectors and mask spans per the plan (auto-mask spans, plus medium spans when forced).
3. `NULL` stays `NULL`.
4. Return masked rows, `changes` (one per masked cell, with `raw` and `sanitized` cell text, for the delta view), and map entries with `scope="db:<table>"`.

For the delta view, render the raw and masked result as pretty-printed JSON or CSV text and compute offsets on that rendering.

## 6. Restore-on-write (`restore.py`)

Runs inside `write_file` before the real write, and inside `db_query` for string literals.

### Files

Inputs: relative path, proposed `content`, session id.

1. Load `mask_map` entries for `scope=<relpath>` and `scope="global"` from the most recent read in this session.
2. **Stale check.** If a logic-block entry exists and the file's current SHA-256 differs from `base_sha256`, flag `STALE_MASKED_BASE` and HOLD. A human decides.
3. **Logic blocks.** Find marker lines with the regex `^(?P<indent>[ \t]*)pass  # \[MASKED_LOGIC_BLOCK_(?P<n>\d+)\][ \t]*$`.
   - Each found marker is replaced by the original lines. If the marker's indent differs from `original_indent`, re-indent: strip `original_indent` from the start of each original line and prepend the new indent. If any original line does not start with `original_indent`, HOLD with `UNMASKABLE_FORMAT`.
   - Each expected marker that is **missing** from the content means the agent removed or rewrote that block. Do not restore it. Flag `MASKED_BLOCK_REMOVED` and HOLD, showing the diff in the delta view. If a human approves, the write proceeds without restoring that block.
4. **Value placeholders.** Replace every known placeholder token (`[MASKED_SECRET_01]`, `[EMAIL_1]`, and so on) with its original. Replace longest tokens first.
5. **Unknown placeholders.** A token that looks like a placeholder but is not in the map (hallucinated) is left as is. Add flag `unknown_placeholder`.
6. Write the restored content. The delta for the write event shows what the agent sent and what was written, with restored spans highlighted.

### SQL literals

Parse with `sqlglot`, walk `exp.Literal` string nodes, replace any literal equal to a known `db:` placeholder with the original, and re-render. If parsing fails the statement is already BLOCK `UNSAFE_SQL`.

## 7. Safety properties to test

- `restore(mask(x)) == x` for every fixture Python file and text file.
- An agent that edits one function and leaves the markers untouched produces a file where every masked block is byte-identical to the original.
- An agent that deletes a marker triggers HOLD, not silent data loss.
- Changing the file on disk after the masked read triggers `STALE_MASKED_BASE`.
- The same email in two rows gets the same placeholder. Two different emails get different placeholders.
- No original secret appears anywhere in the returned `content`, in `events`, or in logs.
- Overlapping spans (an email inside a connection string) do not corrupt offsets.
- A file with a syntax error and `mask_logic` true returns `UNMASKABLE_FORMAT`.
