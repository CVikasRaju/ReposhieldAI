"""Keyword baseline for the evaluation.

Flags an item as sensitive when its content contains a naive keyword, maps
that to BLOCK, and scores the result against the expected decision with the
same metrics the real pipeline uses. This is the deliberately simple
comparison column in eval/results/results.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RANK = {"ALLOW": 0, "MASK": 1, "HOLD": 2, "BLOCK": 3}

KEYWORDS = [
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private",
    "ssn",
    "credit",
    "card",
    ".env",
    "national_id",
    "salary",
]


def is_sensitive_keyword(content: str) -> bool:
    """Return True when the content contains a naive sensitivity keyword."""
    lowered = content.lower()
    return any(keyword in lowered for keyword in KEYWORDS)


def load_items(dataset_path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with dataset_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def run_baseline(dataset_path: Path) -> list[dict[str, Any]]:
    """Score every dataset item with the keyword baseline."""
    results: list[dict[str, Any]] = []
    for item in load_items(dataset_path):
        expected = str(item.get("expected_decision", "ALLOW"))
        decision = "BLOCK" if is_sensitive_keyword(str(item.get("content", ""))) else "ALLOW"
        results.append(
            {
                "id": item.get("id", ""),
                "category": item.get("category", ""),
                "expected_decision": expected,
                "baseline_decision": decision,
                "sensitive": expected != "ALLOW",
                "pair_id": item.get("pair_id"),
                "correct": decision == expected,
            }
        )
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the same headline metrics the pipeline eval reports."""
    n = len(results)
    if n == 0:
        return {"n": 0}

    sensitive_items = [item for item in results if item["sensitive"]]
    benign_items = [item for item in results if not item["sensitive"]]

    detected = [
        item
        for item in sensitive_items
        if RANK.get(item["baseline_decision"], 0) >= RANK.get(item["expected_decision"], 0)
    ]
    detection_rate = len(detected) / len(sensitive_items) if sensitive_items else 0.0

    false_positives = [item for item in benign_items if item["baseline_decision"] != "ALLOW"]
    false_positive_rate = len(false_positives) / len(benign_items) if benign_items else 0.0

    pairs: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        if item.get("pair_id"):
            pairs.setdefault(str(item["pair_id"]), []).append(item)
    complete = [members for members in pairs.values() if len(members) == 2]
    context_correct = [
        members for members in complete if all(member["correct"] for member in members)
    ]
    context_accuracy = len(context_correct) / len(complete) if complete else 0.0

    correct = sum(1 for item in results if item["correct"])
    decision_accuracy = correct / n

    tp = len(detected)
    predicted_positive = [item for item in results if item["baseline_decision"] != "ALLOW"]
    precision = (
        sum(1 for item in predicted_positive if item["sensitive"]) / len(predicted_positive)
        if predicted_positive
        else 0.0
    )
    recall = detection_rate
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "n": n,
        "detected": tp,
        "false_positives": len(false_positives),
        "n_sensitive": len(sensitive_items),
        "n_benign": len(benign_items),
        "n_pairs": len(complete),
        "detection_rate": round(detection_rate, 4),
        "false_positive_rate": round(false_positive_rate, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "context_accuracy": round(context_accuracy, 4),
        "decision_accuracy": round(decision_accuracy, 4),
    }


def main() -> int:
    """Print the baseline summary; run_eval owns the results files."""
    dataset_path = ROOT / "eval" / "dataset.jsonl"
    summary = summarize(run_baseline(dataset_path))
    print(f"Keyword baseline over {summary['n']} items")
    print(f"  detection rate:        {summary['detection_rate']}")
    print(f"  false-positive rate:   {summary['false_positive_rate']}")
    print(f"  precision:             {summary['precision']}")
    print(f"  f1:                    {summary['f1']}")
    print(f"  context accuracy:      {summary['context_accuracy']}")
    print(f"  decision accuracy:     {summary['decision_accuracy']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
