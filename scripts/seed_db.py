"""Generate the synthetic customers database.

Deterministic: a fixed seed produces the same 50 rows on every run.
"""

from __future__ import annotations

import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_settings  # noqa: E402

KNOWN_TEST_CARDS = {"4111111111111111", "4242424242424242", "5555555555554444"}

FIRST_NAMES = [
    "Asha", "Rohan", "Meera", "Kabir", "Priya", "Arjun", "Neha", "Vikram", "Isha", "Dev",
    "Anika", "Rahul", "Sara", "Nikhil", "Tara", "Aditya", "Maya", "Karan", "Leela", "Omar",
    "Nadia", "Sam", "Elena", "Marcus", "Yuki", "Chloe", "Diego", "Fatima", "Hugo", "Irene",
]
LAST_NAMES = [
    "Rao", "Sharma", "Iyer", "Khan", "Patel", "Nair", "Bose", "Kapoor", "Menon", "Gupta",
    "Silva", "Okafor", "Chen", "Dubois", "Garcia", "Haddad", "Ivanov", "Jensen", "Kowalski", "Lim",
]
CITIES = ["Pune", "Mumbai", "Bengaluru", "Delhi", "Chennai", "Hyderabad", "Kolkata", "Jaipur"]
PLANS = ["free", "pro", "enterprise"]
DOMAINS = ["corp.test", "mail.test", "biz.test"]

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
VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_check_digit(number: str) -> str:
    """Return the Verhoeff check digit for a numeric string."""
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = VERHOEFF_D[c][VERHOEFF_P[(i + 1) % 8][int(ch)]]
    return str(VERHOEFF_INV[c])


def luhn_check_digit(number: str) -> str:
    """Return the Luhn check digit for a partial card number."""
    total = 0
    for i, ch in enumerate(reversed(number)):
        digit = int(ch)
        if i % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def make_card(rng: random.Random) -> str:
    """Generate a Luhn-valid fake card number, never a documented test card."""
    while True:
        body = "4" + "".join(str(rng.randint(0, 9)) for _ in range(14))
        card = body + luhn_check_digit(body)
        if card not in KNOWN_TEST_CARDS:
            return card


def make_aadhaar(rng: random.Random) -> str:
    """Generate a Verhoeff-valid fake 12 digit national id."""
    body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
    return body + verhoeff_check_digit(body)


def make_phone(rng: random.Random, index: int) -> str:
    """Generate a fake phone number in a common format."""
    return f"+1-555-{100 + index:03d}-{1000 + (index * 37) % 9000:04d}"


def build_rows(seed: int = 42, count: int = 50) -> list[tuple[object, ...]]:
    """Build deterministic customer rows."""
    rng = random.Random(seed)
    card_rows = {7, 19, 42}
    rows: list[tuple[object, ...]] = []
    start = date(2024, 1, 8)
    for i in range(1, count + 1):
        first = FIRST_NAMES[(i - 1) % len(FIRST_NAMES)]
        last = LAST_NAMES[(i * 3 - 1) % len(LAST_NAMES)]
        full_name = f"{first} {last}"
        domain = DOMAINS[(i - 1) % len(DOMAINS)]
        email = f"{first.lower()}.{last.lower()}{i}@{domain}"
        phone = make_phone(rng, i)
        national_id = make_aadhaar(rng) if i % 4 else f"{rng.randint(100, 899)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"
        city = CITIES[(i - 1) % len(CITIES)]
        plan = PLANS[(i - 1) % len(PLANS)]
        signup = (start + timedelta(days=i * 3)).isoformat()
        if i in card_rows:
            card = make_card(rng)
            notes = f"Card on file {card[:4]} {card[4:8]} {card[8:12]} {card[12:]} for renewal."
        else:
            notes = ["Prefers email contact.", "Renewal due next quarter.", "Trial account.", ""][i % 4]
        rows.append((i, full_name, email, phone, national_id, city, plan, signup, notes))
    return rows


def seed_database(db_path: Path) -> None:
    """Create the customers table and insert the synthetic rows."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
              id INTEGER PRIMARY KEY,
              full_name TEXT NOT NULL,
              email TEXT NOT NULL,
              phone TEXT NOT NULL,
              national_id TEXT NOT NULL,
              city TEXT NOT NULL,
              plan TEXT NOT NULL,
              signup_date TEXT NOT NULL,
              notes TEXT NOT NULL
            )
            """
        )
        conn.execute("DELETE FROM customers")
        conn.executemany(
            "INSERT INTO customers (id, full_name, email, phone, national_id, city, plan, signup_date, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            build_rows(),
        )
        conn.commit()


def main() -> int:
    """Seed the database configured by RS_WORKSPACE."""
    db_path = get_settings().workspace_abs / "business.db"
    seed_database(db_path)
    print(f"Seeded database at {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
