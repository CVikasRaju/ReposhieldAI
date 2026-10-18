"""Detector tests: positives, negatives, checksums and previews."""

from __future__ import annotations

import pytest

from backend.engines.detectors import DetectContext, detect, is_placeholder
from scripts.seed_db import verhoeff_check_digit


def context(target: str = "src/app.py", benign: bool = False) -> DetectContext:
    return DetectContext(
        target=target,
        benign_path=benign,
        known_test_values=frozenset({"4111111111111111", "4242424242424242", "5555555555554444"}),
        example_email_domains=frozenset({"example.com", "example.org", "test.com"}),
    )


def kinds(text: str, ctx: DetectContext | None = None) -> set[str]:
    return {span.kind for span in detect(text, ctx or context())}


AADHAAR = "1234567890" + "1"
AADHAAR = AADHAAR[:11] + verhoeff_check_digit(AADHAAR[:11])

POSITIVES = [
    ("PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc123\n-----END RSA PRIVATE KEY-----"),
    ("AWS_ACCESS_KEY", 'AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"'),
    ("GITHUB_TOKEN", "token=ghp_" + "a" * 36),
    ("STRIPE_KEY", "sk_test_NOTAREALKEY123456"),
    ("SLACK_TOKEN", "xoxb-1234567890-abcdefghijkl"),
    ("JWT", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"),
    ("SECRET_ASSIGNMENT", 'password = "hunter2hunter2"'),
    ("CONNECTION_STRING", "postgres://admin:Sup3rS3cretPass!@db.internal:5432/prod"),
    ("HIGH_ENTROPY", "Xk9f2Qw8Zr4Tn6Yb1Lm3Pc7Vd5Hs0JgA"),
    ("CREDIT_CARD", "card 4111111111111111 expires soon"),
    ("US_SSN", "ssn 123-45-6789"),
    ("AADHAAR", f"id {AADHAAR[:4]} {AADHAAR[4:8]} {AADHAAR[8:]}"),
    ("PAN", "pan ABCDE1234F"),
    ("IBAN", "GB82WEST12345698765432"),
    ("EMAIL", "write to asha@corp.test"),
    ("PHONE", "call +1-555-010-1234"),
]


@pytest.mark.parametrize(("kind", "text"), POSITIVES)
def test_positive_detector(kind: str, text: str) -> None:
    found = kinds(text)
    assert kind in found


def test_private_key_spans_the_whole_block() -> None:
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc123\n-----END RSA PRIVATE KEY-----"
    span = next(span for span in detect(text, context()) if span.kind == "PRIVATE_KEY")
    assert text[span.start : span.end].endswith("-----END RSA PRIVATE KEY-----")


def test_placeholder_values_are_ignored() -> None:
    for text in [
        'API_KEY = "changeme"',
        'API_KEY = "${API_KEY}"',
        'password = "your_password_here"',
        'token = "<token>"',
        'password = ""',
        'secret = os.environ["SECRET"]',
    ]:
        assert "SECRET_ASSIGNMENT" not in kinds(text), text


def test_benign_test_card_is_ignored() -> None:
    assert "CREDIT_CARD" not in kinds('TEST_CARD = "4111111111111111"', context("tests/fixtures/cards.py", True))
    assert "CREDIT_CARD" in kinds('TEST_CARD = "4111111111111111"', context("src/config.py", False))


def test_example_email_is_ignored() -> None:
    assert "EMAIL" not in kinds("demo@example.com")
    assert "EMAIL" in kinds("asha@corp.test")


def test_invalid_checksums_are_rejected() -> None:
    assert "CREDIT_CARD" not in kinds("4111111111111112")
    assert "US_SSN" not in kinds("000-12-3456")
    assert "US_SSN" not in kinds("666-12-3456")
    assert "US_SSN" not in kinds("900-12-3456")
    assert "US_SSN" not in kinds("123-00-6789")
    assert "US_SSN" not in kinds("123-45-0000")
    assert "IBAN" not in kinds("GB82WEST12345698765433")
    assert "AADHAAR" not in kinds("1234 5678 9013")


def test_hashes_uuids_and_lockfiles_are_not_high_entropy() -> None:
    assert "HIGH_ENTROPY" not in kinds("commit 3f9a1c2b7d4e5f60718293a4b5c6d7e8f9012345")
    assert "HIGH_ENTROPY" not in kinds("id 3f9a1c2b-7d4e-5f60-7182-93a4b5c6d7e8")
    assert "HIGH_ENTROPY" not in kinds(
        '"integrity": "sha512-Xk9f2Qw8Zr4Tn6Yb1Lm3Pc7Vd5Hs0JgA"',
        context("package-lock.json"),
    )


def test_benign_context_does_not_lower_real_secrets() -> None:
    text = 'AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"'
    spans = detect(text, context("tests/fixtures/settings.py", True))
    assert any(span.kind == "AWS_ACCESS_KEY" and span.confidence >= 0.85 for span in spans)


def test_previews_never_contain_the_raw_value() -> None:
    text = (
        'AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n'
        "email asha@corp.test\n"
        "card 4111111111111111\n"
        "password = \"hunter2hunter2\"\n"
    )
    for span in detect(text, context()):
        raw = text[span.start : span.end]
        assert raw not in span.preview
        assert span.preview


def test_placeholder_helper() -> None:
    assert is_placeholder("changeme")
    assert is_placeholder("your_key_here")
    assert is_placeholder("${TOKEN}")
    assert not is_placeholder("hunter2hunter2")
    assert not is_placeholder("AKIAIOSFODNN7EXAMPLE")
