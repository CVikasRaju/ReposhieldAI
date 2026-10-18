"""Profile defaults.

Explicit values in the policy file override these. Any key not present in the
file is filled from the selected profile.
"""

from __future__ import annotations

import copy
from typing import Any

BASE_DEFAULTS: dict[str, Any] = {
    "version": 1,
    "task": {"description": "", "scope_paths": []},
    "paths": {"workspace": "./workspace", "database": "./workspace/business.db"},
    "protect": [],
    "hints": [],
    "benign_context": {
        "paths": [],
        "known_test_values": [],
        "example_email_domains": [],
    },
    "commands": {
        "allow": [],
        "destructive": [],
        "deny": [],
        "default": "hold",
        "timeout_seconds": 30,
        "max_output_bytes": 100000,
    },
    "egress": {
        "default": "deny",
        "allow_hosts": [],
        "allow_private_ranges": False,
        "hold_methods": ["POST", "PUT", "PATCH", "DELETE"],
        "timeout_seconds": 10,
    },
    "database": {"max_rows": 200},
    "masking": {
        "code": "structure-preserving",
        "tables": "typed-placeholders",
        "restore_on_write": True,
        "min_body_lines": 3,
    },
    "scoring": {
        "weights": {"base": 0.45, "content": 0.30, "behaviour": 0.25},
        "detectors": {"auto_mask_confidence": 0.85, "ignore_below": 0.40},
    },
    "behaviour": {
        "window_seconds": 60,
        "weights": {"blocked": 0.35, "sensitive_reads": 0.25, "breadth": 0.20, "mismatch": 0.20},
    },
    "kill_switch": {"max_blocked_attempts": 5, "window_seconds": 60, "behaviour_threshold": 0.90},
    "approval": {"channels": ["console", "web"], "timeout_seconds": 60, "on_timeout": "deny"},
    "limits": {"max_read_bytes": 200000, "hide_protected_in_listing": True},
}

_READ_ONLY_ALLOW = ["ls*", "cat *", "head *", "tail *", "grep *", "wc *", "git status", "git diff*", "git log*"]
_DESTRUCTIVE = [
    "rm -rf *",
    "rm -r *",
    "git push --force*",
    "git push -f*",
    "git reset --hard*",
    "git clean -f*",
    "chmod -R *",
]
_DENY = ["curl *", "wget *", "nc *", "ncat *", "ssh *", "scp *", "sudo *", "mkfs*", "dd *"]

PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "developer": {
        "mode": "balanced",
        "protect": [
            {"kind": "path", "match": "**/.env*", "read": "block", "write": "block", "delete": "block"},
            {"kind": "path", "match": "**/*.pem", "read": "block", "write": "block", "delete": "block"},
            {"kind": "path", "match": "**/id_rsa*", "read": "block", "write": "block", "delete": "block"},
        ],
        "commands": {
            "allow": list(_READ_ONLY_ALLOW),
            "destructive": list(_DESTRUCTIVE),
            "deny": list(_DENY),
            "default": "hold",
        },
        "egress": {
            "default": "deny",
            "allow_hosts": ["api.github.com", "pypi.org", "registry.npmjs.org"],
        },
    },
    "business": {
        "mode": "strict",
        "protect": [
            {"kind": "path", "match": "**/.env*", "read": "block", "write": "block", "delete": "block"},
            {"kind": "path", "match": "**/*.pem", "read": "block", "write": "block", "delete": "block"},
        ],
        "commands": {"allow": [], "destructive": list(_DESTRUCTIVE), "deny": list(_DENY), "default": "hold"},
        "egress": {"default": "deny", "allow_hosts": []},
    },
    "student": {
        "mode": "balanced",
        "protect": [
            {"kind": "path", "match": "**/.env*", "read": "block", "write": "block", "delete": "block"},
            {"kind": "path", "match": "**/*.pem", "read": "block", "write": "block", "delete": "block"},
        ],
        "hints": [
            {"match": "thesis/**", "weight": 0.5},
            {"match": "drafts/**", "weight": 0.5},
        ],
        "commands": {
            "allow": list(_READ_ONLY_ALLOW),
            "destructive": list(_DESTRUCTIVE),
            "deny": list(_DENY),
            "default": "hold",
        },
        "egress": {
            "default": "deny",
            "allow_hosts": ["wikipedia.org", "*.wikipedia.org", "arxiv.org", "*.arxiv.org"],
        },
    },
    "strict": {
        "mode": "strict",
        "protect": [
            {"kind": "path", "match": "**/.env*", "read": "block", "write": "block", "delete": "block"},
            {"kind": "path", "match": "**/*.pem", "read": "block", "write": "block", "delete": "block"},
            {"kind": "path", "match": "**/id_rsa*", "read": "block", "write": "block", "delete": "block"},
            {"kind": "path", "match": "**/*.db", "read": "block", "write": "block", "delete": "block"},
        ],
        "commands": {"allow": [], "destructive": list(_DESTRUCTIVE), "deny": list(_DENY), "default": "hold"},
        "egress": {"default": "deny", "allow_hosts": []},
    },
}

PROFILE_NAMES = tuple(PROFILE_DEFAULTS)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge override into a copy of base. Lists and scalars are replaced."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def defaults_for(profile: str) -> dict[str, Any]:
    """Return the full default policy for a profile."""
    return deep_merge(BASE_DEFAULTS, PROFILE_DEFAULTS[profile])
