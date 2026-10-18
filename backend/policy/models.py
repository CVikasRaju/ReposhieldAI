"""Pydantic models for the .aishield policy file.

Explicit values override profile defaults. Unknown keys are rejected so a typo
in a security policy cannot silently disable a control.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Decision = Literal["ALLOW", "MASK", "HOLD", "BLOCK"]
RuleDecision = Decision

MODE_THRESHOLDS: dict[str, dict[str, float]] = {
    "strict": {"mask": 0.30, "hold": 0.55, "block": 0.80},
    "balanced": {"mask": 0.40, "hold": 0.65, "block": 0.85},
    "permissive": {"mask": 0.50, "hold": 0.75, "block": 0.90},
}


def _upper_decision(value: object) -> object:
    if isinstance(value, str):
        return value.upper()
    return value


class StrictModel(BaseModel):
    """Base model that rejects unknown keys."""

    model_config = ConfigDict(extra="forbid")


class ProtectRule(StrictModel):
    """A hard rule for a path glob or a table name."""

    kind: Literal["path", "table"]
    match: str
    read: RuleDecision = "ALLOW"
    write: RuleDecision = "ALLOW"
    delete: RuleDecision = "ALLOW"
    columns: list[str] = Field(default_factory=list)

    @field_validator("read", "write", "delete", mode="before")
    @classmethod
    def _normalise_decision(cls, value: object) -> object:
        return _upper_decision(value)

    def decision_for(self, op: str) -> Decision:
        """Return this rule's decision for an operation."""
        return {"read": self.read, "write": self.write, "delete": self.delete}.get(
            op, "ALLOW"
        )


class TaskConfig(StrictModel):
    """The task declared for the agent run."""

    description: str = ""
    scope_paths: list[str] = Field(default_factory=list)


class PathsConfig(StrictModel):
    """Workspace-relative paths used by the connectors."""

    workspace: str = "./workspace"
    database: str = "./workspace/business.db"


class Hint(StrictModel):
    """A location hint that raises the location signal."""

    match: str
    weight: float = Field(ge=0.0, le=1.0)


class BenignContext(StrictModel):
    """Paths and values known to be safe examples."""

    paths: list[str] = Field(default_factory=list)
    known_test_values: list[str] = Field(default_factory=list)
    example_email_domains: list[str] = Field(default_factory=list)


class CommandPolicy(StrictModel):
    """Command allow, destructive and deny lists."""

    allow: list[str] = Field(default_factory=list)
    destructive: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    default: RuleDecision = "HOLD"
    timeout_seconds: int = Field(default=30, ge=1)
    max_output_bytes: int = Field(default=100_000, ge=1)

    @field_validator("default", mode="before")
    @classmethod
    def _normalise_default(cls, value: object) -> object:
        return _upper_decision(value)


class EgressPolicy(StrictModel):
    """Outbound HTTP policy."""

    default: Literal["allow", "deny"] = "deny"
    allow_hosts: list[str] = Field(default_factory=list)
    allow_private_ranges: bool = False
    hold_methods: list[str] = Field(default_factory=lambda: ["POST", "PUT", "PATCH", "DELETE"])
    timeout_seconds: int = Field(default=10, ge=1)

    @field_validator("hold_methods", mode="before")
    @classmethod
    def _normalise_methods(cls, value: object) -> object:
        if isinstance(value, list):
            return [str(item).upper() for item in value]
        return value


class DatabasePolicy(StrictModel):
    """Database connector limits."""

    max_rows: int = Field(default=200, ge=1)


class MaskingPolicy(StrictModel):
    """Masking behaviour flags."""

    code: str = "structure-preserving"
    tables: str = "typed-placeholders"
    restore_on_write: bool = True
    min_body_lines: int = Field(default=3, ge=1)


class ScoringWeights(StrictModel):
    """Weights for the combined risk score."""

    base: float = Field(default=0.45, ge=0.0, le=1.0)
    content: float = Field(default=0.30, ge=0.0, le=1.0)
    behaviour: float = Field(default=0.25, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> ScoringWeights:
        total = self.base + self.content + self.behaviour
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"scoring.weights must sum to 1.0 (got {total:.4f})")
        return self


