"""Shared pydantic models for the gateway.

These types cross module boundaries: detectors, connectors, the pipeline, the
audit log and the dashboard API all use them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

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


class ConnectorName(StrEnum):
    """The connector that handled an action."""

    FILESYSTEM = "filesystem"
    COMMAND = "command"
    DATABASE = "database"
    NETWORK = "network"
    SYSTEM = "system"


class FinalOutcome(StrEnum):
    """What actually happened after the decision."""

    EXECUTED = "executed"
    EXECUTED_MASKED = "executed_masked"
    BLOCKED = "blocked"
    DENIED = "denied"
    TIMEOUT = "timeout"
    TERMINATED = "terminated"
    ERROR = "error"


class ApprovalKind(StrEnum):
    """How the action was approved."""

    AUTO = "auto"
    APPROVED = "approved"
    APPROVED_MASKED = "approved_masked"
    DENIED = "denied"
    TIMEOUT = "timeout"


class Resolution(StrEnum):
    """A human's answer to a held action."""

    APPROVE = "approve"
    APPROVE_MASKED = "approve_masked"
    DENY = "deny"
    DENY_AND_LOCK = "deny_and_lock"


class SignalScores(BaseModel):
    """The four sensitivity signals and the combined score."""

    pattern: float = 0.0
    location: float = 0.0
    content: float = 0.0
    behaviour: float = 0.0
    combined: float = 0.0


class RuleVerdict(BaseModel):
    """A hard rule's decision and reason codes."""

    decision: Decision = Decision.ALLOW
    reason_codes: list[str] = Field(default_factory=list)


class ActionRequest(BaseModel):
    """One tool call entering the gateway."""

    session_id: str = "default"
    connector: ConnectorName
    action: str
    target: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    task_context: str = ""


class Prepared(BaseModel):
    """A normalised action with its content, base tier and rule verdict."""

    connector: ConnectorName
    action: str
    target: str
    display_target: str = ""
    scope: str = ""
    tier: int = 0
    read_operation: bool = True
    content: str | None = None
    content_kind: ContentKind = "text"
    write_content: str | None = None
    rule: RuleVerdict = Field(default_factory=RuleVerdict)
    meta: dict[str, Any] = Field(default_factory=dict)
    base_sha256: str | None = None
    needs_snapshot: bool = False
    snapshot_kind: Literal["file", "database", "workspace"] | None = None
    snapshot_target: str = ""


class SnapshotRecord(BaseModel):
    """A snapshot created before a reversible action."""

    id: str
    event_id: str | None = None
    session_id: str
    kind: Literal["file", "database", "workspace"]
    target: str
    snapshot_path: str
    existed: bool
    created_at: str
    rolled_back_at: str | None = None


class ExecutionPlan(BaseModel):
    """What a connector may execute after the decision."""

    decision: Decision
    resolution: Resolution | None = None
    mask_plan: MaskPlan = Field(default_factory=MaskPlan)
    restore: RestoreResult | None = None
    snapshot: SnapshotRecord | None = None
    approval: ApprovalKind = ApprovalKind.AUTO
    flags: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class ExecResult(BaseModel):
    """The connector's raw result. The pipeline masks it before returning."""

    ok: bool = True
    content: str | None = None
    content_kind: ContentKind = "text"
    columns: list[str] = Field(default_factory=list)
    rows: list[list[object]] = Field(default_factory=list)
    truncated: bool = False
    raw_delta: str | None = None
    sanitized_delta: str | None = None
    changes: list[Change] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class EventRecord(BaseModel):
    """One completed action in the hash-chained ledger."""

    seq: int | None = None
    id: str
    session_id: str
    ts: str
    connector: ConnectorName
    action: str
    target: str
    task_context: str | None = None
    risk_tier: int
    scores: SignalScores = Field(default_factory=SignalScores)
    decision: Decision
    final_outcome: FinalOutcome
    approval: ApprovalKind = ApprovalKind.AUTO
    reason_codes: list[str] = Field(default_factory=list)
    reason_text: str = ""
    detections: list[dict[str, Any]] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    content_sha256: str | None = None
    delta_sha256: str | None = None
    latency_ms: float | None = None
    prev_hash: str | None = None
    hash: str | None = None


class DeltaRecord(BaseModel):
    """Raw against sanitized content for the dashboard."""

    event_id: str
    raw: str
    sanitized: str
    changes: list[Change] = Field(default_factory=list)


class ApprovalRecord(BaseModel):
    """A held action waiting for a human."""

    id: str
    session_id: str
    connector: ConnectorName
    action: str
    target: str
    summary: str
    tier: int
    reason_codes: list[str] = Field(default_factory=list)
    reason_text: str
    scores: SignalScores = Field(default_factory=SignalScores)
    options: list[Resolution] = Field(default_factory=list)
    status: Literal[
        "pending", "approved", "approved_masked", "denied", "timeout", "cancelled"
    ] = "pending"
    resolved_by: str | None = None
    created_at: str
    expires_at: str
    resolved_at: str | None = None
    event_id: str | None = None


class SessionRecord(BaseModel):
    """One agent run."""

    id: str
    name: str | None = None
    profile: str
    task: str | None = None
    status: Literal["active", "locked", "ended"] = "active"
    locked_reason: str | None = None
    agent_pid: int | None = None
    started_at: str
    ended_at: str | None = None


class ChainVerification(BaseModel):
    """The result of recomputing the audit hash chain."""

    ok: bool
    checked: int = 0
    first_bad_seq: int | None = None
    reason: str | None = None


class ToolResult(BaseModel):
    """The common result shape returned to the agent."""

    status: Literal["ok", "masked", "blocked", "denied", "terminated", "error"]
    event_id: str
    content: str | None = None
    notice: str | None = None


class ActionError(Exception):
    """A non-security failure such as a missing file or invalid SQL."""
