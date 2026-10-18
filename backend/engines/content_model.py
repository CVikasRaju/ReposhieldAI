"""Offline content model behind a stable interface."""

from __future__ import annotations

import logging
from pathlib import Path

import joblib

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "content_model.joblib"


class ContentModel:
    """TF-IDF plus logistic regression sensitivity classifier.

    The model is optional: when the artifact is missing the score is 0.0 and a
    warning is logged, so the gateway fails toward rules instead of crashing.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_MODEL_PATH
        self._pipeline = None
        self._warning_logged = False
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            logger.warning("content model not found at %s; content signal will be 0.0", self.path)
            self._warning_logged = True
            return
        try:
            self._pipeline = joblib.load(self.path)
        except Exception as exc:  # pragma: no cover - corrupt model is an environment error
            logger.warning("failed to load content model %s: %s", self.path, exc)
            self._pipeline = None

    @property
    def available(self) -> bool:
        """Return True when a model artifact is loaded."""
        return self._pipeline is not None

    def score(self, text: str, context: str) -> float:
        """Return the probability that the content is sensitive."""
        if self._pipeline is None:
            return 0.0
        sample = f"{context} | {text[:4000]}"
        try:
            probabilities = self._pipeline.predict_proba([sample])[0]
            return float(probabilities[1])
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("content model inference failed: %s", exc)
            return 0.0
