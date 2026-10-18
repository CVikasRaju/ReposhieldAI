"""Filesystem connector with a strict workspace jail."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from backend.core.models import (
    ActionError,
    ActionRequest,
    ConnectorName,
    Decision,
    ExecResult,
    ExecutionPlan,
    Prepared,
    RuleVerdict,
)
from backend.policy.models import Policy


class PathEscapeError(ActionError):
    """Raised when a path resolves outside the workspace."""


def sha256_text(text: str) -> str:
    """Return a stable SHA-256 for text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_in_workspace(root: Path, user_path: str) -> tuple[Path, str]:
    """Resolve a user path and require it to stay inside the workspace."""
    workspace = Path(root).resolve()
    cleaned = (user_path or ".").strip()
    if "\x00" in cleaned:
        raise PathEscapeError("Invalid path")
    candidate = Path(cleaned)
    resolved = (
        candidate.resolve(strict=False)
        if candidate.is_absolute()
        else (workspace / candidate).resolve(strict=False)
    )
    if not resolved.is_relative_to(workspace):
        raise PathEscapeError("The requested path is outside the workspace.")
    relative = resolved.relative_to(workspace).as_posix()
    return resolved, relative or "."


class FilesystemConnector:
    """Read, write, list and delete inside the workspace jail."""

    name = "filesystem"

    def __init__(self, policy: Policy, workspace_root: Path) -> None:
        self.policy = policy
        self.workspace = Path(workspace_root).resolve()

    def _path_rule(self, rel: str, op: str) -> RuleVerdict:
        if self.policy.is_policy_tamper_target(rel):
            return RuleVerdict(decision=Decision.BLOCK, reason_codes=["POLICY_TAMPER"])
        rule = self.policy.protect_rule_for_path(rel, op)
        if rule is not None:
            decision = rule.decision_for(op)
            if decision == "BLOCK":
                return RuleVerdict(decision=Decision.BLOCK, reason_codes=["PROTECTED_PATH"])
            if decision == "HOLD":
                return RuleVerdict(decision=Decision.HOLD, reason_codes=["SENSITIVE_LOCATION"])
            if decision == "MASK":
                return RuleVerdict(decision=Decision.MASK, reason_codes=["SENSITIVE_LOCATION"])
        if self.policy.default_path_decision(op) == "HOLD":
            return RuleVerdict(decision=Decision.HOLD, reason_codes=["SENSITIVE_LOCATION"])
        return RuleVerdict()

    async def prepare(self, req: ActionRequest) -> Prepared:
        action = req.action
        try:
            absolute, rel = resolve_in_workspace(self.workspace, req.target)
        except PathEscapeError as exc:
            return Prepared(
                connector=ConnectorName.FILESYSTEM,
                action=action,
                target=req.target[:300],
                display_target=req.target[:300],
                tier=5,
                rule=RuleVerdict(decision=Decision.BLOCK, reason_codes=["PATH_ESCAPE"]),
                meta={"error": str(exc)},
            )

        if self.policy.is_policy_tamper_target(rel):
            return Prepared(
                connector=ConnectorName.FILESYSTEM,
                action=action,
                target=rel,
                display_target=rel,
                tier=5,
                rule=RuleVerdict(decision=Decision.BLOCK, reason_codes=["POLICY_TAMPER"]),
            )

        if action == "list_directory":
            if not absolute.exists():
                raise ActionError(f"Directory not found: {rel}")
            if not absolute.is_dir():
                raise ActionError(f"Not a directory: {rel}")
            return Prepared(
                connector=ConnectorName.FILESYSTEM,
                action=action,
                target=rel,
                display_target=rel,
                scope=rel,
                tier=0,
                read_operation=True,
                meta={"path": rel},
            )

        if action == "read_file":
            return self._prepare_read(absolute, rel)

        if action == "write_file":
            return self._prepare_write(absolute, rel, req)

        if action == "delete_file":
            return self._prepare_delete(absolute, rel)

        raise ActionError(f"Unknown filesystem action: {action}")

    def _prepare_read(self, absolute: Path, rel: str) -> Prepared:
        rule = self._path_rule(rel, "read")
        if rule.decision == Decision.BLOCK:
            return Prepared(
                connector=ConnectorName.FILESYSTEM,
                action="read_file",
                target=rel,
                display_target=rel,
                scope=rel,
                tier=5,
                read_operation=True,
                rule=rule,
            )
        if not absolute.exists():
            raise ActionError(f"File not found: {rel}")
        if absolute.is_dir():
            raise ActionError(f"Not a file: {rel}")
        size = absolute.stat().st_size
        if size > self.policy.limits.max_read_bytes:
            return Prepared(
                connector=ConnectorName.FILESYSTEM,
                action="read_file",
                target=rel,
                display_target=rel,
                scope=rel,
                tier=5,
                read_operation=True,
                rule=RuleVerdict(decision=Decision.BLOCK, reason_codes=["TOO_LARGE"]),
                meta={"size": size},
            )
        data = absolute.read_bytes()
        if b"\x00" in data[:8000]:
            return Prepared(
                connector=ConnectorName.FILESYSTEM,
                action="read_file",
                target=rel,
                display_target=rel,
                scope=rel,
                tier=5,
                read_operation=True,
                rule=RuleVerdict(decision=Decision.BLOCK, reason_codes=["UNSUPPORTED_BINARY"]),
            )
        try:
            content = data.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            content = data.decode("latin-1")
            encoding = "latin-1"
        return Prepared(
            connector=ConnectorName.FILESYSTEM,
            action="read_file",
            target=rel,
            display_target=rel,
            scope=rel,
            tier=1 if rule.decision == Decision.MASK else 0,
            read_operation=True,
            content=content,
            content_kind="python" if rel.endswith(".py") else "text",
            rule=rule,
            base_sha256=sha256_text(content),
            meta={"encoding": encoding, "size": size},
        )

    def _prepare_write(self, absolute: Path, rel: str, req: ActionRequest) -> Prepared:
        rule = self._path_rule(rel, "write")
        if rule.decision == Decision.BLOCK:
            return Prepared(
                connector=ConnectorName.FILESYSTEM,
                action="write_file",
                target=rel,
                display_target=rel,
                scope=rel,
                tier=5,
                read_operation=False,
                rule=rule,
                needs_snapshot=False,
            )
        if absolute.exists() and absolute.is_dir():
            raise ActionError(f"Not a file: {rel}")
        current = absolute.read_text(encoding="utf-8", errors="replace") if absolute.is_file() else ""
        content = str(req.params.get("content", ""))
        return Prepared(
            connector=ConnectorName.FILESYSTEM,
            action="write_file",
            target=rel,
            display_target=rel,
            scope=rel,
            tier=3 if rule.decision == Decision.HOLD else 2,
            read_operation=False,
            content=content,
            content_kind="python" if rel.endswith(".py") else "text",
            write_content=content,
            rule=rule,
            needs_snapshot=True,
            snapshot_kind="file",
            snapshot_target=rel,
            meta={
                "current_sha256": sha256_text(current) if absolute.is_file() else None,
                "existed": absolute.is_file(),
            },
        )

    def _prepare_delete(self, absolute: Path, rel: str) -> Prepared:
        rule = self._path_rule(rel, "delete")
        if rule.decision == Decision.BLOCK:
            return Prepared(
                connector=ConnectorName.FILESYSTEM,
                action="delete_file",
                target=rel,
                display_target=rel,
                scope=rel,
                tier=5,
                read_operation=False,
                rule=rule,
            )
        if not absolute.exists():
            raise ActionError(f"File not found: {rel}")
        if absolute.is_dir():
            raise ActionError("Directories cannot be deleted through delete_file.")
        return Prepared(
            connector=ConnectorName.FILESYSTEM,
            action="delete_file",
            target=rel,
            display_target=rel,
            scope=rel,
            tier=3,
            read_operation=False,
            rule=rule,
            needs_snapshot=True,
            snapshot_kind="file",
            snapshot_target=rel,
            meta={"existed": True},
        )

    async def execute(self, prepared: Prepared, plan: ExecutionPlan) -> ExecResult:
        if prepared.rule.decision == Decision.BLOCK:
            return ExecResult(ok=True, content=None)
        if prepared.action == "list_directory":
            return self._execute_list(prepared)
        if prepared.action == "read_file":
            return ExecResult(content=prepared.content, content_kind=prepared.content_kind)
        if prepared.action == "write_file":
            target = self.workspace / prepared.snapshot_target
            target.parent.mkdir(parents=True, exist_ok=True)
            content = prepared.write_content or ""
            temporary: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", newline="\n", dir=target.parent, delete=False
                ) as handle:
                    temporary = handle.name
                    handle.write(content)
                os.replace(temporary, target)
            except OSError as exc:
                if temporary and Path(temporary).exists():
                    Path(temporary).unlink(missing_ok=True)
                raise ActionError(f"Could not write {prepared.target}: {exc.strerror or exc}") from exc
            return ExecResult(
                content=None,
                raw_delta=prepared.content,
                sanitized_delta=prepared.write_content,
            )
        if prepared.action == "delete_file":
            target = self.workspace / prepared.snapshot_target
            try:
                target.unlink()
            except OSError as exc:
                raise ActionError(f"Could not delete {prepared.target}: {exc.strerror or exc}") from exc
            return ExecResult(content=None)
        raise ActionError(f"Unknown filesystem action: {prepared.action}")

    def _execute_list(self, prepared: Prepared) -> ExecResult:
        directory = self.workspace / (prepared.meta.get("path") or ".")
        entries: list[str] = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
            relative = child.relative_to(self.workspace).as_posix()
            if self.policy.is_policy_tamper_target(relative):
                continue
            rule = self.policy.protect_rule_for_path(relative, "read")
            if (
                rule is not None
                and rule.read == "BLOCK"
                and self.policy.limits.hide_protected_in_listing
            ):
                continue
            entries.append(child.name + ("/" if child.is_dir() else ""))
        content = "\n".join(entries) if entries else "(empty directory)"
        return ExecResult(content=content, content_kind="text")
