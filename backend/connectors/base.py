"""Connector interface.

Adding a connector means implementing this protocol and registering MCP tools.
The pipeline, scoring, approvals, audit and dashboard need no changes.
"""

from __future__ import annotations

from typing import Protocol

from backend.core.models import ActionRequest, ExecResult, ExecutionPlan, Prepared


class Connector(Protocol):
    """Every connector normalises a request and executes an approved plan."""

    name: str

    async def prepare(self, req: ActionRequest) -> Prepared:
        """Normalise targets, assign a tier and fetch content without returning it."""
        ...

    async def execute(self, prepared: Prepared, plan: ExecutionPlan) -> ExecResult:
        """Run the action after the decision and return the raw result."""
        ...
