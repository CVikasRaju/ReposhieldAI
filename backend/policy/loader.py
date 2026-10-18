"""Load, merge and validate the .aishield policy file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from backend.policy.models import Policy
from backend.policy.profiles import PROFILE_NAMES, deep_merge, defaults_for


class PolicyError(ValueError):
    """Raised when a policy file is missing, malformed or invalid."""


def parse_policy(data: dict[str, Any], profile_override: str | None = None) -> Policy:
    """Merge profile defaults into a policy mapping and validate it."""
    if not isinstance(data, dict):
        raise PolicyError("policy root must be a mapping")
    profile = profile_override or data.get("profile") or "developer"
    if profile not in PROFILE_NAMES:
        raise PolicyError(
            f"unknown profile {profile!r}; expected one of {', '.join(PROFILE_NAMES)}"
        )
    merged = deep_merge(defaults_for(profile), data)
    merged["profile"] = profile
    try:
        return Policy.model_validate(merged)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise PolicyError(f"invalid policy: {details}") from exc


def load_policy(path: Path, profile_override: str | None = None) -> Policy:
    """Load a policy file from disk."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError(f"cannot read policy file {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError(f"invalid YAML in {path}: {exc}") from exc
    if data is None:
        raise PolicyError(f"policy file {path} is empty")
    return parse_policy(data, profile_override)
