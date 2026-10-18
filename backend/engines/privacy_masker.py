"""Masking entry point.

Dispatches to the code, text or table masker. Masked output is for
comprehension, not execution.
"""

from __future__ import annotations

from backend.core.models import ContentKind, MaskPlan, MaskResult, Span
from backend.engines.code_masker import mask_python
from backend.engines.text_masker import MaskMapStore, mask_text


def mask(
    content: str,
    *,
    kind: ContentKind,
    scope: str,
    session_id: str,
    detections: list[Span],
    plan: MaskPlan,
    store: MaskMapStore,
    base_sha256: str | None = None,
    min_body_lines: int = 3,
) -> MaskResult:
    """Mask text, Python source or command output."""
    if kind == "python":
        return mask_python(
            content,
            spans=plan.spans,
            mask_logic=plan.mask_logic,
            session_id=session_id,
            scope=scope,
            store=store,
            min_body_lines=min_body_lines,
            base_sha256=base_sha256,
            release_pii=plan.release_pii,
        )
    if plan.mask_logic:
        # Block masking was requested for a format we cannot parse safely.
        return MaskResult(sanitized=content, flags=["UNMASKABLE_FORMAT"])
    return mask_text(
        content,
        spans=plan.spans,
        session_id=session_id,
        scope=scope,
        store=store,
        base_sha256=base_sha256,
        release_pii=plan.release_pii,
    )
