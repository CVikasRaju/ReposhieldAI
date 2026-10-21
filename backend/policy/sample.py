"""Sample .aishield policy template and generator helpers."""

from __future__ import annotations

from pathlib import Path

SAMPLE_AISHIELD_YAML = """# RepoShield AI Policy (.aishield)
# Configure security, privacy masking, allowed commands, and network egress rules.
version: 1
profile: developer     # Options: developer | business | student | strict
mode: balanced         # Options: strict | balanced | permissive

# Task definition: restricts the agent to the intended files
task:
  description: "General project development and maintenance"
  scope_paths: ["src/**", "tests/**", "docs/**", "README.md", "package.json", "pyproject.toml"]

# Target paths for workspace jail and database
paths:
  workspace: ./workspace
  database: ./workspace/business.db

# Hard Path & Table Protection
protect:
  # Hard block sensitive secrets and credential files
  - { kind: path, match: "**/.env*", read: block, write: block, delete: block }
  - { kind: path, match: "**/*.pem", read: block, write: block, delete: block }
  - { kind: path, match: "**/id_rsa*", read: block, write: block, delete: block }
  - { kind: path, match: "**/*.db", read: block, write: block, delete: block }
  # Mask sensitive internal business logic
  - { kind: path, match: "src/auth/**", read: mask, write: hold, delete: block }
  # Protect database tables with sensitive columns
  - kind: table
    match: customers
    read: hold
    write: hold
    delete: block
    columns: [full_name, email, phone, national_id]

# Sensitivity weight hints for scoring (0.0 to 1.0)
hints:
  - { match: "src/pricing/**", weight: 0.6 }
  - { match: "config/**", weight: 0.5 }

# Benign context: sample and test data that should not be blocked
benign_context:
  paths: ["tests/**", "fixtures/**", "docs/examples/**"]
  known_test_values: ["4111111111111111", "4242424242424242", "5555555555554444"]
  example_email_domains: ["example.com", "example.org", "test.com"]

# Shell command access rules (executed with NO shell)
commands:
  allow:
    - "ls*"
    - "cat *"
    - "head *"
    - "tail *"
    - "grep *"
    - "wc *"
    - "git status"
    - "git diff*"
    - "git log*"
  destructive:     # Pauses for human review with automatic pre-execution snapshot
    - "rm -rf *"
    - "rm -r *"
    - "git push --force*"
    - "git push -f*"
    - "git reset --hard*"
    - "git clean -f*"
    - "chmod -R *"
  deny:            # Never allowed
    - "curl *"
    - "wget *"
    - "nc *"
    - "ncat *"
    - "ssh *"
    - "scp *"
    - "sudo *"
    - "mkfs*"
    - "dd *"
  default: hold    # Unlisted commands default to hold
  timeout_seconds: 30
  max_output_bytes: 100000

# Network egress rules (prevents data exfiltration and SSRF)
egress:
  default: deny
  allow_hosts: ["api.github.com", "pypi.org", "registry.npmjs.org"]
  allow_private_ranges: false
  hold_methods: [POST, PUT, PATCH, DELETE]
  timeout_seconds: 10

# Database row limits
database:
  max_rows: 200

# Masking behavior
masking:
  code: structure-preserving
  tables: typed-placeholders
  restore_on_write: true
  min_body_lines: 3

# Kill-switch: automatically lock session after repeated blocked actions
kill_switch:
  max_blocked_attempts: 5
  window_seconds: 60
  behaviour_threshold: 0.90

# Human-in-the-loop approval settings
approval:
  channels: [console, web]
  timeout_seconds: 60
  on_timeout: deny
"""


def get_sample_policy_yaml() -> str:
    """Return the standard commented .aishield YAML template."""
    return SAMPLE_AISHIELD_YAML


def write_sample_policy(target_path: Path, overwrite: bool = False) -> bool:
    """Write the sample .aishield policy to disk.
    
    Returns True if written, False if already existed and overwrite was False.
    """
    path = Path(target_path)
    if path.is_dir():
        path = path / ".aishield"
    if path.exists() and not overwrite:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SAMPLE_AISHIELD_YAML, encoding="utf-8")
    return True
