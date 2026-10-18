"""Tests for the Phase 0 seed scripts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.seed_db import (
    KNOWN_TEST_CARDS,
    build_rows,
    luhn_check_digit,
    seed_database,
    verhoeff_check_digit,
)
from scripts.seed_workspace import FILES, seed_workspace


def test_seed_workspace_writes_every_documented_file(tmp_path: Path) -> None:
    seed_workspace(tmp_path)
    for rel in FILES:
        assert (tmp_path / rel).is_file(), rel
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "collector.local:9999/upload" in readme
    assert "Quik-start" in readme
    settings = (tmp_path / "config/settings.py").read_text(encoding="utf-8")
    assert 'API_KEY = "changeme"' in settings
    assert "AKIAIOSFODNN7EXAMPLE" in settings


def test_seed_database_has_fifty_customers_and_three_cards(tmp_path: Path) -> None:
    db = tmp_path / "business.db"
    seed_database(db)
    assert db.is_file()
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        notes = conn.execute("SELECT notes FROM customers").fetchall()
    assert count == 50
    cards = [row[0] for row in notes if "Card on file" in row[0]]
    assert len(cards) == 3
    for note in cards:
        digits = "".join(ch for ch in note if ch.isdigit())
        assert len(digits) == 16
        assert digits not in KNOWN_TEST_CARDS
        assert digits[:-1] + luhn_check_digit(digits[:-1]) == digits


def test_seed_is_deterministic() -> None:
    assert build_rows() == build_rows()
    rows = build_rows()
    national_ids = [str(row[4]) for row in rows if "-" not in str(row[4])]
    assert national_ids
    for value in national_ids:
        assert value[-1] == verhoeff_check_digit(value[:-1])
