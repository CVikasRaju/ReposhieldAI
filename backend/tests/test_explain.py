"""Every reason code has a safe, plain-language explanation."""

from __future__ import annotations

import pytest

from backend.core.explain import TEMPLATES, explain
from backend.core.reasons import ALL_CODES, ReasonCode

DOCUMENTED = {
    "PROTECTED_PATH", "PROTECTED_TABLE", "SECRET_DETECTED", "PII_DETECTED", "PATH_ESCAPE",
    "POLICY_TAMPER", "EGRESS_DENIED", "EXFIL_DETECTED", "OUTBOUND_WRITE", "DESTRUCTIVE_COMMAND",
    "DENIED_COMMAND", "UNLISTED_COMMAND", "SHELL_OPERATORS", "DESTRUCTIVE_SQL", "UNSAFE_SQL",
    "TASK_MISMATCH", "SENSITIVE_LOCATION", "CONTENT_SENSITIVE", "BEHAVIOUR_ESCALATION",
    "KILL_SWITCH", "SESSION_LOCKED", "MASKED_FOR_PRIVACY", "UNMASKABLE_FORMAT",
    "MASKED_BLOCK_REMOVED", "STALE_MASKED_BASE", "HUMAN_APPROVED", "HUMAN_EDITED",
    "HUMAN_DENIED", "APPROVAL_TIMEOUT", "BENIGN_CONTEXT", "UNSUPPORTED_BINARY", "TOO_LARGE",
    "WATCHDOG_ALERT", "INTERNAL_ERROR",
}


def test_every_documented_code_has_a_template() -> None:
    assert DOCUMENTED <= set(ALL_CODES)
    assert DOCUMENTED <= set(TEMPLATES)


@pytest.mark.parametrize("code", ALL_CODES)
def test_templates_are_non_empty_and_fill(code: str) -> None:
    text = explain(code, target="src/app.py", host="example.com")
    assert text
    assert "{" not in text and "}" not in text


def test_templates_use_the_right_placeholders() -> None:
    assert "collector.local" in explain("EGRESS_DENIED", host="collector.local")
    assert "src/pricing/engine.py" in explain("PROTECTED_PATH", target="src/pricing/engine.py")
    assert "customers" in explain("PROTECTED_TABLE", target="customers")


def test_extra_fields_are_never_interpolated() -> None:
    text = explain(
        "SECRET_DETECTED",
        target="config/settings.py",
        leaked="AKIAIOSFODNN7EXAMPLE",
        password="hunter2",
    )
    assert "AKIAIOSFODNN7EXAMPLE" not in text
    assert "hunter2" not in text


def test_unknown_code_falls_back() -> None:
    text = explain("NOT_A_REASON", target="x")
    assert text == TEMPLATES["ALLOWED"]


def test_display_only_codes_are_marked() -> None:
    assert explain("ACTION_FAILED")
    assert ReasonCode.ACTION_FAILED.value == "ACTION_FAILED"
