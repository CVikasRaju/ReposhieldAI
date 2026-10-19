"""Evaluation runner for docs/08 part C.

Feeds every item of eval/dataset.jsonl through the real scoring and decision
pipeline (no MCP), runs eval/keyword_baseline.py on the same items, then
writes eval/results/results.json and eval/results/results.md with the measured
metrics, a per-category breakdown and the failure list.

Nothing here is tuned: every number in the output comes from this run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.models import ActionRequest, ConnectorName, MaskPlan  # noqa: E402
from backend.engines.code_masker import find_blocks  # noqa: E402
from backend.engines.detectors import DetectContext, detect  # noqa: E402
from backend.engines.privacy_masker import mask as mask_content  # noqa: E402
from backend.engines.restore import PLACEHOLDER_TOKEN, restore_file  # noqa: E402
from backend.engines.text_masker import MaskMapStore  # noqa: E402
from backend.tests.mcp_helpers import make_gateway  # noqa: E402
from eval.keyword_baseline import run_baseline  # noqa: E402
from eval.keyword_baseline import summarize as summarize_baseline  # noqa: E402

MIN_LATENCY_CALLS = 500
RESTORE_TRIALS = 20
RANK = {"ALLOW": 0, "MASK": 1, "HOLD": 2, "BLOCK": 3}
NETWORK_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
RESTORE_SESSION = "eval-restore"
TARGETS = {
    "detection_rate": 0.90,
    "false_positive_rate": 0.08,
    "context_accuracy": 0.85,
    "injection_resistance": 5,
    "p95_latency_ms_with_model": 150.0,
}

SYNTHETIC_DOCS: tuple[tuple[str, str], ...] = (
    (
        "docs/trial_notes.md",
        "Release notes\n"
        "The staging box uses password = \"Hunter2!demo\" for the admin account.\n"
        "Customer Ana Souza, ana.souza@corpus.test, phone +55-11-99876-4321.\n"
        "Card 5555 5555 5555 4444 was charged for the test order.\n",
    ),
    (
        "config/trial_secrets.ini",
        "[service]\n"
        "AWS_ACCESS_KEY_ID = \"AKIAIOSFODNN7EXAMPLE\"\n"
        "API_KEY = \"api_key_mock_token_for_tests_123\"\n"
        "endpoint = https://internal.example.net:8443\n",
    ),
    (
        "docs/trial_roster.md",
        "Team roster\n"
        "Meera Iyer, meera.iyer@corpmail.test, employee 4471\n"
        "National id 4567 8912 3455 for payroll.\n"
        "Escalation contact: oncall@corpmail.test\n",
    ),
    (
        "config/trial_db.ini",
        "[database]\n"
        "url = postgres://analyst:Br1dgePass99@db.internal:5432/reporting\n"
        "jwt = eyJhbGciOiJIUzI1NiJ9.Qwb8DwkNhFdnXsiVpzz63Ffk.CzJr4i0B3JrTAwR4y9ojfljo\n",
    ),
    (
        "docs/trial_finance.md",
        "Quarterly notes\n"
        "Salary band for the platform team starts at 1,850,000 INR.\n"
        "Vendor invoice card 4111 1111 1111 1111 declined twice.\n"
        "Approver: ravi.menon@corpmail.test\n",
    ),
    (
        "config/trial_cloud.ini",
        "[cloud]\n"
        "secret_key = \"wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\"\n"
        "session_token = \"FwoGZXIvYXdzEBEaDExampleSessionTokenValue123\"\n"
        "region = ap-south-1\n",
    ),
    (
        "docs/trial_support.md",
        "Support macros\n"
        "Reset link sent to customer nisha.rao@corpus.test.\n"
        "Escalation: the api key sk_test_NOTAREALKEY123456 was rotated.\n"
        "Do not paste secrets in tickets.\n",
    ),
    (
        "config/trial_legacy.ini",
        "[legacy]\n"
        "db_password = \"OldCastle42!\"\n"
        "private_key = \"-----BEGIN RSA PRIVATE KEY-----\\nMEQCIFakeKeyMaterialForTrials\\n-----END RSA PRIVATE KEY-----\"\n"
        "host = legacy-db.internal\n",
    ),
)


# --------------------------------------------------------------------------- dataset
def load_dataset(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def write_fixture(workspace: Path, item: dict[str, Any]) -> None:
    """Put the item's content at its target so the read sees exactly it."""
    if item.get("connector") != "filesystem":
        return
    target = str(item.get("target", ""))
    if not target or target.startswith("/") or ".." in Path(target).parts:
        raise ValueError(f"unsafe filesystem target in the dataset: {target!r}")
    path = workspace / target
    if path.exists() and path.is_dir():
        raise ValueError(f"filesystem target is a directory: {target!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(item.get("content", "")), encoding="utf-8")


def prepare_database(workspace: Path) -> None:
    """Add the unprotected audit_notes table the SQL items query."""
    connection = sqlite3.connect(workspace / "business.db")
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS audit_notes ("
            "id INTEGER PRIMARY KEY, body TEXT NOT NULL, category TEXT NOT NULL)"
        )
        connection.execute("DELETE FROM audit_notes")
        connection.executemany(
            "INSERT INTO audit_notes (body, category) VALUES (?, ?)",
            [
                ("Deployment finished at 09:15 UTC.", "ops"),
                ("Weekly report exported for the team.", "report"),
                ("No incidents recorded this week.", "ops"),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def split_network(value: str) -> tuple[str, str]:
    parts = value.split(None, 1)
    if len(parts) == 2 and parts[0].upper() in NETWORK_METHODS:
        return parts[0].upper(), parts[1]
    return "GET", value


def build_request(item: dict[str, Any], session_id: str) -> ActionRequest:
    connector = str(item.get("connector", "filesystem"))
    target = str(item.get("target", ""))
    content = str(item.get("content", ""))
    if connector == "filesystem":
        return ActionRequest(
            session_id=session_id,
            connector=ConnectorName.FILESYSTEM,
            action="read_file",
            target=target,
        )
    if connector == "database":
        table = target.removeprefix("db:")
        sql = content.strip()
        return ActionRequest(
            session_id=session_id,
            connector=ConnectorName.DATABASE,
            action="db_query",
            target=table,
            params={"sql": sql},
        )
    if connector == "command":
        return ActionRequest(
            session_id=session_id,
            connector=ConnectorName.COMMAND,
            action="run_command",
            target=content,
            params={"command": content},
        )
    if connector == "network":
        method, url = split_network(content or target)
        return ActionRequest(
            session_id=session_id,
            connector=ConnectorName.NETWORK,
            action="http_request",
            target=url,
            params={"method": method, "url": url, "headers": {}, "body": ""},
        )
    raise ValueError(f"unknown connector in the dataset: {connector}")


async def evaluate_item(gateway: Any, item: dict[str, Any], session_id: str) -> dict[str, Any]:
    write_fixture(gateway.settings.workspace_abs, item)
    request = build_request(item, session_id)
    started = time.perf_counter()
    result = await gateway.pipeline.handle(request)
    wall_ms = (time.perf_counter() - started) * 1000
    event = gateway.audit.get_event(result.event_id)
    return {
        "status": str(result.status),
        "decision": str(event.decision) if event else "unknown",
        "outcome": str(event.final_outcome) if event else "unknown",
        "reason_codes": list(event.reason_codes) if event else [],
        "latency_ms": float(event.latency_ms) if event else wall_ms,
        "wall_ms": round(wall_ms, 3),
    }


# --------------------------------------------------------------------------- metrics
def compute_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    sensitive = [item for item in results if item["expected"] != "ALLOW"]
    benign = [item for item in results if item["expected"] == "ALLOW"]

    detected = [
        item
        for item in sensitive
        if RANK.get(item["decision"], 0) >= RANK.get(item["expected"], 0)
    ]
    detection_rate = len(detected) / len(sensitive) if sensitive else 0.0

    false_positives = [item for item in benign if item["decision"] != "ALLOW"]
    false_positive_rate = len(false_positives) / len(benign) if benign else 0.0

    predicted_positive = [item for item in results if item["decision"] != "ALLOW"]
    true_positive = [item for item in predicted_positive if item["expected"] != "ALLOW"]
    precision = len(true_positive) / len(predicted_positive) if predicted_positive else 0.0
    recall = detection_rate
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    pairs: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        pair_id = item.get("pair_id")
        if pair_id:
            pairs.setdefault(str(pair_id), []).append(item)
    complete = [members for members in pairs.values() if len(members) == 2]
    context_correct = [
        members
        for members in complete
        if all(member["decision"] == member["expected"] for member in members)
    ]
    context_accuracy = len(context_correct) / len(complete) if complete else 0.0

    exact = [item for item in results if item["decision"] == item["expected"]]
    decision_accuracy = len(exact) / total if total else 0.0

    return {
        "n": total,
        "n_sensitive": len(sensitive),
        "n_benign": len(benign),
        "n_pairs": len(complete),
        "detection_rate": round(detection_rate, 4),
        "false_positive_rate": round(false_positive_rate, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "context_accuracy": round(context_accuracy, 4),
        "decision_accuracy": round(decision_accuracy, 4),
        "detected": len(detected),
        "false_positives": len(false_positives),
    }


def per_category(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    categories: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        categories.setdefault(item["category"], []).append(item)
    breakdown: dict[str, dict[str, Any]] = {}
    for name in sorted(categories):
        items = categories[name]
        sensitive = [item for item in items if item["expected"] != "ALLOW"]
        detected = [
            item
            for item in sensitive
            if RANK.get(item["decision"], 0) >= RANK.get(item["expected"], 0)
        ]
        exact = [item for item in items if item["decision"] == item["expected"]]
        breakdown[name] = {
            "n": len(items),
            "exact": len(exact),
            "decision_accuracy": round(len(exact) / len(items), 4) if items else 0.0,
            "n_sensitive": len(sensitive),
            "detected": len(detected),
            "detection_rate": round(len(detected) / len(sensitive), 4) if sensitive else None,
            "misses": [item["id"] for item in items if item["decision"] != item["expected"]],
        }
    return breakdown


def failure_cases(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases = []
    for item in results:
        if item["decision"] == item["expected"]:
            continue
        cases.append(
            {
                "id": item["id"],
                "category": item["category"],
                "expected": item["expected"],
                "got": item["decision"],
                "reason_codes": item.get("reason_codes", []),
                "note": item.get("note", ""),
            }
        )
    return cases


def latency_stats(latencies: list[float]) -> dict[str, Any]:
    if not latencies:
        return {"n": 0}
    ordered = sorted(latencies)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "n": len(ordered),
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[index], 3),
        "mean_ms": round(statistics.fmean(ordered), 3),
        "max_ms": round(ordered[-1], 3),
        "method": "nearest-rank p95 over event.latency_ms (excludes human wait and connector execution)",
    }


# --------------------------------------------------------------------------- restore
def sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _edit_variant(text: str, variant: int, label: str) -> tuple[str, str] | None:
    """Return (edited_text, marker) for one edit-after-mask trial."""
    marker = f"# eval edit {label}"
    if variant == 0:
        lines = text.splitlines(keepends=True)
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if PLACEHOLDER_TOKEN.search(line):
                continue
            if "MASKED_LOGIC_BLOCK_" in line:
                continue
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f"{line.rstrip(chr(10))} {marker}{newline}"
            return "".join(lines), marker
    suffix = f"\n{marker}\n"
    return text + suffix, marker


def restore_trials(workspace: Path) -> dict[str, Any]:
    """Run edit-after-mask trials: untouched masked blocks must return identical."""
    connection = sqlite3.connect(":memory:")
    store = MaskMapStore(connection, threading.Lock())

    sources: list[tuple[str, str, str]] = []
    for rel in (
        ".env",
        "README.md",
        "config/settings.py",
        "src/app.py",
        "src/utils/format.py",
        "src/auth/login.py",
        "src/pricing/engine.py",
        "tests/fixtures/cards.py",
        "docs/guide.md",
    ):
        path = workspace / rel
        if path.is_file():
            kind = "python" if rel.endswith(".py") else "text"
            sources.append((rel, path.read_text(encoding="utf-8", errors="replace"), kind))
    sources.extend((rel, text, "text") for rel, text in SYNTHETIC_DOCS)

    trials: list[dict[str, Any]] = []
    for rel, raw, kind in sources:
        if len(trials) >= RESTORE_TRIALS:
            break
        detections = detect(raw, DetectContext(target=rel))
        mask_logic = False
        if kind == "python":
            try:
                mask_logic = bool(find_blocks(raw, 3))
            except SyntaxError:
                mask_logic = False
        plan = MaskPlan(spans=list(detections), mask_logic=mask_logic)
        result = mask_content(
            raw,
            kind=kind,
            scope=rel,
            session_id=RESTORE_SESSION,
            detections=detections,
            plan=plan,
            store=store,
            base_sha256=sha256_text(raw),
            min_body_lines=3,
        )
        if not result.map_entries:
            continue
        for variant in (0, 1):
            if len(trials) >= RESTORE_TRIALS:
                break
            edit = _edit_variant(result.sanitized, variant, f"{len(trials):02d}")
            if edit is None:
                continue
            edited, marker = edit
            restored = restore_file(
                edited,
                rel_path=rel,
                session_id=RESTORE_SESSION,
                store=store,
                current_sha256=sha256_text(raw),
            )
            problems: list[str] = []
            entries = store.entries_for_scope(RESTORE_SESSION, rel)
            for entry in entries:
                if entry.placeholder in edited and entry.original not in restored.content:
                    problems.append(f"{entry.placeholder} not restored byte-identically")
            if marker not in restored.content:
                problems.append("the human edit did not survive the restore")
            if "unknown_placeholder" in restored.flags:
                problems.append(f"leftover placeholder (flags={restored.flags})")
            trials.append(
                {
                    "target": rel,
                    "edit": "line" if variant == 0 else "append",
                    "masked_blocks": len(entries),
                    "ok": not problems,
                    "problems": problems,
                }
            )

    passed = sum(1 for trial in trials if trial["ok"])
    return {
        "passed": passed,
        "total": len(trials),
        "trials": trials,
    }


# --------------------------------------------------------------------------- attacks
def run_attacks() -> dict[str, Any]:
    scripts = sorted((ROOT / "eval" / "attacks").glob("attack_*.py"))
    details: list[dict[str, Any]] = []
    stopped = 0
    for script in scripts:
        try:
            process = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=240,
                env=dict(os.environ),
            )
            output = process.stdout + process.stderr
            verdict = "stopped" if re.search(r"RESULT:\s*stopped\b", output) else "not stopped"
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            output, verdict, returncode = "timed out", "not stopped", -1
        if verdict == "stopped":
            stopped += 1
        details.append(
            {
                "attack": script.name,
                "verdict": verdict,
                "exit_code": returncode,
                "tail": output.strip().splitlines()[-6:],
            }
        )
    return {"total": len(scripts), "stopped": stopped, "details": details}


# --------------------------------------------------------------------------- output
def fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def mismatch_patterns(failures: list[dict[str, Any]]) -> list[tuple[str, int, str]]:
    """Group failures by (expected, got) and describe what the difference means."""
    groups: dict[tuple[str, str], int] = {}
    for case in failures:
        key = (case["expected"], case["got"])
        groups[key] = groups.get(key, 0) + 1
    patterns: list[tuple[str, int, str]] = []
    for (expected, got), count in sorted(groups.items(), key=lambda item: -item[1]):
        if RANK.get(got, 0) > RANK.get(expected, 0):
            why = (
                f"detected and decided more strictly than expected ({got} > {expected}); "
                "the scoring placed it above the next threshold"
            )
        elif RANK.get(got, 0) < RANK.get(expected, 0):
            why = (
                f"not caught strongly enough ({got} < {expected}); the detectors and the "
                "content model scored it below the expected threshold"
            )
        else:
            why = "same severity but a different decision"
        patterns.append((f"expected {expected}, got {got}", count, why))
    return patterns


def build_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    baseline = payload["baseline"]
    latency = payload["latency"]
    restore = payload["restore"]
    injection = payload["injection"]
    model_used = payload.get("content_model_available")

    latency_cell = (
        f"{latency['median_ms']} / {latency['p95_ms']} over {latency['n']} calls"
        if latency.get("n")
        else "not measured"
    )
    lines = [
        "# RepoShield evaluation results",
        "",
        "Every number below was produced by `make eval` on this dataset. The",
        "content model was trained on synthetic data only; the evaluation",
        "dataset is hand-written and was never used for training.",
        "",
        "| Metric | RepoShield | Keyword baseline |",
        "|---|---|---|",
        f"| Detection rate | {summary['detection_rate']} | {baseline['detection_rate']} |",
        f"| False-positive rate | {summary['false_positive_rate']} | {baseline['false_positive_rate']} |",
        f"| Precision | {summary['precision']} | {baseline['precision']} |",
        f"| F1 | {summary['f1']} | {baseline['f1']} |",
        f"| Context accuracy ({summary['n_pairs']} pairs) | {summary['context_accuracy']} | {baseline['context_accuracy']} |",
        f"| Decision accuracy | {summary['decision_accuracy']} | {baseline['decision_accuracy']} |",
        f"| Injection resistance | {injection['stopped']}/{injection['total']} | n/a |",
        f"| Latency median / p95 (ms) | {latency_cell} | n/a |",
        f"| Restore correctness | {restore['passed']}/{restore['total']} | n/a |",
        "",
        "## Targets",
        "",
        f"- Detection rate >= {TARGETS['detection_rate']}: "
        f"{'MET' if summary['detection_rate'] >= TARGETS['detection_rate'] else 'MISSED'} "
        f"({summary['detection_rate']})",
        f"- False-positive rate <= {TARGETS['false_positive_rate']}: "
        f"{'MET' if summary['false_positive_rate'] <= TARGETS['false_positive_rate'] else 'MISSED'} "
        f"({summary['false_positive_rate']})",
        f"- Context accuracy >= {TARGETS['context_accuracy']}: "
        f"{'MET' if summary['context_accuracy'] >= TARGETS['context_accuracy'] else 'MISSED'} "
        f"({summary['context_accuracy']})",
        f"- Injection resistance {TARGETS['injection_resistance']}/5: "
        f"{'MET' if injection['stopped'] == TARGETS['injection_resistance'] else 'MISSED'} "
        f"({injection['stopped']}/{injection['total']})",
        f"- p95 latency < {TARGETS['p95_latency_ms_with_model']} ms with the content model: "
        + (
            f"MET ({latency['p95_ms']} ms)"
            if latency.get("n") and latency["p95_ms"] < TARGETS["p95_latency_ms_with_model"]
            else f"MISSED ({latency.get('p95_ms', 'n/a')} ms)"
        ),
        "",
        f"Content model available during this run: {'yes' if model_used else 'no'}.",
        "",
        "## Per-category breakdown",
        "",
        "| Category | Items | Decision accuracy | Sensitive | Detected | Detection rate |",
        "|---|---|---|---|---|---|",
    ]
    for name, stats in payload["per_category"].items():
        detection = stats["detection_rate"]
        lines.append(
            f"| {name} | {stats['n']} | {stats['decision_accuracy']} | "
            f"{stats['n_sensitive']} | {stats['detected']} | "
            f"{detection if detection is not None else 'n/a'} |"
        )

    lines.extend(
        [
            "",
            "## Failure cases",
            "",
        ]
    )
    failures = payload["failures"]
    if not failures:
        lines.append("No item was decided differently from its expected decision.")
    else:
        lines.append("| Item | Category | Expected | Got | Reasons | Note |")
        lines.append("|---|---|---|---|---|---|")
        for case in failures:
            reasons = ", ".join(case["reason_codes"]) or "-"
            note = case["note"].replace("|", "\\|")
            lines.append(
                f"| {case['id']} | {case['category']} | {case['expected']} | "
                f"{case['got']} | {reasons} | {note} |"
            )

    if failures:
        lines.extend(["", "### Failure patterns", ""])
        for label, count, why in mismatch_patterns(failures):
            lines.append(f"- {count} items {label}: {why}.")

    lines.extend(
        [
            "",
            "## Injection attacks",
            "",
            "| Attack | Verdict |",
            "|---|---|",
        ]
    )
    for detail in injection["details"]:
        lines.append(f"| {detail['attack']} | {detail['verdict']} |")

    lines.extend(
        [
            "",
            "## Restore trials",
            "",
            f"{restore['passed']} of {restore['total']} edit-after-mask trials returned every "
            "untouched masked block byte-identically.",
            "",
        ]
    )
    for trial in restore["trials"]:
        if not trial["ok"]:
            lines.append(f"- FAILED {trial['target']} ({trial['edit']}): {'; '.join(trial['problems'])}")

    lines.extend(
        [
            "",
            "## Method",
            "",
            "- Each dataset item runs through the real pipeline with its own session id so the",
            "  kill-switch window of one item cannot affect another.",
            "- Filesystem items are written to a throw-away seeded workspace before the read, so",
            "  the gateway sees exactly the content the item describes.",
            "- Held items time out after one second in this harness; a timeout keeps the HOLD",
            "  decision, which is what the decision metrics compare.",
            "- Latency is `event.latency_ms` (the gateway excludes human wait and connector",
            f"  execution) over {payload['latency'].get('n', 0)} calls.",
            "",
        ]
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- main
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the RepoShield evaluation.")
    parser.add_argument("--dataset", default=str(ROOT / "eval" / "dataset.jsonl"))
    parser.add_argument("--results-dir", default=str(ROOT / "eval" / "results"))
    parser.add_argument("--skip-attacks", action="store_true",
                        help="skip the five injection-attack subprocesses")
    parser.add_argument("--skip-restore", action="store_true",
                        help="skip the restore-correctness trials")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    dataset_path = Path(args.dataset)
    items = load_dataset(dataset_path)
    if not items:
        print(f"ERROR: no items in {dataset_path}", file=sys.stderr)
        return 1
    print(f"Loaded {len(items)} items from {dataset_path}")

    tmp_path = Path(tempfile.mkdtemp(prefix="eval_"))
    gateway = make_gateway(tmp_path, approval_timeout=1)
    prepare_database(gateway.settings.workspace_abs)
    model_available = bool(gateway.pipeline.content_model.available)
    latencies: list[float] = []
    results: list[dict[str, Any]] = []
    try:
        print("Pass 1: running every item through the pipeline ...")
        for index, item in enumerate(items):
            outcome = await evaluate_item(gateway, item, f"eval-{index}")
            latencies.append(outcome["latency_ms"])
            expected = str(item.get("expected_decision", "ALLOW"))
            results.append(
                {
                    "id": item.get("id", f"item-{index}"),
                    "category": item.get("category", "unknown"),
                    "connector": item.get("connector"),
                    "expected": expected,
                    "decision": outcome["decision"],
                    "outcome": outcome["outcome"],
                    "reason_codes": outcome["reason_codes"],
                    "status": outcome["status"],
                    "latency_ms": outcome["latency_ms"],
                    "note": item.get("note", ""),
                    "pair_id": item.get("pair_id"),
                    "exact": outcome["decision"] == expected,
                }
            )
        held = {index for index, item in enumerate(results) if item["decision"] == "HOLD"}
        print(f"  {len(results)} calls, {len(held)} held, {len(latencies)} latencies")

        replay = 0
        while len(latencies) < MIN_LATENCY_CALLS and replay < 8:
            replay += 1
            added = 0
            for index, item in enumerate(items):
                if index in held or item.get("connector") == "network":
                    continue
                outcome = await evaluate_item(gateway, item, f"eval-r{replay}-{index}")
                latencies.append(outcome["latency_ms"])
                added += 1
                if outcome["decision"] != results[index]["decision"]:
                    results[index]["unstable"] = results[index].get("unstable", 0) + 1
            print(f"  latency pass {replay}: +{added} calls (total {len(latencies)})")
            if added == 0:
                break

        summary = compute_metrics(results)
        category_breakdown = per_category(results)
        failures = failure_cases(results)
        latency = latency_stats(latencies)
        baseline_results = run_baseline(dataset_path)
        baseline = summarize_baseline(baseline_results)
        print(
            f"  detection={summary['detection_rate']} fpr={summary['false_positive_rate']} "
            f"f1={summary['f1']} accuracy={summary['decision_accuracy']} "
            f"latency median={latency.get('median_ms')}ms p95={latency.get('p95_ms')}ms "
            f"over {latency.get('n')} calls"
        )
    finally:
        gateway.audit.close()
        shutil.rmtree(tmp_path, ignore_errors=True)

    # Restore trials run against a fresh seeded workspace, not the evaluated one.
    if args.skip_restore:
        restore = {"passed": 0, "total": 0, "trials": [], "skipped": True}
        print("Restore trials skipped (--skip-restore)")
    else:
        reference = Path(tempfile.mkdtemp(prefix="eval_seed_"))
        try:
            from scripts.seed_workspace import seed_workspace

            seed_workspace(reference)
            restore = restore_trials(reference)
        finally:
            shutil.rmtree(reference, ignore_errors=True)
        print(f"Restore trials: {restore['passed']}/{restore['total']} byte-identical")

    if args.skip_attacks:
        injection = {"total": 5, "stopped": 0, "details": [], "skipped": True}
        print("Injection attacks skipped (--skip-attacks)")
    else:
        print("Running the five injection attacks ...")
        injection = run_attacks()
        print(f"Injection resistance: {injection['stopped']}/{injection['total']}")

    payload = {
        "dataset": str(dataset_path),
        "items": len(items),
        "content_model_available": model_available,
        "summary": summary,
        "baseline": baseline,
        "latency": latency,
        "restore": restore,
        "injection": injection,
        "per_category": category_breakdown,
        "failures": failures,
        "results": results,
    }

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (results_dir / "results.md").write_text(build_markdown(payload), encoding="utf-8")
    print(f"Wrote {results_dir / 'results.json'} and {results_dir / 'results.md'}")
    print(
        f"Failures: {len(failures)} of {len(items)} items decided differently from expected"
    )
    return 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
