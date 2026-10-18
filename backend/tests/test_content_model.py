"""Content model fail-soft behaviour and inference budget."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from backend.engines.content_model import DEFAULT_MODEL_PATH, ContentModel

ROOT = Path(__file__).resolve().parents[2]


def test_missing_model_returns_zero(tmp_path: Path, caplog) -> None:
    model = ContentModel(tmp_path / "missing.joblib")
    with caplog.at_level(logging.WARNING):
        assert model.score("AKIAIOSFODNN7EXAMPLE", "config/settings.py") == 0.0
    assert not model.available
    assert "content model not found" in caplog.text


def test_training_data_is_large_and_labelled() -> None:
    path = ROOT / "eval" / "training_data.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) >= 400
    labels = {row["label"] for row in rows}
    assert labels == {0, 1}
    assert all(row["text"] and row["context"] for row in rows)


def test_trained_model_scores_and_meets_inference_budget() -> None:
    if not DEFAULT_MODEL_PATH.is_file():
        import pytest

        pytest.skip("run make train-model first")
    model = ContentModel()
    assert model.available
    secret = model.score('AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"', "config/settings.py")
    readme = model.score("# Quick start\nInstall the package and run the demo.", "README.md")
    assert secret > 0.7
    assert readme < secret

    sample = ("db:customers | " + '{"full_name": "Asha Rao", "email": "asha@corp.test"}' * 20)
    started = time.perf_counter()
    for _ in range(50):
        model.score(sample, "db:customers")
    average_ms = (time.perf_counter() - started) * 1000 / 50
    assert average_ms < 20.0
