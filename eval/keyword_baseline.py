"""Keyword baseline: flags items as sensitive if they contain certain keywords."""

from __future__ import annotations

import json
from pathlib import Path

import sys
from typing import Any

RNG = None  # not needed for baseline


def is_sensitive_keyword(content: str) -> bool:
    """Return True if content contains any keyword that indicates sensitivity."""
    keywords = [
        "password", "secret", "token", "api_key", "apikey", "private",
        "ssn", "credit", "card", ".env", "national_id", "salary",
    ]
    content_lower = content.lower()
    return any(kw in content_lower for kw in keywords)


def run_baseline(dataset_path: Path) -> list[dict[str, Any]]:
    """Run the keyword baseline on the dataset and return results."""
    results = []
    with open(dataset_path) as f:
        for line in f:
            item = json.loads(line)
            content = item["content"]
            sensitive = is_sensitive_keyword(content)
            # The baseline decision is: BLOCK if sensitive, ALLOW otherwise
            # but we map to the same decision format as RepoShield
            decision = "BLOCK" if sensitive else "ALLOW"
            results.append({
                "id": item["id"],
                "category": item["category"],
                "expected_decision": item["expected_decision"],
                "baseline_decision": decision,
                "sensitive": item["sensitive"],
                "correct": decision == item["expected_decision"],
            })
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary metrics for the baseline results."""
    n = len(results)
    if n == 0:
        return {"n": 0}

    # Detection rate: sensitive items where decision >= expected
    sensitive_items = [r for r in results if r["sensitive"]]
    benign_items = [r for r in results if not r["sensitive"]]
    
    # True positives (sensitive correctly caught)
    tp = sum(1 for r in sensitive_items if r["baseline_decision"] == "BLOCK")
    detection_rate = tp / len(sensitive_items) if sensitive_items else 0.0
    
    # False positives (benign incorrectly blocked)
    fp = sum(1 for r in benign_items if r["baseline_decision"] == "BLOCK")
    fpr = fp / len(benign_items) if benign_items else 0.0
    
    # Context accuracy: for paired items, both get expected decision
    paired = [r for r in results if r["id"].startswith("pair-")]
    context_acc = sum(1 for r in paired if r["correct"]) / len(paired) if paired else 0.0
    
    # Decision accuracy
    da = sum(1 for r in results if r["correct"]) / n
    
    # Precision and F1
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / len(sensitive_items) if sensitive_items else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    
    return {
        "n": n,
        "detection_rate": round(detection_rate, 4),
        "false_positive_rate": round(fpr, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "context_accuracy": round(context_acc, 4),
        "decision_accuracy": round(da, 4),
    }


def main() -> None:
    dataset_path = Path("eval/dataset.jsonl")
    results = run_baseline(dataset_path)
    summary = summarize(results)
    
    # Write results
    results_dir = Path("eval/results")
    results_dir.mkdir(parents=True, exist_ok=True)
    
    with open(results_dir / "results.json", "w") as f:
        json.dump({"summary": summary, "results": results}, f, indent=2)
    
    # Write markdown
    lines = [
        f"| Metric | Keyword baseline |",
        "|---|---|",
        f"| Detection rate | {summary['detection_rate']} |",
        f"| False-positive rate | {summary['false_positive_rate']} |",
        f"| Precision | {summary['precision']} |",
        f"| F1 | {summary['f1']} |",
        f"| Context accuracy (pairs) | {summary['context_accuracy']} |",
        f"| Decision accuracy | {summary['decision_accuracy']} |",
        f"| Injection resistance | n/a |",
        f"| Latency median / p95 (ms) | n/a |",
        f"| Restore correctness | n/a |",
    ]
    with open(results_dir / "results.md", "w") as f:
        f.write("\n".join(lines))
    
    print("Keyword baseline complete.")
    print(f"Processed {summary['n']} items")
    print(f"Detection rate: {summary['detection_rate']}")
    print(f"False-positive rate: {summary['false_positive_rate']}")
    print(f"Decision accuracy: {summary['decision_accuracy']}")


if __name__ == "__main__":
    main()