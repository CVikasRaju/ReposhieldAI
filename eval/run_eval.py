"""Evaluation runner: runs the dataset through the RepoShield pipeline and
compares with the keyword baseline.

Writes eval/results/results.json and eval/results/results.md.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

# Ensure we can import backend modules
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
from backend.core.models import ActionRequest
from backend.tests.mcp_helpers import make_gateway, running_server


def load_dataset(path: Path) -> list[dict]:
    """Load the evaluation dataset from JSONL."""
    items = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def run_pipeline_on_item(item: dict, gateway) -> dict[str, any]:
    """Run a single item through the pipeline and return the decision."""
    connector = item.get("connector", "filesystem")
    target = item.get("target", "")
    content = item.get("content", "")
    task_scope = item.get("task_scope", ["src/**"])

    if connector == "filesystem":
        action = "read_file"
        req = ActionRequest(
            session_id="default",
            connector=connector,
            action=action,
            target=target,
            params={"content": content} if content else {},
        )
        result = gateway.pipeline.handle(req)
        if hasattr(result, "status"):
            status_str = str(result.status).lower()
            if "mask" in status_str:
                return {"decision": "MASK"}
            elif "block" in status_str:
                return {"decision": "BLOCK"}
            elif "allow" in status_str:
                return {"decision": "ALLOW"}
        return {"decision": "unknown"}
    elif connector == "database":
        sql = content if content else "SELECT * FROM customers LIMIT 5"
        req = ActionRequest(
            session_id="default",
            connector=connector,
            action="db_query",
            target=target,
            params={"sql": sql},
        )
        result = gateway.pipeline.handle(req)
        status_str = str(result.status).lower() if hasattr(result, "status") else ""
        if "hold" in status_str:
            return {"decision": "HOLD"}
        elif "block" in status_str:
            return {"decision": "BLOCK"}
        else:
            return {"decision": "ALLOW"}
    elif connector == "command":
        req = ActionRequest(
            session_id="default",
            connector=connector,
            action="run_command",
            target=target,
            params={"command": content},
        )
        result = gateway.pipeline.handle(req)
        status_str = str(result.status).lower() if hasattr(result, "status") else ""
        if "hold" in status_str:
            return {"decision": "HOLD"}
        elif "block" in status_str:
            return {"decision": "BLOCK"}
        else:
            return {"decision": "ALLOW"}
    elif connector == "network":
        req = ActionRequest(
            session_id="default",
            connector=connector,
            action="http_request",
            target=target,
            params={"method": "GET", "url": content},
        )
        result = gateway.pipeline.handle(req)
        status_str = str(result.status).lower() if hasattr(result, "status") else ""
        if "block" in status_str:
            return {"decision": "BLOCK"}
        elif "allow" in status_str:
            return {"decision": "ALLOW"}
        else:
            return {"decision": "unknown"}

    return {"decision": "unknown"}


async def run_eval() -> None:
    dataset_path = Path("eval/dataset.jsonl")
    items = load_dataset(dataset_path)

    tmp_path = Path(tempfile.mkdtemp())
    gateway = make_gateway(tmp_path)

    try:
        import httpx

        async with running_server(gateway.mcp_app) as mcp_base:
            async with running_server(gateway.api_app) as api_base:
                async with httpx.AsyncClient(
                    base_url=api_base, timeout=10.0,
                    headers={"X-Approver-Token": "integration-token"},
                ) as client:
                    results = []
                    for item in items:
                        result = run_pipeline_on_item(item, gateway)
                        expected = item["expected_decision"]
                        sensitive = item.get("sensitive", False)
                        correct = result["decision"] == expected

                        results.append({
                            "id": item["id"],
                            "category": item["category"],
                            "expected_decision": expected,
                            "pipeline_decision": result["decision"],
                            "sensitive": sensitive,
                            "correct": correct,
                            "connector": item.get("connector"),
                            "task_scope": item.get("task_scope"),
                        })

                    # Compute summary metrics
                    n = len(results)
                    sensitive_items = [r for r in results if r["sensitive"]]
                    benign_items = [r for r in results if not r["sensitive"]]

                    # Detection rate: sensitive items where decision >= expected
                    tp = sum(1 for r in sensitive_items if r["correct"])
                    detection_rate = tp / len(sensitive_items) if sensitive_items else 0.0

                    # False positive rate: benign items where decision != ALLOW
                    fp = sum(1 for r in benign_items if not r["correct"])
                    fpr = fp / len(benign_items) if benign_items else 0.0

                    # Context accuracy: for paired items
                    paired = [r for r in results if r["id"].startswith("pair-")]
                    context_correct = sum(1 for r in paired if r["correct"])
                    context_accuracy = context_correct / len(paired) if paired else 0.0

                    # Decision accuracy
                    da = sum(1 for r in results if r["correct"]) / n if n > 0 else 0.0

                    # Precision, Recall, F1
                    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
                    recall = tp / len(sensitive_items) if sensitive_items else 0.0
                    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

                    summary = {
                        "detection_rate": round(detection_rate, 4),
                        "false_positive_rate": round(fpr, 4),
                        "precision": round(precision, 4),
                        "recall": round(recall, 4),
                        "f1": round(f1, 4),
                        "context_accuracy": round(context_accuracy, 4),
                        "decision_accuracy": round(da, 4),
                        "n": n,
                    }

                    # Write results
                    results_dir = Path("eval/results")
                    results_dir.mkdir(parents=True, exist_ok=True)

                    with open(results_dir / "results.json", "w") as f:
                        json.dump({"summary": summary, "results": results}, f, indent=2)

                    # Write markdown
                    metrics_lines = [
                        "| Metric | RepoShield | Keyword baseline |",
                        "|---|---|---|",
                        f"| Detection rate | {summary['detection_rate']} | (placeholder) |",
                        f"| False-positive rate | {summary['false_positive_rate']} | (placeholder) |",
                        f"| Precision | {summary['precision']} | (placeholder) |",
                        f"| F1 | {summary['f1']} | (placeholder) |",
                        f"| Context accuracy (10 pairs) | {summary['context_accuracy']} | (placeholder) |",
                        f"| Decision accuracy | {summary['decision_accuracy']} | (placeholder) |",
                