class Thresholds(StrictModel):
    """Score thresholds for MASK, HOLD and BLOCK."""

    mask: float = Field(ge=0.0, le=1.0)
    hold: float = Field(ge=0.0, le=1.0)
    block: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> Thresholds:
        if not (self.mask < self.hold < self.block):
            raise ValueError("scoring.thresholds must satisfy mask < hold < block")
        return self


class DetectorConfig(StrictModel):
    """Confidence bands for detectors."""

    auto_mask_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    ignore_below: float = Field(default=0.40, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> DetectorConfig:
        if self.ignore_below >= self.auto_mask_confidence:
            raise ValueError("ignore_below must be lower than auto_mask_confidence")
        return self


class ScoringConfig(StrictModel):
    """Weights, thresholds and detector bands."""

    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    thresholds: Thresholds | None = None
    detectors: DetectorConfig = Field(default_factory=DetectorConfig)


class BehaviourWeights(StrictModel):
    """Weights for the behaviour signal."""

    blocked: float = Field(default=0.35, ge=0.0, le=1.0)
    sensitive_reads: float = Field(default=0.25, ge=0.0, le=1.0)
    breadth: float = Field(default=0.20, ge=0.0, le=1.0)
    mismatch: float = Field(default=0.20, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> BehaviourWeights:
        total = self.blocked + self.sensitive_reads + self.breadth + self.mismatch
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"behaviour.weights must sum to 1.0 (got {total:.4f})")
        return self


class BehaviourConfig(StrictModel):
    """Sliding-window behaviour settings."""

    window_seconds: int = Field(default=60, ge=1)
    weights: BehaviourWeights = Field(default_factory=BehaviourWeights)


class KillSwitchConfig(StrictModel):
    """When the session is locked."""

    max_blocked_attempts: int = Field(default=5, ge=1)
    window_seconds: int = Field(default=60, ge=1)
    behaviour_threshold: float = Field(default=0.90, ge=0.0, le=1.0)


class ApprovalConfig(StrictModel):
    """Human approval channels and timeout."""

    channels: list[Literal["console", "web"]] = Field(default_factory=lambda: ["console", "web"])
    timeout_seconds: int = Field(default=60, ge=1)
    on_timeout: Literal["deny"] = "deny"


class LimitsConfig(StrictModel):
    """Read and listing limits."""

    max_read_bytes: int = Field(default=200_000, ge=1)
    hide_protected_in_listing: bool = True


class Policy(StrictModel):
    """A fully validated, profile-merged policy."""

    version: Literal[1] = 1
    profile: str = "developer"
    mode: Literal["strict", "balanced", "permissive"] = "balanced"
    task: TaskConfig = Field(default_factory=TaskConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    protect: list[ProtectRule] = Field(default_factory=list)
    hints: list[Hint] = Field(default_factory=list)
    benign_context: BenignContext = Field(default_factory=BenignContext)
    commands: CommandPolicy = Field(default_factory=CommandPolicy)
    egress: EgressPolicy = Field(default_factory=EgressPolicy)
    database: DatabasePolicy = Field(default_factory=DatabasePolicy)
    masking: MaskingPolicy = Field(default_factory=MaskingPolicy)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    behaviour: BehaviourConfig = Field(default_factory=BehaviourConfig)
    kill_switch: KillSwitchConfig = Field(default_factory=KillSwitchConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)

    def thresholds(self) -> Thresholds:
        """Return explicit thresholds or the preset for the active mode."""
        if self.scoring.thresholds is not None:
            return self.scoring.thresholds
        return Thresholds(**MODE_THRESHOLDS[self.mode])

    def protect_rule_for_path(self, rel_path: str, op: str) -> ProtectRule | None:
        """Return the first path rule matching rel_path for op, if any."""
        from backend.policy.matcher import path_matches

        for rule in self.protect:
            if rule.kind != "path":
                continue
            if path_matches(rule.match, rel_path) and rule.decision_for(op) != "ALLOW":
                return rule
        return None

    def protect_rule_for_table(
        self, name: str, op: str, columns: list[str] | None = None
    ) -> ProtectRule | None:
        """Return the table rule for a table, including business column defaults."""
        from backend.policy.matcher import path_matches

        for rule in self.protect:
            if rule.kind != "table":
                continue
            if path_matches(rule.match, name) and rule.decision_for(op) != "ALLOW":
                return rule
        if self.profile == "business" and columns:
            sensitive = {"email", "phone", "ssn", "salary", "account", "national_id"}
            if any(any(token in col.lower() for token in sensitive) for col in columns):
                return ProtectRule(
                    kind="table",
                    match=name,
                    read="HOLD",
                    write="HOLD",
                    delete="BLOCK",
                    columns=[col for col in columns if any(t in col.lower() for t in sensitive)],
                )
        return None

    def protect_rule_for_table_name(self, name: str) -> ProtectRule | None:
        """Return the matching table rule regardless of operation."""
        from backend.policy.matcher import path_matches

        for rule in self.protect:
            if rule.kind == "table" and path_matches(rule.match, name):
                return rule
        return None

    def protected_columns(self, table: str) -> list[str]:
        """Return the configured protected columns for a table."""
        rule = self.protect_rule_for_table_name(table)
        return list(rule.columns) if rule else []

    def hint_weight(self, rel_path: str) -> float:
        """Return the highest matching location hint weight."""
        from backend.policy.matcher import path_matches

        weights = [hint.weight for hint in self.hints if path_matches(hint.match, rel_path)]
        return max(weights, default=0.0)

    def is_benign_path(self, rel_path: str) -> bool:
        """Return True when the path is declared benign example context."""
        from backend.policy.matcher import path_matches

        return any(path_matches(pattern, rel_path) for pattern in self.benign_context.paths)

    def is_test_value(self, value: str) -> bool:
        """Return True for configured known-safe test values."""
        digits = "".join(ch for ch in value if ch.isdigit())
        return value in self.benign_context.known_test_values or (
            bool(digits) and digits in self.benign_context.known_test_values
        )

    def is_example_email(self, value: str) -> bool:
        """Return True for a configured example email domain."""
        lowered = value.lower()
        return any(lowered.endswith("@" + domain) or lowered.endswith("." + domain)
                   for domain in self.benign_context.example_email_domains)

    def command_verdict(self, normalised_command: str) -> CommandMatch:
        """Classify a normalised command against the ordered lists."""
        import fnmatch

        for pattern in self.commands.deny:
            if fnmatch.fnmatchcase(normalised_command, pattern):
                return CommandMatch("deny", "DENIED_COMMAND", "BLOCK", 5)
        for pattern in self.commands.destructive:
            if fnmatch.fnmatchcase(normalised_command, pattern):
                return CommandMatch("destructive", "DESTRUCTIVE_COMMAND", "HOLD", 3)
        for pattern in self.commands.allow:
            if fnmatch.fnmatchcase(normalised_command, pattern):
                return CommandMatch("allow", "ALLOWED", "ALLOW", 0)
        return CommandMatch("default", "UNLISTED_COMMAND", self.commands.default, 3)

    def egress_allows(self, host: str) -> bool:
        """Return True when a host is on the egress allowlist."""
        host = host.lower().rstrip(".")
        for pattern in self.egress.allow_hosts:
            candidate = pattern.lower().rstrip(".")
            if candidate.startswith("*."):
                suffix = candidate[1:]
                if host.endswith(suffix) and host != suffix.lstrip("."):
                    return True
            elif host == candidate:
                return True
        return False

    def is_policy_tamper_target(self, rel_path: str) -> bool:
        """Return True when any path component names policy or audit data."""
        parts = [part for part in rel_path.replace("\\", "/").split("/") if part]
        return any(part in {".aishield", "data"} for part in parts)

    def default_path_decision(self, op: str) -> Decision:
        """Return the profile default when no path rule matches."""
        if self.profile == "student" and op == "delete":
            return "HOLD"
        return "ALLOW"


@dataclass(frozen=True)
class CommandMatch:
    """The policy verdict for one command."""

    kind: str
    reason_code: str
    decision: Decision
    tier: int
