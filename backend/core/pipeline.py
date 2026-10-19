"""The request lifecycle.

Every tool call runs: resolve session, prepare, rules, detect, score, decide,
hold, snapshot, execute, mask, log, respond. Any unexpected exception becomes a
BLOCK with INTERNAL_ERROR and a generic message to the agent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from backend.core.approvals import ApprovalManager
from backend.core.audit import AuditLog, utc_now
from backend.core.decision import TIER_DEFAULT, decide
from backend.core.events import EventBus
from backend.core.explain import explain
from backend.core.models import (
    ActionError,
    ActionRequest,
    ApprovalKind,
    ConnectorName,
    Decision,
    DeltaRecord,
    EventRecord,
    ExecResult,
    ExecutionPlan,
    FinalOutcome,
    MaskPlan,
    Prepared,
    Resolution,
    SessionRecord,
    SignalScores,
    SnapshotRecord,
    Span,
    ToolResult,
)
from backend.core.reasons import ReasonCode
from backend.core.sessions import SessionManager
from backend.core.snapshots import SnapshotManager, SnapshotTooLargeError
from backend.engines.behaviour import BehaviourTracker
from backend.engines.code_masker import find_blocks
from backend.engines.content_model import ContentModel
from backend.engines.detectors import (
    DetectContext,
    detect,
    has_auto_mask,
    max_medium_confidence,
)
from backend.engines.privacy_masker import mask as mask_content
from backend.engines.restore import restore_file, restore_sql
from backend.engines.risk_scorer import RiskScorer
from backend.engines.table_masker import mask_table
from backend.engines.text_masker import MaskMapStore
from backend.policy.matcher import path_matches
from backend.policy.models import Policy

logger = logging.getLogger(__name__)

BLOCKED_MESSAGE = "This action is not permitted by the workspace security policy."
TERMINATED_MESSAGE = "This session has been ended by the workspace security policy."
MASKED_NOTICE = (
    "Some content was replaced with placeholders such as [MASKED_SECRET_01]. "
    "If you write this file back, the original values are restored automatically."
)


def new_event_id() -> str:
    """Return a new event id."""
    return f"evt_{uuid.uuid4().hex[:12]}"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Pipeline:
    """Run one action through policy, scoring, approval, execution and audit."""

    def __init__(
        self,
        *,
        policy: Policy,
        audit: AuditLog,
        sessions: SessionManager,
        approvals: ApprovalManager,
        snapshots: SnapshotManager,
        bus: EventBus,
        connectors: dict[str, Any],
        content_model: ContentModel,
        behaviour: BehaviourTracker,
        store: MaskMapStore,
        workspace_root: Path,
    ) -> None:
        self.policy = policy
        self.audit = audit
        self.sessions = sessions
        self.approvals = approvals
        self.snapshots = snapshots
        self.bus = bus
        self.connectors = connectors
        self.content_model = content_model
        self.behaviour = behaviour
        self.store = store
        self.workspace_root = Path(workspace_root)
        self.scorer = RiskScorer(policy.scoring.weights)
        self.thresholds = policy.thresholds()
        self.auto_confidence = policy.scoring.detectors.auto_mask_confidence
        self.ignore_below = policy.scoring.detectors.ignore_below

    # ------------------------------------------------------------------ public

    async def handle(self, request: ActionRequest) -> ToolResult:
        """Run one action and return the agent-facing result."""
        started = time.perf_counter()
        try:
            session = await self.sessions.get_or_create(
                request.session_id,
                profile=self.policy.profile,
                task=self.policy.task.description or None,
            )
        except Exception:
            logger.error("could not resolve session")
            return ToolResult(
                status="blocked", event_id=new_event_id(), notice=BLOCKED_MESSAGE
            )
        if session.status == "locked":
            return await self._reject_locked(request, started)
        try:
            return await self._handle_active(request, session, started)
        except ActionError as exc:
            return await self._action_error(request, session, started, exc)
        except Exception as exc:  # fail closed
            logger.error("pipeline error: %s", type(exc).__name__)
            return await self._internal_error(request, session, started)

    async def record_system_event(
        self,
        *,
        session_id: str,
        action: str,
        target: str,
        reason_code: str = ReasonCode.ALLOWED.value,
    ) -> EventRecord:
        """Append a system event such as a rollback."""
        session = await self.sessions.get_or_create(
            session_id, profile=self.policy.profile, task=self.policy.task.description or None
        )
        event = EventRecord(
            id=new_event_id(),
            session_id=session.id,
            ts=utc_now(),
            connector=ConnectorName.SYSTEM,
            action=action,
            target=target[:300],
            task_context=self.policy.task.description or None,
            risk_tier=0,
            scores=SignalScores(),
            decision=Decision.ALLOW,
            final_outcome=FinalOutcome.EXECUTED,
            approval=ApprovalKind.AUTO,
            reason_codes=[reason_code],
            reason_text=explain(reason_code, target=target),
            latency_ms=0.0,
        )
        logged = await self.audit.append_event(event)
        await self.bus.publish("event.created", {"event": logged.model_dump(mode="json")})
        return logged

    # ------------------------------------------------------------------ helpers

    def _detect_context(self, prepared: Prepared) -> DetectContext:
        scope = prepared.scope or prepared.target
        return DetectContext(
            target=scope,
            benign_path=self.policy.is_benign_path(scope),
            known_test_values=frozenset(self.policy.benign_context.known_test_values),
            example_email_domains=frozenset(self.policy.benign_context.example_email_domains),
            ignore_below=self.ignore_below,
        )

    def _safe_text(self, text: str) -> str:
        """Redact auto-mask spans from a string that may enter an event."""
        spans = detect(text, DetectContext(ignore_below=self.ignore_below))
        selected = [span for span in spans if span.confidence >= self.auto_confidence]
        if not selected:
            return text
        parts: list[str] = []
        cursor = 0
        for span in selected:
            if span.start < cursor:
                continue
            parts.append(text[cursor : span.start])
            parts.append("[MASKED]")
            cursor = span.end
        parts.append(text[cursor:])
        return "".join(parts)

    def _safe_target(self, prepared: Prepared) -> str:
        target = prepared.target
        return self._safe_text(target)[:300]

    def _redact_spans(self, content: str, spans: list[Span]) -> str:
        if not spans:
            return content
        parts: list[str] = []
        cursor = 0
        for span in sorted(spans, key=lambda item: item.start):
            if span.start < cursor:
                continue
            parts.append(content[cursor : span.start])
            parts.append("[MASKED]")
            cursor = span.end
        parts.append(content[cursor:])
        return "".join(parts)

    def _out_of_scope(self, prepared: Prepared) -> bool:
        scopes = self.policy.task.scope_paths
        if not scopes:
            return False
        if prepared.connector == ConnectorName.DATABASE:
            candidate = f"db:{prepared.target}"
        elif prepared.connector == ConnectorName.NETWORK:
            candidate = f"host:{prepared.target}"
        else:
            candidate = prepared.scope or prepared.target
        return not any(
            path_matches(pattern, candidate) or path_matches(pattern, prepared.target)
            for pattern in scopes
        )

    def _tier_default(self, prepared: Prepared, tier: int) -> Decision:
        override = prepared.meta.get("tier_default")
        if isinstance(override, str):
            try:
                return Decision(override.upper())
            except ValueError:
                pass
        return TIER_DEFAULT.get(tier, Decision.BLOCK)

    def _mask_logic(self, prepared: Prepared, location: float, content_signal: float) -> bool:
        if prepared.content_kind != "python" or not prepared.read_operation:
            return False
        return location >= 0.5 or content_signal >= self.thresholds.mask

    def _primary_reason(
        self,
        *,
        decision: Decision,
        prepared: Prepared,
        detections: list[Span],
        codes: list[str],
        benign: bool,
    ) -> str:
        auto = [span for span in detections if span.confidence >= self.auto_confidence]
        has_secret = any(span.category == "secret" for span in auto)
        has_pii = any(span.category != "secret" for span in auto)
        if decision == Decision.ALLOW:
            return ReasonCode.BENIGN_CONTEXT.value if benign else ReasonCode.ALLOWED.value
        if decision == Decision.MASK:
            if has_secret:
                return ReasonCode.SECRET_DETECTED.value
            if has_pii:
                return ReasonCode.PII_DETECTED.value
            for candidate in (
                ReasonCode.SENSITIVE_LOCATION.value,
                ReasonCode.CONTENT_SENSITIVE.value,
                ReasonCode.MASKED_FOR_PRIVACY.value,
            ):
                if candidate in codes:
                    return candidate
            return ReasonCode.MASKED_FOR_PRIVACY.value
        for code in codes:
            if code not in {ReasonCode.ALLOWED.value, ReasonCode.BENIGN_CONTEXT.value}:
                return code
        return ReasonCode.INTERNAL_ERROR.value if decision == Decision.BLOCK else ReasonCode.MASKED_FOR_PRIVACY.value

    async def _publish_event(self, event: EventRecord) -> None:
        await self.bus.publish("event.created", {"event": event.model_dump(mode="json")})

    async def _reject_locked(self, request: ActionRequest, started: float) -> ToolResult:
        event = EventRecord(
            id=new_event_id(),
            session_id=request.session_id,
            ts=utc_now(),
            connector=request.connector,
            action=request.action,
            target=self._safe_text(request.target)[:300],
            task_context=self.policy.task.description or None,
            risk_tier=5,
            scores=SignalScores(),
            decision=Decision.BLOCK,
            final_outcome=FinalOutcome.TERMINATED,
            approval=ApprovalKind.AUTO,
            reason_codes=[ReasonCode.SESSION_LOCKED.value],
            reason_text=explain(ReasonCode.SESSION_LOCKED.value),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )
        try:
            logged = await self.audit.append_event(event)
            await self._publish_event(logged)
            event_id = logged.id
        except Exception:
            event_id = event.id
        return ToolResult(status="terminated", event_id=event_id, notice=TERMINATED_MESSAGE)

    async def _action_error(
        self,
        request: ActionRequest,
        session: SessionRecord,
        started: float,
        exc: ActionError,
    ) -> ToolResult:
        message = self._safe_text(str(exc))[:200] or "The action could not be completed."
        event = EventRecord(
            id=new_event_id(),
            session_id=session.id,
            ts=utc_now(),
            connector=request.connector,
            action=request.action,
            target=self._safe_text(request.target)[:300],
            task_context=self.policy.task.description or None,
            risk_tier=0,
            scores=SignalScores(),
            decision=Decision.ALLOW,
            final_outcome=FinalOutcome.ERROR,
            approval=ApprovalKind.AUTO,
            reason_codes=[ReasonCode.ACTION_FAILED.value],
            reason_text=explain(ReasonCode.ACTION_FAILED.value),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )
        try:
            logged = await self.audit.append_event(event)
            await self._publish_event(logged)
            event_id = logged.id
        except Exception:
            event_id = event.id
        return ToolResult(status="error", event_id=event_id, notice=message)

    async def _internal_error(
        self, request: ActionRequest, session: SessionRecord, started: float
    ) -> ToolResult:
        event = EventRecord(
            id=new_event_id(),
            session_id=session.id,
            ts=utc_now(),
            connector=request.connector,
            action=request.action,
            target=self._safe_text(request.target)[:300],
            task_context=self.policy.task.description or None,
            risk_tier=5,
            scores=SignalScores(),
            decision=Decision.BLOCK,
            final_outcome=FinalOutcome.BLOCKED,
            approval=ApprovalKind.AUTO,
            reason_codes=[ReasonCode.INTERNAL_ERROR.value],
            reason_text=explain(ReasonCode.INTERNAL_ERROR.value),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )
        try:
            logged = await self.audit.append_event(event)
            await self._publish_event(logged)
            event_id = logged.id
        except Exception:
            event_id = event.id
        return ToolResult(status="blocked", event_id=event_id, notice=BLOCKED_MESSAGE)

    # ------------------------------------------------------------------- active

    async def _handle_active(
        self, request: ActionRequest, session: SessionRecord, started: float
    ) -> ToolResult:
        connector = self.connectors.get(str(request.connector))
        if connector is None:
            raise ActionError(f"unknown connector {request.connector}")

        prepared: Prepared = await connector.prepare(request)
        event_id = new_event_id()
        safe_target = self._safe_target(prepared)
        benign = self.policy.is_benign_path(prepared.scope or prepared.target)

        content = prepared.content or ""
        detections = await asyncio.to_thread(detect, content, self._detect_context(prepared))
        auto_spans = [span for span in detections if span.confidence >= self.auto_confidence]
        has_floor = has_auto_mask(detections, self.auto_confidence) and prepared.read_operation
        pattern = max_medium_confidence(detections, self.auto_confidence)

        location = 0.0
        if not benign:
            location = max(
                self.policy.hint_weight(prepared.scope or prepared.target),
                float(prepared.meta.get("location_weight", 0.0) or 0.0),
            )

        content_signal = 0.0
        if content:
            redacted = self._redact_spans(content, auto_spans)
            context_label = f"{prepared.connector} | {prepared.scope or prepared.target}"
            content_signal = await asyncio.to_thread(
                self.content_model.score, redacted, context_label
            )

        out_of_scope = self._out_of_scope(prepared)
        behaviour_score = self.behaviour.score(session.id, out_of_scope=out_of_scope)
        scores = self.scorer.combine(pattern, location, content_signal, behaviour_score.combined)

        tier = prepared.tier
        if prepared.rule.decision == Decision.BLOCK:
            tier = 5
        elif prepared.read_operation and has_floor:
            tier = max(tier, 1)
        elif prepared.read_operation and tier == 0 and (location >= 0.5 or content_signal >= 0.5):
            tier = max(tier, 1)

        decision = decide(
            prepared.rule.decision,
            self._tier_default(prepared, tier),
            scores.combined,
            self.thresholds,
            has_floor,
        )

        codes: list[str] = []
        for code in prepared.rule.reason_codes:
            if code not in codes:
                codes.append(code)
        auto_secret = any(span.category == "secret" for span in auto_spans)
        auto_pii = any(span.category != "secret" for span in auto_spans)
        if auto_secret:
            codes.append(ReasonCode.SECRET_DETECTED.value)
        if auto_pii:
            codes.append(ReasonCode.PII_DETECTED.value)
        if out_of_scope:
            codes.append(ReasonCode.TASK_MISMATCH.value)
        if behaviour_score.combined >= 0.5:
            codes.append(ReasonCode.BEHAVIOUR_ESCALATION.value)
        if location >= 0.5:
            codes.append(ReasonCode.SENSITIVE_LOCATION.value)
        if content_signal >= 0.5:
            codes.append(ReasonCode.CONTENT_SENSITIVE.value)
        codes = list(dict.fromkeys(codes))

        if decision == Decision.BLOCK:
            primary = codes[0] if codes else ReasonCode.INTERNAL_ERROR.value
            return await self._finish_blocked(
                request=request,
                prepared=prepared,
                session=session,
                event_id=event_id,
                safe_target=safe_target,
                tier=tier,
                scores=scores,
                reason=primary,
                codes=codes,
                detections=detections,
                started=started,
            )

        restore = None
        if (
            prepared.action == "write_file"
            and prepared.write_content is not None
            and self.policy.masking.restore_on_write
            and decision != Decision.BLOCK
        ):
            restore = await asyncio.to_thread(
                restore_file,
                prepared.write_content,
                rel_path=prepared.scope or prepared.target,
                session_id=session.id,
                store=self.store,
                current_sha256=prepared.meta.get("current_sha256"),
            )
            prepared.write_content = restore.content
            for code in restore.reason_codes:
                if code not in codes:
                    codes.append(code)
            if restore.requires_hold:
                decision = Decision.HOLD
        if (
            prepared.connector == ConnectorName.DATABASE
            and prepared.action == "db_query"
            and not prepared.read_operation
            and prepared.content
            and decision != Decision.BLOCK
        ):
            restored_sql = await asyncio.to_thread(
                restore_sql,
                prepared.content,
                table=prepared.target,
                session_id=session.id,
                store=self.store,
            )
            prepared.write_content = restored_sql.content
            prepared.meta["executed_sql"] = restored_sql.content
            if restore is None:
                restore = restored_sql
            else:
                restore = restore.model_copy(
                    update={"flags": list(dict.fromkeys([*restore.flags, *restored_sql.flags]))}
                )

        mask_logic = self._mask_logic(prepared, location, content_signal)
        unmaskable = False
        if (
            mask_logic
            and prepared.content_kind == "python"
            and prepared.read_operation
            and content
        ):
            try:
                await asyncio.to_thread(find_blocks, content, self.policy.masking.min_body_lines)
            except SyntaxError:
                unmaskable = True
                if ReasonCode.UNMASKABLE_FORMAT.value not in codes:
                    codes.append(ReasonCode.UNMASKABLE_FORMAT.value)
                decision = Decision.HOLD

        approval = ApprovalKind.AUTO
        approval_id: str | None = None
        human_wait = 0.0
        approved_resolution: Resolution | None = None
        denial_reason: str | None = None

        if decision == Decision.HOLD:
            primary = self._primary_reason(
                decision=decision,
                prepared=prepared,
                detections=detections,
                codes=codes,
                benign=benign,
            )
            summary = str(prepared.meta.get("summary") or prepared.display_target or safe_target)
            human_started = time.perf_counter()
            outcome = await self.approvals.request(
                session_id=session.id,
                connector=prepared.connector,
                action=prepared.action,
                target=safe_target,
                summary=self._safe_text(summary)[:300],
                tier=tier,
                reason_codes=codes,
                reason_text=explain(primary, target=safe_target, host=prepared.meta.get("host", "")),
                scores=scores,
                read_operation=prepared.read_operation and not unmaskable,
            )
            human_wait = time.perf_counter() - human_started
            approval_id = outcome.approval_id
            if outcome.resolution is None:
                decision = Decision.HOLD
                denial_reason = ReasonCode.APPROVAL_TIMEOUT.value
            elif outcome.resolution in {Resolution.DENY, Resolution.DENY_AND_LOCK}:
                decision = Decision.HOLD
                denial_reason = ReasonCode.HUMAN_DENIED.value
                if outcome.resolution == Resolution.DENY_AND_LOCK:
                    await self.sessions.lock(session.id, ReasonCode.HUMAN_DENIED.value)
                    await self._publish_session(session.id)
            else:
                approved_resolution = outcome.resolution
                approval = (
                    ApprovalKind.APPROVED_MASKED
                    if outcome.resolution == Resolution.APPROVE_MASKED
                    else ApprovalKind.APPROVED
                )
                if outcome.resolution == Resolution.APPROVE:
                    mask_logic = False
                else:
                    mask_logic = mask_logic or prepared.content_kind == "python"

        if denial_reason is not None:
            return await self._finish_denied(
                request=request,
                prepared=prepared,
                session=session,
                event_id=event_id,
                safe_target=safe_target,
                tier=tier,
                scores=scores,
                codes=codes,
                detections=detections,
                denial_reason=denial_reason,
                approval=approval_id,
                started=started,
                human_wait=human_wait,
                approval_kind=ApprovalKind.DENIED
                if denial_reason == ReasonCode.HUMAN_DENIED.value
                else ApprovalKind.TIMEOUT,
            )

        release_pii = approved_resolution == Resolution.APPROVE
        if decision == Decision.MASK or approved_resolution is not None:
            plan_spans = list(detections)
        else:
            plan_spans = list(auto_spans)
        plan = MaskPlan(
            spans=plan_spans,
            mask_logic=mask_logic and decision != Decision.ALLOW and prepared.read_operation,
            protected_columns=list(prepared.meta.get("protected_columns", []) or []),
            release_pii=release_pii,
        )

        snapshot: SnapshotRecord | None = None
        if prepared.needs_snapshot and prepared.snapshot_kind:
            try:
                snapshot = await self._create_snapshot(event_id, session.id, prepared)
            except SnapshotTooLargeError:
                return await self._finish_blocked(
                    request=request,
                    prepared=prepared,
                    session=session,
                    event_id=event_id,
                    safe_target=safe_target,
                    tier=tier,
                    scores=scores,
                    reason=ReasonCode.TOO_LARGE.value,
                    codes=[ReasonCode.TOO_LARGE.value, *codes],
                    detections=detections,
                    started=started,
                )

        execution_plan = ExecutionPlan(
            decision=decision,
            resolution=approved_resolution,
            mask_plan=plan,
            restore=restore,
            snapshot=snapshot,
            approval=approval,
            reason_codes=codes,
        )
        execution_started = time.perf_counter()
        result = await connector.execute(prepared, execution_plan)
        execution_time = time.perf_counter() - execution_started
        if not result.ok:
            raise ActionError(str(result.meta.get("error") or "The action could not be completed."))

        masked = await self._mask_result(
            prepared=prepared,
            result=result,
            plan=plan,
            session_id=session.id,
            decision=decision,
        )
        final_decision = masked["decision"]
        raw_delta = masked["raw"]
        sanitized_delta = masked["sanitized"]
        changes = masked["changes"]
        flags = list(dict.fromkeys([*masked["flags"], *result.flags]))
        detections_all = list(detections) + list(masked["extra_detections"])

        if final_decision == Decision.ALLOW and masked["masked_out"]:
            final_decision = Decision.MASK
        if decision == Decision.MASK and not changes:
            final_decision = Decision.ALLOW
            flags.append("score_elevated")
        if (
            final_decision != Decision.BLOCK
            and not prepared.read_operation
            and not masked["masked_out"]
        ):
            final_decision = decision

        final_outcome = FinalOutcome.EXECUTED_MASKED if changes else FinalOutcome.EXECUTED

        if approved_resolution is not None:
            if approved_resolution == Resolution.APPROVE:
                codes = [*codes, ReasonCode.HUMAN_APPROVED.value]
            else:
                codes = [*codes, ReasonCode.HUMAN_EDITED.value]
        if final_decision == Decision.MASK and ReasonCode.MASKED_FOR_PRIVACY.value not in codes:
            codes = [*codes, ReasonCode.MASKED_FOR_PRIVACY.value]
        if final_decision == Decision.ALLOW:
            codes = [
                ReasonCode.BENIGN_CONTEXT.value if benign else ReasonCode.ALLOWED.value,
                *[code for code in codes if code not in {ReasonCode.ALLOWED.value, ReasonCode.BENIGN_CONTEXT.value}],
            ]
        codes = list(dict.fromkeys(codes))

        primary = self._primary_reason(
            decision=final_decision,
            prepared=prepared,
            detections=detections_all,
            codes=codes,
            benign=benign,
        )
        reason_text = explain(primary, target=safe_target, host=prepared.meta.get("host", ""))

        latency_ms = round(
            max(0.0, (time.perf_counter() - started - human_wait - execution_time) * 1000), 3
        )
        event = EventRecord(
            id=event_id,
            session_id=session.id,
            ts=utc_now(),
            connector=prepared.connector,
            action=prepared.action,
            target=safe_target,
            task_context=self.policy.task.description or None,
            risk_tier=tier,
            scores=scores,
            decision=final_decision,
            final_outcome=final_outcome,
            approval=approval,
            reason_codes=codes,
            reason_text=reason_text,
            detections=[self._detection_dict(span) for span in detections_all],
            flags=flags,
            content_sha256=_sha256(raw_delta) if raw_delta is not None else None,
            delta_sha256=_sha256(sanitized_delta) if sanitized_delta is not None else None,
            latency_ms=latency_ms,
        )
        delta = (
            DeltaRecord(
                event_id=event_id,
                raw=raw_delta or "",
                sanitized=sanitized_delta or "",
                changes=changes,
            )
            if raw_delta is not None
            else None
        )
        try:
            logged = await self.audit.append_event(event, delta=delta, snapshot=snapshot)
        except Exception:
            logger.error("could not append audit event")
            return ToolResult(status="blocked", event_id=event_id, notice=BLOCKED_MESSAGE)
        if approval_id:
            await asyncio.to_thread(self.audit.attach_approval_event, approval_id, logged.id)
        await self._publish_event(logged)

        sensitive_read = bool(
            prepared.read_operation and (location >= 0.5 or pattern >= 0.5 or has_floor)
        )
        self.behaviour.record(
            session.id,
            decision=str(final_decision),
            sensitive_read=sensitive_read,
            target=safe_target,
        )
        fresh_behaviour = self.behaviour.score(session.id, out_of_scope=out_of_scope)
        if self.behaviour.should_lock(session.id, fresh_behaviour):
            await self.sessions.lock(session.id, ReasonCode.KILL_SWITCH.value)
            await self._log_kill_switch(session.id)
            await self._publish_session(session.id)

        content_out = masked["content"]
        if final_decision == Decision.BLOCK:
            return ToolResult(status="blocked", event_id=logged.id, notice=BLOCKED_MESSAGE)

        status = "masked" if changes or masked["masked_out"] else "ok"
        notice = MASKED_NOTICE if status == "masked" else None
        if status == "masked" and "typed_placeholders" in flags:
            notice = (
                f"{MASKED_NOTICE} Numeric values in protected columns became placeholder strings."
            )
        return ToolResult(
            status=status,
            event_id=logged.id,
            content=content_out,
            notice=notice,
        )

    async def _mask_result(
        self,
        *,
        prepared: Prepared,
        result: ExecResult,
        plan: MaskPlan,
        session_id: str,
        decision: Decision,
    ) -> dict[str, Any]:
        """Mask a connector result and build the delta."""
        flags: list[str] = []
        extra: list[Span] = []
        raw: str | None = None
        sanitized: str | None = None
        changes = []
        masked_out = False

        if (
            not prepared.read_operation
            and str(prepared.connector) in {"command", "network"}
            and result.content
        ):
            raw_content = result.content
            output_context = DetectContext(
                target=str(prepared.target),
                ignore_below=self.ignore_below,
                known_test_values=frozenset(self.policy.benign_context.known_test_values),
                example_email_domains=frozenset(self.policy.benign_context.example_email_domains),
            )
            output_spans = await asyncio.to_thread(detect, raw_content, output_context)
            auto_output = [
                span for span in output_spans if span.confidence >= self.auto_confidence
            ]
            extra.extend(auto_output)
            output_plan = plan.model_copy(
                update={"spans": auto_output, "mask_logic": False, "release_pii": False}
            )
            masked_result = await asyncio.to_thread(
                mask_content,
                raw_content,
                kind=result.content_kind if result.content_kind else "text",
                scope=f"{prepared.scope or prepared.target}:output",
                session_id=session_id,
                detections=auto_output,
                plan=output_plan,
                store=self.store,
                base_sha256=None,
                min_body_lines=self.policy.masking.min_body_lines,
            )
            flags.extend(masked_result.flags)
            return {
                "decision": decision,
                "raw": raw_content,
                "sanitized": masked_result.sanitized,
                "changes": masked_result.changes,
                "flags": flags,
                "extra_detections": extra,
                "content": masked_result.sanitized,
                "masked_out": bool(masked_result.changes),
            }

        if not prepared.read_operation:
            raw = result.raw_delta if result.raw_delta is not None else prepared.content
            sanitized = (
                result.sanitized_delta
                if result.sanitized_delta is not None
                else prepared.write_content
            )
            changes = list(result.changes)
            return {
                "decision": decision,
                "raw": raw,
                "sanitized": sanitized,
                "changes": changes,
                "flags": flags,
                "extra_detections": extra,
                "content": result.content,
                "masked_out": False,
            }

        if result.columns or prepared.content_kind == "table":
            if decision == Decision.ALLOW and not plan.release_pii and not plan.spans:
                columns = result.columns or list(prepared.meta.get("columns", []))
                rows = result.rows or list(prepared.meta.get("rows", []))
                return {
                    "decision": decision,
                    "raw": None,
                    "sanitized": None,
                    "changes": [],
                    "flags": flags,
                    "extra_detections": extra,
                    "content": json.dumps(
                        {
                            "columns": columns,
                            "rows": rows,
                            "truncated": result.truncated or bool(prepared.meta.get("truncated")),
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                    "masked_out": False,
                }
            masked_table = await asyncio.to_thread(
                mask_table,
                table=prepared.target,
                columns=result.columns or list(prepared.meta.get("columns", [])),
                rows=result.rows or list(prepared.meta.get("rows", [])),
                protected_columns=plan.protected_columns,
                session_id=session_id,
                store=self.store,
                plan=plan,
                detect_cell=lambda cell: detect(cell, self._detect_context(prepared)),
                truncated=result.truncated or bool(prepared.meta.get("truncated")),
            )
            flags.extend(masked_table.flags)
            masked_out = bool(masked_table.changes)
            content = json.dumps(
                {
                    "columns": masked_table.columns,
                    "rows": masked_table.rows,
                    "truncated": masked_table.truncated,
                },
                ensure_ascii=False,
            )
            return {
                "decision": decision,
                "raw": masked_table.raw_render,
                "sanitized": masked_table.sanitized,
                "changes": masked_table.changes,
                "flags": flags,
                "extra_detections": extra,
                "content": content,
                "masked_out": masked_out,
            }

        raw_content = result.content if result.content is not None else prepared.content or ""
        if str(prepared.connector) in {"command", "network"} and raw_content:
            output_context = DetectContext(
                target=str(prepared.target),
                ignore_below=self.ignore_below,
                known_test_values=frozenset(self.policy.benign_context.known_test_values),
                example_email_domains=frozenset(self.policy.benign_context.example_email_domains),
            )
            output_spans = await asyncio.to_thread(detect, raw_content, output_context)
            auto_output = [
                span for span in output_spans if span.confidence >= self.auto_confidence
            ]
            extra.extend(auto_output)
            combined = [*plan.spans, *auto_output]
        else:
            combined = list(plan.spans)

        effective_plan = plan.model_copy(update={"spans": combined})
        masked_result = await asyncio.to_thread(
            mask_content,
            raw_content,
            kind=result.content_kind if result.content_kind else prepared.content_kind,
            scope=prepared.scope or prepared.target,
            session_id=session_id,
            detections=combined,
            plan=effective_plan,
            store=self.store,
            base_sha256=prepared.base_sha256,
            min_body_lines=self.policy.masking.min_body_lines,
        )
        flags.extend(masked_result.flags)
        if ReasonCode.UNMASKABLE_FORMAT.value in masked_result.flags:
            return {
                "decision": Decision.BLOCK,
                "raw": raw_content,
                "sanitized": raw_content,
                "changes": [],
                "flags": flags,
                "extra_detections": extra,
                "content": None,
                "masked_out": True,
            }
        return {
            "decision": decision,
            "raw": raw_content,
            "sanitized": masked_result.sanitized,
            "changes": masked_result.changes,
            "flags": flags,
            "extra_detections": extra,
            "content": masked_result.sanitized,
            "masked_out": bool(masked_result.changes),
        }

    async def _create_snapshot(
        self, event_id: str, session_id: str, prepared: Prepared
    ) -> SnapshotRecord | None:
        if prepared.snapshot_kind == "file":
            return await self.snapshots.create_file(
                event_id=event_id,
                session_id=session_id,
                rel_path=prepared.snapshot_target,
                abs_path=self.workspace_root / prepared.snapshot_target,
            )
        if prepared.snapshot_kind == "database":
            return await self.snapshots.create_database(
                event_id=event_id,
                session_id=session_id,
                source=self.workspace_root / prepared.snapshot_target,
                target=prepared.snapshot_target,
            )
        if prepared.snapshot_kind == "workspace":
            return await self.snapshots.create_workspace(event_id=event_id, session_id=session_id)
        return None

    async def _finish_denied(
        self,
        *,
        request: ActionRequest,
        prepared: Prepared,
        session: SessionRecord,
        event_id: str,
        safe_target: str,
        tier: int,
        scores: SignalScores,
        codes: list[str],
        detections: list[Span],
        denial_reason: str,
        approval: str | None,
        started: float,
        human_wait: float,
        approval_kind: ApprovalKind,
    ) -> ToolResult:
        outcome = (
            FinalOutcome.TIMEOUT
            if denial_reason == ReasonCode.APPROVAL_TIMEOUT.value
            else FinalOutcome.DENIED
        )
        event_codes = [denial_reason, *[code for code in codes if code != denial_reason]]
        event = EventRecord(
            id=event_id,
            session_id=session.id,
            ts=utc_now(),
            connector=prepared.connector,
            action=prepared.action,
            target=safe_target,
            task_context=self.policy.task.description or None,
            risk_tier=tier,
            scores=scores,
            decision=Decision.HOLD,
            final_outcome=outcome,
            approval=approval_kind,
            reason_codes=event_codes,
            reason_text=explain(denial_reason, target=safe_target),
            detections=[self._detection_dict(span) for span in detections],
            latency_ms=round(
                max(0.0, (time.perf_counter() - started - human_wait) * 1000), 3
            ),
        )
        try:
            logged = await self.audit.append_event(event)
            if approval:
                await asyncio.to_thread(self.audit.attach_approval_event, approval, logged.id)
            await self._publish_event(logged)
            event_id = logged.id
        except Exception:
            logger.error("could not append denied event")
        self.behaviour.record(
            session.id,
            decision=str(Decision.HOLD),
            sensitive_read=False,
            target=safe_target,
        )
        return ToolResult(status="denied", event_id=event_id, notice=BLOCKED_MESSAGE)

    async def _finish_blocked(
        self,
        *,
        request: ActionRequest,
        prepared: Prepared,
        session: SessionRecord,
        event_id: str,
        safe_target: str,
        tier: int,
        scores: SignalScores,
        reason: str,
        codes: list[str],
        detections: list[Span],
        started: float,
    ) -> ToolResult:
        event = EventRecord(
            id=event_id,
            session_id=session.id,
            ts=utc_now(),
            connector=prepared.connector,
            action=prepared.action,
            target=safe_target,
            task_context=self.policy.task.description or None,
            risk_tier=max(tier, 5),
            scores=scores,
            decision=Decision.BLOCK,
            final_outcome=FinalOutcome.BLOCKED,
            approval=ApprovalKind.AUTO,
            reason_codes=list(dict.fromkeys(codes)),
            reason_text=explain(reason, target=safe_target),
            detections=[self._detection_dict(span) for span in detections],
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )
        try:
            logged = await self.audit.append_event(event)
            await self._publish_event(logged)
            event_id = logged.id
        except Exception:
            logger.error("could not append blocked event")
        self.behaviour.record(
            session.id, decision=str(Decision.BLOCK), sensitive_read=False, target=safe_target
        )
        fresh = self.behaviour.score(session.id)
        if self.behaviour.should_lock(session.id, fresh):
            await self.sessions.lock(session.id, ReasonCode.KILL_SWITCH.value)
            await self._log_kill_switch(session.id)
            await self._publish_session(session.id)
        return ToolResult(status="blocked", event_id=event_id, notice=BLOCKED_MESSAGE)

    async def _log_kill_switch(self, session_id: str) -> None:
        event = EventRecord(
            id=new_event_id(),
            session_id=session_id,
            ts=utc_now(),
            connector=ConnectorName.SYSTEM,
            action="kill_switch",
            target="session",
            task_context=self.policy.task.description or None,
            risk_tier=5,
            scores=SignalScores(),
            decision=Decision.BLOCK,
            final_outcome=FinalOutcome.BLOCKED,
            approval=ApprovalKind.AUTO,
            reason_codes=[ReasonCode.KILL_SWITCH.value],
            reason_text=explain(ReasonCode.KILL_SWITCH.value),
            latency_ms=0.0,
        )
        try:
            logged = await self.audit.append_event(event)
            await self._publish_event(logged)
        except Exception:
            logger.error("could not append kill switch event")

    async def _publish_session(self, session_id: str) -> None:
        session = await self.sessions.get(session_id)
        if session is not None:
            await self.bus.publish("session.updated", {"session": session.model_dump(mode="json")})

    @staticmethod
    def _detection_dict(span: Span) -> dict[str, Any]:
        return {
            "kind": span.kind,
            "category": span.category,
            "confidence": round(span.confidence, 4),
            "preview": span.preview,
        }
