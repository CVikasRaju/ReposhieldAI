"""Command connector: allowlist first, no shell, path-argument analysis."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from backend.connectors.filesystem import PathEscapeError, resolve_in_workspace
from backend.core.models import (
    ActionError,
    ActionRequest,
    ConnectorName,
    Decision,
    ExecResult,
    ExecutionPlan,
    Prepared,
    RuleVerdict,
    strictest,
)
from backend.policy.matcher import path_matches
from backend.policy.models import Policy

SHELL_OPERATORS = (";", "&&", "||", "|", "`", "$(", ">", "<", "\n", "\r")


class CommandConnector:
    """Run one allowlisted command with no shell and a minimal environment."""

    name = "command"

    def __init__(self, policy: Policy, workspace_root: Path) -> None:
        self.policy = policy
        self.workspace = Path(workspace_root).resolve()

    async def prepare(self, req: ActionRequest) -> Prepared:
        raw = str(req.params.get("command", req.target or ""))
        if any(operator in raw for operator in SHELL_OPERATORS):
            return self._prepared(
                raw,
                raw,
                TierVerdict(
                    5,
                    RuleVerdict(decision=Decision.BLOCK, reason_codes=["SHELL_OPERATORS"]),
                ),
            )
        try:
            tokens = shlex.split(raw)
        except ValueError:
            return self._prepared(
                raw,
                raw,
                TierVerdict(
                    5,
                    RuleVerdict(decision=Decision.BLOCK, reason_codes=["SHELL_OPERATORS"]),
                ),
            )
        if not tokens:
            raise ActionError("Empty command.")
        normalised = " ".join(tokens)
        match = self.policy.command_verdict(normalised)
        rule = RuleVerdict(
            decision=match.decision,
            reason_codes=[] if match.decision == Decision.ALLOW else [match.reason_code],
        )
        tier = match.tier

        path_rule, path_tier = self._path_analysis(tokens)
        rule = RuleVerdict(
            decision=strictest(rule.decision, path_rule.decision),
            reason_codes=list(dict.fromkeys([*rule.reason_codes, *path_rule.reason_codes])),
        )
        tier = max(tier, path_tier)
        if path_rule.decision == Decision.BLOCK:
            tier = 5

        needs_snapshot = rule.decision == Decision.HOLD and tier >= 3
        return self._prepared(
            normalised,
            normalised,
            TierVerdict(tier, rule),
            needs_snapshot=needs_snapshot,
        )

    def _prepared(
        self,
        target: str,
        display: str,
        verdict: TierVerdict,
        *,
        needs_snapshot: bool = False,
    ) -> Prepared:
        return Prepared(
            connector=ConnectorName.COMMAND,
            action="run_command",
            target=target[:300],
            display_target=display[:300],
            scope=target[:300],
            tier=verdict.tier,
            read_operation=False,
            content=target if verdict.rule.decision != Decision.BLOCK else None,
            content_kind="text",
            rule=verdict.rule,
            needs_snapshot=needs_snapshot,
            snapshot_kind="workspace" if needs_snapshot else None,
            meta={"command": target, "summary": target},
        )

    def _path_analysis(self, tokens: list[str]) -> tuple[RuleVerdict, int]:
        decision = Decision.ALLOW
        codes: list[str] = []
        tier = 0
        for token in tokens:
            if token.startswith("-") or not token:
                continue
            try:
                absolute, relative = resolve_in_workspace(self.workspace, token)
            except PathEscapeError:
                decision = strictest(decision, Decision.BLOCK)
                codes.append("PATH_ESCAPE")
                tier = 5
                continue
            exists = absolute.exists()
            protected_glob = any(
                rule.kind == "path" and path_matches(rule.match, relative)
                for rule in self.policy.protect
            )
            if not exists and not protected_glob:
                continue
            if self.policy.is_policy_tamper_target(relative):
                decision = strictest(decision, Decision.BLOCK)
                codes.append("POLICY_TAMPER")
                tier = 5
                continue
            rule = self.policy.protect_rule_for_path(relative, "read")
            if rule is None:
                continue
            if rule.read == "BLOCK":
                decision = strictest(decision, Decision.BLOCK)
                codes.append("PROTECTED_PATH")
                tier = 5
            elif rule.read == "HOLD":
                decision = strictest(decision, Decision.HOLD)
                codes.append("SENSITIVE_LOCATION")
                tier = max(tier, 3)
        return RuleVerdict(decision=decision, reason_codes=list(dict.fromkeys(codes))), tier

    async def execute(self, prepared: Prepared, plan: ExecutionPlan) -> ExecResult:
        if prepared.rule.decision == Decision.BLOCK:
            return ExecResult(ok=True, content=None)
        tokens = shlex.split(prepared.meta.get("command", ""))
        try:
            try:
                completed = subprocess.run(
                    tokens,
                    cwd=str(self.workspace),
                    env={"PATH": os.environ.get("PATH", ""), "HOME": "/tmp"},
                    timeout=self.policy.commands.timeout_seconds,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                    check=False,
                )
            except FileNotFoundError as exc:
                if sys.platform == "win32" and tokens:
                    first = tokens[0].lower()
                    if first == "cat" and len(tokens) > 1:
                        out_parts = []
                        retcode = 0
                        for arg in tokens[1:]:
                            if arg.startswith("-"):
                                continue
                            target_file = (self.workspace / arg).resolve()
                            if target_file.is_file():
                                out_parts.append(target_file.read_text(encoding="utf-8", errors="replace"))
                            else:
                                out_parts.append(f"cat: {arg}: No such file or directory\n")
                                retcode = 1
                        completed = SimpleNamespace(stdout="".join(out_parts), stderr="", returncode=retcode)
                    elif first == "rm" and len(tokens) > 1:
                        out_parts = []
                        retcode = 0
                        for arg in tokens[1:]:
                            if arg.startswith("-"):
                                continue
                            target_path = (self.workspace / arg).resolve()
                            if target_path.exists():
                                if target_path.is_dir():
                                    shutil.rmtree(target_path, ignore_errors=True)
                                else:
                                    target_path.unlink(missing_ok=True)
                            else:
                                out_parts.append(f"rm: {arg}: No such file or directory\n")
                                retcode = 1
                        completed = SimpleNamespace(stdout="".join(out_parts), stderr="", returncode=retcode)
                    else:
                        raise exc
                else:
                    raise exc
        except subprocess.TimeoutExpired as exc:
            raise ActionError(
                f"Command timed out after {self.policy.commands.timeout_seconds} seconds."
            ) from exc
        except FileNotFoundError as exc:
            raise ActionError(f"Command not found: {tokens[0] if tokens else ''}") from exc
        except OSError as exc:
            raise ActionError(f"Could not run command: {exc.strerror or exc}") from exc

        output = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode != 0:
            output += f"\n[exit code {completed.returncode}]"
        encoded = output.encode("utf-8")[: self.policy.commands.max_output_bytes]
        output = encoded.decode("utf-8", errors="ignore")
        return ExecResult(
            content=output,
            content_kind="command_output",
            meta={"returncode": completed.returncode},
        )


class TierVerdict:
    """Small helper pairing a tier with a rule verdict."""

    def __init__(self, tier: int, rule: RuleVerdict) -> None:
        self.tier = tier
        self.rule = rule
