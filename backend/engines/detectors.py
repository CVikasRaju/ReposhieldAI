"""Detectors for secrets, personal data and financial data.

Every span carries a masked preview, never the raw value. Confidence bands are
applied at the end: anything below ``ignore_below`` is discarded.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from backend.core.models import Span

SECRET_KINDS = {
    "PRIVATE_KEY",
    "AWS_ACCESS_KEY",
    "GITHUB_TOKEN",
    "STRIPE_KEY",
    "SLACK_TOKEN",
    "JWT",
    "SECRET_ASSIGNMENT",
    "CONNECTION_STRING",
    "HIGH_ENTROPY",
}

PLACEHOLDER_VALUES = {
    "",
    "changeme",
    "password",
    "example",
    "xxxx",
    "xxxxx",
    "xxxxxxxx",
    "your_key_here",
    "your_api_key_here",
    "your_secret_here",
    "your_token_here",
}

LOCKFILE_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "uv.lock",
    "Cargo.lock",
    "go.sum",
}

CHECKSUM_MARKERS = ("sha256", "sha1", "sha512", "md5", "integrity", "checksum", "digest")

_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
)
_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_GITHUB = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")
_STRIPE = re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b")
_SLACK = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")
_JWT = re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}\b")
_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|token|passwd|password|private[_-]?key|access[_-]?key)\b"
    r"\s*[:=]\s*[\"']?([^\s\"']{8,})"
)
_CONNECTION = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+:[^\s/@]+@[^\s\"']+")
_ENTROPY_TOKEN = re.compile(r"(?<![A-Za-z0-9+/=_-])([A-Za-z0-9+/=_-]{20,})(?![A-Za-z0-9+/=_-])")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_SSN = re.compile(r"\b(\d{3})-(\d{2})-(\d{4})\b")
_AADHAAR = re.compile(r"\b(\d{4})[ -]?(\d{4})[ -]?(\d{4})\b")
_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(
    r"(?<![\w+])(?:\+\d{1,3}[ -]?)?(?:\(\d{1,4}\)[ -]?)?\d{3,4}[ -]?\d{3}[ -]?\d{4}(?!\w)"
)
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HEX_HASH = re.compile(r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")

VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


@dataclass(frozen=True)
class DetectContext:
    """Everything the detectors need to judge context."""

    target: str = ""
    benign_path: bool = False
    known_test_values: frozenset[str] = field(default_factory=frozenset)
    example_email_domains: frozenset[str] = field(default_factory=frozenset)
    ignore_below: float = 0.40


def shannon_entropy(value: str) -> float:
    """Return the Shannon entropy of a string in bits per character."""
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def luhn_valid(number: str) -> bool:
    """Validate a digit string with the Luhn checksum."""
    digits = [int(ch) for ch in number if ch.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def verhoeff_valid(number: str) -> bool:
    """Validate a digit string with the Verhoeff checksum."""
    digits = [int(ch) for ch in number if ch.isdigit()]
    if len(digits) != 12:
        return False
    c = 0
    for index, digit in enumerate(reversed(digits)):
        c = VERHOEFF_D[c][VERHOEFF_P[index % 8][digit]]
    return c == 0


def iban_valid(value: str) -> bool:
    """Validate an IBAN with the mod-97 checksum."""
    compact = value.replace(" ", "").upper()
    if not 15 <= len(compact) <= 34 or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(ch, 36)) if ch.isalpha() else ch for ch in rearranged)
    try:
        return int(digits) % 97 == 1
    except ValueError:
        return False


def valid_ssn(value: str) -> bool:
    """Reject impossible US SSN area and group numbers."""
    match = _SSN.fullmatch(value.strip())
    if not match:
        return False
    area, group, serial = match.groups()
    return (
        area not in {"000", "666"}
        and not area.startswith("9")
        and group != "00"
        and serial != "0000"
    )


def is_placeholder(value: str) -> bool:
    """Return True for a value that is clearly not a live credential."""
    lowered = value.strip().strip("\"'").lower()
    if lowered in PLACEHOLDER_VALUES:
        return True
    if lowered.startswith("<") and lowered.endswith(">"):
        return True
    if lowered.startswith("${") or lowered.startswith("{{"):
        return True
    if set(lowered) <= {"x", "*", "-", "_", "."}:
        return True
    if lowered.startswith(("os.environ", "process.env", "getenv(", "settings.")):
        return True
    if lowered.startswith("your_") and "here" in lowered:
        return True
    if lowered in {"example", "placeholder", "changeme"}:
        return True
    if lowered.startswith(("example_", "example-", "sample_", "sample-", "dummy_", "dummy-")):
        return True
    return False


def make_preview(kind: str, raw: str) -> str:
    """Build a masked preview that never contains the raw value."""
    if kind in SECRET_KINDS:
        visible = raw[:4] if len(raw) > 8 else ""
        return visible + "*" * min(12, max(4, len(raw) - len(visible)))
    if kind == "EMAIL":
        return raw[:1] + "***@****"
    return "*" * min(16, max(4, len(raw)))


def _line_around(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end]


def _skip_entropy_token(token: str, line: str, target: str) -> bool:
    if _UUID.match(token) or _HEX_HASH.match(token):
        return True
    if token.isdigit():
        return True
    basename = target.replace("\\", "/").rsplit("/", 1)[-1]
    if basename in LOCKFILE_NAMES:
        return True
    lowered = line.lower()
    return any(marker in lowered for marker in CHECKSUM_MARKERS)


def _entropy_threshold(token: str) -> float:
    return 3.5 if all(ch in "0123456789abcdefABCDEF" for ch in token) else 4.0


def detect(text: str, context: DetectContext | None = None) -> list[Span]:
    """Return sensitive spans in text, filtered by ``ignore_below``."""
    ctx = context or DetectContext()
    found: list[Span] = []

    def add(kind: str, category: str, start: int, end: int, confidence: float) -> None:
        raw = text[start:end]
        if not raw:
            return
        found.append(
            Span(
                kind=kind,
                category=category,  # type: ignore[arg-type]
                start=start,
                end=end,
                confidence=confidence,
                preview=make_preview(kind, raw),
            )
        )

    for match in _PRIVATE_KEY.finditer(text):
        add("PRIVATE_KEY", "secret", match.start(), match.end(), 0.99)
    for match in _AWS_KEY.finditer(text):
        add("AWS_ACCESS_KEY", "secret", match.start(), match.end(), 0.99)
    for match in _GITHUB.finditer(text):
        add("GITHUB_TOKEN", "secret", match.start(), match.end(), 0.99)
    for match in _STRIPE.finditer(text):
        add("STRIPE_KEY", "secret", match.start(), match.end(), 0.97)
    for match in _SLACK.finditer(text):
        add("SLACK_TOKEN", "secret", match.start(), match.end(), 0.97)
    for match in _JWT.finditer(text):
        add("JWT", "secret", match.start(), match.end(), 0.90)
    for match in _ASSIGNMENT.finditer(text):
        value = match.group(1)
        confidence = 0.10 if is_placeholder(value) else 0.90
        add("SECRET_ASSIGNMENT", "secret", match.start(1), match.end(1), confidence)
    for match in _CONNECTION.finditer(text):
        add("CONNECTION_STRING", "secret", match.start(), match.end(), 0.92)

    for match in _ENTROPY_TOKEN.finditer(text):
        token = match.group(1)
        line = _line_around(text, match.start(1), match.end(1))
        if _skip_entropy_token(token, line, ctx.target):
            continue
        if shannon_entropy(token) >= _entropy_threshold(token):
            add("HIGH_ENTROPY", "secret", match.start(1), match.end(1), 0.60)

    for match in _CARD.finditer(text):
        raw = match.group(0)
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not luhn_valid(digits):
            continue
        confidence = 0.95
        if ctx.benign_path and digits in ctx.known_test_values:
            confidence = 0.10
        add("CREDIT_CARD", "financial", match.start(), match.end(), confidence)

    for match in _SSN.finditer(text):
        if valid_ssn(match.group(0)):
            add("US_SSN", "pii", match.start(), match.end(), 0.85)

    for match in _AADHAAR.finditer(text):
        if verhoeff_valid(match.group(0)):
            add("AADHAAR", "pii", match.start(), match.end(), 0.90)

    for match in _PAN.finditer(text):
        add("PAN", "pii", match.start(), match.end(), 0.85)

    for match in _IBAN.finditer(text):
        if iban_valid(match.group(0)):
            add("IBAN", "financial", match.start(), match.end(), 0.90)

    for match in _EMAIL.finditer(text):
        value = match.group(0)
        domain = value.rsplit("@", 1)[-1].lower()
        confidence = 0.10 if domain in ctx.example_email_domains else 0.50
        add("EMAIL", "pii", match.start(), match.end(), confidence)

    for match in _PHONE.finditer(text):
        digits = "".join(ch for ch in match.group(0) if ch.isdigit())
        if 10 <= len(digits) <= 15:
            add("PHONE", "pii", match.start(), match.end(), 0.50)

    unique: dict[tuple[str, int, int], Span] = {}
    for span in found:
        if span.confidence < ctx.ignore_below:
            continue
        key = (span.kind, span.start, span.end)
        unique[key] = span
    return sorted(unique.values(), key=lambda span: (span.start, -span.confidence, -(span.end - span.start)))


def max_medium_confidence(spans: list[Span], auto_mask_confidence: float) -> float:
    """Return the highest pattern-signal confidence (medium band only)."""
    medium = [span.confidence for span in spans if span.confidence < auto_mask_confidence]
    return max(medium, default=0.0)


def has_auto_mask(spans: list[Span], auto_mask_confidence: float) -> bool:
    """Return True when at least one span is in the auto-mask band."""
    return any(span.confidence >= auto_mask_confidence for span in spans)


def spans_for_plan(
    spans: list[Span],
    *,
    decision: str,
    auto_mask_confidence: float,
    release_pii: bool,
    approved_masked: bool,
) -> list[Span]:
    """Select the spans a masker may replace for a decision."""
    selected: list[Span] = []
    for span in spans:
        if span.category == "secret":
            if span.confidence >= auto_mask_confidence or decision in {"MASK", "HOLD", "BLOCK"}:
                selected.append(span)
        elif release_pii:
            continue
        elif approved_masked:
            selected.append(span)
        elif span.confidence >= auto_mask_confidence:
            selected.append(span)
        elif decision == "MASK":
            selected.append(span)
    return selected
