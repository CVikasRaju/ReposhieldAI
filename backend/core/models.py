"""Shared pydantic models for the gateway.

These types cross module boundaries: detectors, maskers, the pipeline and the
dashboard API all use them. Phase 4 adds the request, event and approval types.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SpanCategory = Literal["secret", "pii", "financial"]
ContentKind = Literal["python", "text", "table", "command_output"]


class Span(BaseModel):
    """One detected sensitive range in a piece of content."""

    model_config = ConfigDict(frozen=True)

    kind: str
    category: SpanCategory
    start: int
    end: int
    confidence: float
    preview: str


class Change(BaseModel):
    """One replacement, expressed in both raw and sanitized coordinates."""

    model_config = ConfigDict(extra="ignore")

    raw_start: int
    raw_end: int
    san_start: int
    san_end: int
    kind: str
    reason_code: str
    placeholder: str


class MapEntry(BaseModel):
    """A placeholder-to-original mapping to persist for restore-on-write."""

    scope: str
    placeholder: str
    kind: str
    original: str
    original_indent: str | None = None
    base_sha256: str | None = None


class MaskPlan(BaseModel):
    """What the masker is allowed to hide for this action."""

    spans: list[Span] = Field(default_factory=list)
    mask_logic: bool = False
    protected_columns: list[str] = Field(default_factory=list)
    release_pii: bool = False


class MaskResult(BaseModel):
    """The sanitized content plus the delta and persistence records."""

    sanitized: str
    changes: list[Change] = Field(default_factory=list)
    map_entries: list[MapEntry] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class TableMaskResult(MaskResult):
    """Masking output for a database result set."""

    raw_render: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[list[object]] = Field(default_factory=list)
    truncated: bool = False


class RestoreResult(BaseModel):
    """The outcome of restoring placeholders before a write."""

    content: str
    flags: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    requires_hold: bool = False


class Decision(StrEnum):
    """Gateway decisions, ordered from least to most restrictive."""

    ALLOW = "ALLOW"
    MASK = "MASK"
    HOLD = "HOLD"
    BLOCK = "BLOCK"

    @property
    def rank(self) -> int:
        """Numeric rank used by strictest-wins resolution."""
        return _DECISION_RANK[self]


_DECISION_RANK = {
    Decision.ALLOW: 0,
    Decision.MASK: 1,
    Decision.HOLD: 2,
    Decision.BLOCK: 3,
}


def strictest(*decisions: Decision) -> Decision:
    """Return the most restrictive decision."""
    return max(decisions, key=lambda item: item.rank)
