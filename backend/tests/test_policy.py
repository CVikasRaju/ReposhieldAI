"""Tests for the policy engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.policy.loader import PolicyError, load_policy, parse_policy
from backend.policy.matcher import path_matches
from backend.policy.profiles import PROFILE_NAMES

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("**/.env*", ".env", True),
        ("**/.env*", "config/.env", True),
        ("**/.env*", "a/b/.env.local", True),
        ("**/.env*", "env.txt", False),
        ("src/auth/**", "src/auth/login.py", True),
        ("src/auth/**", "src/auth/x/y.py", True),
        ("src/auth/**", "src/authx/a.py", False),
        ("**/*.db", "business.db", True),
        ("**/*.db", "data/x.db", True),
        ("**/*.db", "notes.txt", False),
        ("tests/**", "tests/fixtures/cards.py", True),
        ("*.py", "app.py", True),
        ("*.py", "src/app.py", False),
        ("docs/**", "docs", False),
    ],
)
def test_matcher_cases(pattern: str, path: str, expected: bool) -> None:
    assert path_matches(pattern, path) is expected


def test_canonical_policy_loads() -> None:
    policy = load_policy(ROOT / ".aishield")
    assert policy.version == 1
    assert policy.profile == "developer"
    assert policy.mode == "strict"
    thresholds = policy.thresholds()
    assert thresholds.mask == pytest.approx(0.30)
    assert thresholds.hold == pytest.approx(0.55)
    assert thresholds.block == pytest.approx(0.80)


def test_empty_policy_uses_developer_defaults() -> None:
    policy = parse_policy({})
    assert policy.profile == "developer"
    assert policy.mode == "balanced"
    assert policy.thresholds().hold == pytest.approx(0.65)


def test_explicit_mode_overrides_profile_default() -> None:
    policy = parse_policy({"mode": "strict"})
    assert policy.mode == "strict"
    assert policy.thresholds().hold == pytest.approx(0.55)


def test_explicit_thresholds_override_mode_preset() -> None:
    policy = parse_policy({"mode": "strict", "scoring": {"thresholds": {"mask": 0.2, "hold": 0.4, "block": 0.7}}})
    assert policy.thresholds().mask == pytest.approx(0.2)
    assert policy.thresholds().block == pytest.approx(0.7)


def test_profile_override_changes_defaults_but_not_explicit_values() -> None:
    policy = parse_policy({"profile": "developer", "mode": "balanced"}, profile_override="student")
    assert policy.profile == "student"
    assert policy.mode == "balanced"
    assert policy.hint_weight("thesis/chapter1.md") == pytest.approx(0.5)
    assert policy.egress_allows("wikipedia.org")
    assert not policy.egress_allows("api.github.com")


def test_unknown_profile_rejected() -> None:
    with pytest.raises(PolicyError, match="unknown profile"):
        parse_policy({"profile": "wizard"})
    with pytest.raises(PolicyError, match="unknown profile"):
        parse_policy({}, profile_override="wizard")


def test_unknown_key_rejected() -> None:
    with pytest.raises(PolicyError, match="invalid policy"):
        parse_policy({"scroring": {}})


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(PolicyError, match="must sum to 1.0"):
        parse_policy({"scoring": {"weights": {"base": 0.5, "content": 0.3, "behaviour": 0.3}}})


def test_thresholds_must_be_ordered() -> None:
    with pytest.raises(PolicyError, match="mask < hold < block"):
        parse_policy({"scoring": {"thresholds": {"mask": 0.7, "hold": 0.5, "block": 0.9}}})


def test_protect_rules() -> None:
    policy = load_policy(ROOT / ".aishield")
    assert policy.protect_rule_for_path(".env", "read").read == "BLOCK"
    assert policy.protect_rule_for_path("config/.env", "read").read == "BLOCK"
    assert policy.protect_rule_for_path("src/auth/login.py", "read").read == "MASK"
    assert policy.protect_rule_for_path("src/auth/login.py", "delete").delete == "BLOCK"
    assert policy.protect_rule_for_path("README.md", "read") is None
    table = policy.protect_rule_for_table("customers", "read")
    assert table is not None and table.read == "HOLD"
    assert table.delete == "BLOCK"
    assert policy.protected_columns("customers") == ["full_name", "email", "phone", "national_id"]


def test_business_profile_protects_sensitive_columns() -> None:
    policy = parse_policy({"profile": "business"})
    rule = policy.protect_rule_for_table("accounts", "read", columns=["id", "Email", "salary"])
    assert rule is not None
    assert rule.read == "HOLD"
    assert rule.delete == "BLOCK"


def test_hints_and_benign_context() -> None:
    policy = load_policy(ROOT / ".aishield")
    assert policy.hint_weight("src/pricing/engine.py") == pytest.approx(0.6)
    assert policy.hint_weight("src/app.py") == 0.0
    assert policy.is_benign_path("tests/fixtures/cards.py")
    assert not policy.is_benign_path("src/app.py")
    assert policy.is_test_value("4111 1111 1111 1111")
    assert policy.is_example_email("demo@example.com")
    assert not policy.is_example_email("someone@corp.test")


def test_command_verdict() -> None:
    policy = load_policy(ROOT / ".aishield")
    denied = policy.command_verdict("curl http://x")
    assert (denied.decision, denied.reason_code, denied.tier) == ("BLOCK", "DENIED_COMMAND", 5)
    destructive = policy.command_verdict("rm -rf src")
    assert (destructive.decision, destructive.reason_code, destructive.tier) == (
        "HOLD",
        "DESTRUCTIVE_COMMAND",
        3,
    )
    allowed = policy.command_verdict("git status")
    assert allowed.decision == "ALLOW"
    assert policy.command_verdict("git diff --stat").decision == "ALLOW"
    unlisted = policy.command_verdict("python -c 1")
    assert (unlisted.decision, unlisted.reason_code) == ("HOLD", "UNLISTED_COMMAND")


def test_egress_allowlist_and_wildcards() -> None:
    policy = load_policy(ROOT / ".aishield")
    assert policy.egress_allows("api.github.com")
    assert policy.egress_allows("API.GITHUB.COM")
    assert not policy.egress_allows("evil.example")
    student = parse_policy({"profile": "student"})
    assert student.egress_allows("en.wikipedia.org")
    assert student.egress_allows("arxiv.org")
    assert not student.egress_allows("notwikipedia.org")


def test_policy_tamper_targets() -> None:
    policy = load_policy(ROOT / ".aishield")
    assert policy.is_policy_tamper_target(".aishield")
    assert policy.is_policy_tamper_target("config/.aishield")
    assert policy.is_policy_tamper_target("data/audit.db")
    assert policy.is_policy_tamper_target("a/data/b")
    assert not policy.is_policy_tamper_target("database/schema.py")
    assert not policy.is_policy_tamper_target("metadata.json")


def test_every_profile_parses() -> None:
    for name in PROFILE_NAMES:
        policy = parse_policy({"profile": name})
        assert policy.profile == name
