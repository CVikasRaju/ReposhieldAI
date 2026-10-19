"""Generate synthetic training examples for the offline content model.

This data is separate from the hand-written evaluation dataset. It is generated
once, committed, and read by ``eval/train_content_model.py``.
"""

from __future__ import annotations

import json
import random
import string
from pathlib import Path

OUTPUT = Path(__file__).with_name("training_data.jsonl")
RNG = random.Random(7)
FIRST = ["Asha", "Rohan", "Meera", "Kabir", "Priya", "Arjun", "Neha", "Vikram", "Isha", "Dev"]
LAST = ["Rao", "Sharma", "Iyer", "Khan", "Patel", "Nair", "Bose", "Kapoor", "Menon", "Gupta"]
CITIES = ["Pune", "Mumbai", "Delhi", "Chennai", "Jaipur"]

VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def token(alphabet: str, length: int) -> str:
    return "".join(RNG.choice(alphabet) for _ in range(length))


def verhoeff(number: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = VERHOEFF_D[c][VERHOEFF_P[(i + 1) % 8][int(ch)]]
    return str(VERHOEFF_INV[c])


def luhn(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        digit = int(ch)
        if i % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def card() -> str:
    body = RNG.choice("456") + token(string.digits, 14)
    return body + luhn(body)


def aadhaar() -> str:
    body = str(RNG.randint(2, 9)) + token(string.digits, 10)
    return body + verhoeff(body)


def person() -> tuple[str, str, str, str]:
    first = RNG.choice(FIRST)
    last = RNG.choice(LAST)
    email = f"{first.lower()}.{last.lower()}{RNG.randint(1, 999)}@{RNG.choice(['corp.test', 'mail.test'])}"
    phone = f"+1-555-{RNG.randint(100, 999):03d}-{RNG.randint(1000, 9999):04d}"
    return f"{first} {last}", email, phone, aadhaar()


def credentials() -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for index in range(140):
        kind = index % 10
        if kind == 0:
            rows.append((f'AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE{index:02d}"', "config/settings.py", 1))
        elif kind == 1:
            rows.append((f"GITHUB_TOKEN=ghp_EXAMPLE_TOKEN_FOR_TESTING_{index:04d}", ".env", 1))
        elif kind == 2:
            rows.append((f"STRIPE_API_KEY=sk_test_EXAMPLE_KEY_FOR_TESTING_{index:04d}", ".env", 1))
        elif kind == 3:
            rows.append((f"SLACK_TOKEN=xoxb-EXAMPLE-TOKEN-FOR-TESTING-{index:04d}", "config/secrets.yaml", 1))
        elif kind == 4:
            rows.append((f'password = "ExampleSecretPassword{index}!"', "src/db/connect.py", 1))
        elif kind == 5:
            rows.append((f"DATABASE_URL=postgres://admin:ExamplePassword{index}@db.internal:5432/prod", ".env", 1))
        elif kind == 6:
            rows.append((f"JWT_SECRET=eyJhbGciOiJIUzI1NiJ9.EXAMPLE_PAYLOAD_{index}.EXAMPLE_SIG_{index}", "deploy/config.yml", 1))
        elif kind == 7:
            rows.append((f"-----BEGIN RSA PRIVATE KEY-----\nEXAMPLE_RSA_PRIVATE_KEY_DATA_{index}\n-----END RSA PRIVATE KEY-----", "keys/server.pem", 1))
        elif kind == 8:
            rows.append((f'api_key = "example_api_key_value_{index:04d}"', "scripts/sync.py", 1))
        else:
            rows.append((f"Authorization: Bearer example_bearer_token_{index:04d}", "logs/request.txt", 1))
    return rows


def personal() -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for _ in range(80):
        name, email, phone, national = person()
        choice = RNG.randint(0, 3)
        if choice == 0:
            rows.append((f'{{"full_name": "{name}", "email": "{email}", "phone": "{phone}", "national_id": "{national}"}}', "db:customers", 1))
        elif choice == 1:
            rows.append((f"{name},{email},{phone},{national},{RNG.choice(CITIES)}", "exports/customers.csv", 1))
        elif choice == 2:
            rows.append((f"Customer {name} can be reached at {email} or {phone}. ID {national}.", "notes/accounts.txt", 1))
        else:
            rows.append((f"SELECT * FROM customers WHERE email = '{email}' LIMIT 10", "db:customers", 1))
    return rows


def financial() -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for index in range(80):
        choice = index % 5
        number = card()
        if choice == 0:
            rows.append((f'{{"customer": "acc-{RNG.randint(1000, 9999)}", "card": "{number}", "amount": {RNG.randint(10, 9000)}}}', "db:payments", 1))
        elif choice == 1:
            rows.append((f"Card on file {number[:4]} {number[4:8]} {number[8:12]} {number[12:]} for renewal.", "db:customers", 1))
        elif choice == 2:
            rows.append((f"salary={RNG.randint(50000, 250000)} account=GB{RNG.randint(10, 99)}WEST{token(string.digits, 14)}", "db:payroll", 1))
        elif choice == 3:
            rows.append((f"Invoice {RNG.randint(1000, 9999)} paid with card {number} by customer {RNG.choice(FIRST)}.", "finance/invoices.txt", 1))
        else:
            rows.append((f"IBAN GB{RNG.randint(10, 99)}WEST{token(string.digits, 14)} belongs to {RNG.choice(FIRST)} {RNG.choice(LAST)}.", "finance/accounts.csv", 1))
    return rows


def proprietary() -> list[tuple[str, str, int]]:
    templates = [
        (
            "def score_risk(features):\n"
            "    # proprietary scoring model\n"
            "    base = 0.42 * features['a']\n"
            "    lift = features['b'] ** 1.7\n"
            "    return round(base + lift * 0.31, 4)\n",
            "src/pricing/model.py",
        ),
        (
            "INTERNAL_MARGIN = 0.183\n"
            "def quote(units, tier):\n"
            "    # confidential formula\n"
            "    discount = {1: 1.0, 2: 0.92, 3: 0.85}[tier]\n"
            "    return units * INTERNAL_MARGIN * discount\n",
            "src/pricing/engine.py",
        ),
        (
            "def churn_probability(days, tickets, revenue):\n"
            "    # internal algorithm, do not share\n"
            "    risk = (days / 365) * 0.6 + tickets * 0.08\n"
            "    return min(0.99, risk - revenue / 1_000_000)\n",
            "src/ml/churn.py",
        ),
        (
            "CUSTOMER_VALUE_WEIGHTS = [0.31, 0.24, 0.18, 0.15, 0.12]\n"
            "def lifetime_value(spend, years):\n"
            "    # proprietary customer value model\n"
            "    return sum(w * spend * years for w in CUSTOMER_VALUE_WEIGHTS)\n",
            "src/analytics/value.py",
        ),
    ]
    rows: list[tuple[str, str, int]] = []
    for index in range(150):
        body, context = templates[index % len(templates)]
        variant = body.replace("0.42", f"0.{RNG.randint(10, 89)}").replace("1.7", f"1.{RNG.randint(1, 9)}")
        rows.append((variant, context, 1))
    return rows


def benign() -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for index in range(260):
        choice = index % 12
        if choice == 0:
            rows.append(("def format_greeting(name):\n    return f\"Hello, {name.strip().title()}!\"\n", "src/utils/format.py", 0))
        elif choice == 1:
            rows.append(('TEST_CARD = "4111111111111111"\nTEST_EMAIL = "demo@example.com"\n', "tests/fixtures/cards.py", 0))
        elif choice == 2:
            rows.append(('API_KEY = "changeme"\nTIMEOUT = 30\n', "config/settings.py", 0))
        elif choice == 3:
            rows.append(("contact support@example.com or visit https://example.org/help", "docs/guide.md", 0))
        elif choice == 4:
            rows.append((f'"integrity": "sha512-{token(string.hexdigits.lower(), 64)}"', "package-lock.json", 0))
        elif choice == 5:
            rows.append((f"id: {token('0123456789abcdef', 8)}-{token('0123456789abcdef', 4)}-4{token('0123456789abcdef', 3)}-8{token('0123456789abcdef', 3)}-{token('0123456789abcdef', 12)}", "tests/ids.json", 0))
        elif choice == 6:
            rows.append((f"git commit {token('0123456789abcdef', 40)}", "CHANGELOG.md", 0))
        elif choice == 7:
            rows.append(("def verify_password(username, supplied):\n    # placeholder authentication for tests\n    return supplied == 'hunter2'\n", "tests/test_login.py", 0))
        elif choice == 8:
            rows.append((f"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB{token(string.ascii_letters + string.digits + '+/', 40)}", "docs/assets.md", 0))
        elif choice == 9:
            rows.append(("# Quick start\nInstall the package and run the demo.\n", "README.md", 0))
        elif choice == 10:
            rows.append((f"rows = {RNG.randint(10, 9999)}\nlatency_ms = {RNG.randint(1, 50)}\n", "docs/benchmarks.md", 0))
        else:
            rows.append(("def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n", "src/math.py", 0))
    return rows


def main() -> int:
    rows = credentials() + personal() + financial() + proprietary() + benign()
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, object]] = []
    for text, context, label in rows:
        key = (text, context)
        if key in seen:
            continue
        seen.add(key)
        unique.append({"text": text, "context": context, "label": label})
    OUTPUT.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in unique) + "\n",
        encoding="utf-8",
    )
    sensitive = sum(1 for row in unique if row["label"] == 1)
    print(f"Wrote {len(unique)} examples ({sensitive} sensitive) to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
