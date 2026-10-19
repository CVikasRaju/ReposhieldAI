"""Guards for the hand-written evaluation dataset.

The evaluation only means something if its items were never used for training.
These tests fail when a dataset item leaks into the training data, and they
keep the schema and category quotas from silently drifting.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "eval" / "dataset.jsonl"
TRAINING = ROOT / "eval" / "training_data.jsonl"

ALLOWED_DECISIONS = {"ALLOW", "MASK", "HOLD", "BLOCK"}
ALLOWED_CONNECTORS = {"filesystem", "command", "database", "network"}
ALLOWED_CATEGORIES = {
    "secrets",
    "pii_financial",
    "proprietary_code",
    "benign",
    "paired_context",
    "commands",
    "sql",
    "egress",
}
QUOTAS = {
    "secrets": 30,
    "pii_financial": 25,
    "proprietary_code": 20,
    "benign": 25,
    "paired_context": 20,
    "commands": 30,
    "sql": 10,
    "egress": 10,
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def normalise(text: str) -> str:
    return " ".join(text.lower().split())


@pytest.fixture(scope="module")
def dataset() -> list[dict[str, Any]]:
    return load_jsonl(DATASET)


@pytest.fixture(scope="module")
def training_texts() -> list[str]:
    return [normalise(item["text"]) for item in load_jsonl(TRAINING)]


def test_dataset_items_do_not_appear_in_training_data(
    dataset: list[dict[str, Any]], training_texts: list[str]
) -> None:
    """No dataset item's content may be present in any training sample."""
    leaked: list[str] = []
    for item in dataset:
        content = normalise(str(item.get("content", "")))
        if not content:
            continue
        for sample in training_texts:
            if content in sample or sample in content:
                leaked.append(f"{item['id']} <-> {sample[:60]!r}")
                break
    assert not leaked, "evaluation items leaked into training data:\n" + "\n".join(leaked[:10])


def test_dataset_schema(dataset: list[dict[str, Any]]) -> None:
    """Every item has the documented fields and valid values."""
    ids: set[str] = set()
    for item in dataset:
        for key in ("id", "category", "connector", "content", "context", "expected_decision", "note"):
            assert key in item, f"{item.get('id', '?')} is missing {key}"
        assert item["id"] not in ids, f"duplicate id {item['id']}"
        ids.add(item["id"])
        assert item["category"] in ALLOWED_CATEGORIES, item["category"]
        assert item["connector"] in ALLOWED_CONNECTORS, item["connector"]
        assert item["expected_decision"] in ALLOWED_DECISIONS, item["expected_decision"]
        assert str(item["content"]).strip(), f"{item['id']} has empty content"


def test_dataset_meets_documented_quotas(dataset: list[dict[str, Any]]) -> None:
    """The categories cover the counts docs/08 part C asks for."""
    counts = Counter(item["category"] for item in dataset)
    shortfall = {
        name: (counts.get(name, 0), needed)
        for name, needed in QUOTAS.items()
        if counts.get(name, 0) < needed
    }
    assert not shortfall, f"categories below quota (have, need): {shortfall}"


def test_pairs_are_complete(dataset: list[dict[str, Any]]) -> None:
    """Every pair_id appears exactly twice with one sensitive and one benign role."""
    pairs: dict[str, list[dict[str, Any]]] = {}
    for item in dataset:
        if item.get("pair_id"):
            pairs.setdefault(str(item["pair_id"]), []).append(item)
    assert len(pairs) >= 10, f"only {len(pairs)} pairs"
    for pair_id, members in pairs.items():
        assert len(members) == 2, f"{pair_id} has {len(members)} members"
        roles = sorted(str(member.get("pair_role")) for member in members)
        assert roles == ["benign", "sensitive"], f"{pair_id} roles are {roles}"
