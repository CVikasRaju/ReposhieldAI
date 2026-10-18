"""Train the offline sensitivity model and report held-out accuracy."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joblib  # noqa: E402
from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.model_selection import train_test_split  # noqa: E402
from sklearn.pipeline import FeatureUnion, Pipeline  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "eval" / "training_data.jsonl"
DEFAULT_OUT = ROOT / "backend" / "models" / "content_model.joblib"


def load_rows(path: Path) -> tuple[list[str], list[int]]:
    """Load and validate training examples."""
    texts: list[str] = []
    labels: list[int] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        text = str(row["text"])
        context = str(row.get("context", ""))
        label = int(row["label"])
        if label not in (0, 1):
            raise ValueError(f"line {line_number}: label must be 0 or 1")
        texts.append(f"{context} | {text[:4000]}")
        labels.append(label)
    return texts, labels


def build_pipeline() -> Pipeline:
    """Create the TF-IDF plus logistic regression classifier."""
    features = FeatureUnion(
        [
            ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True)),
        ]
    )
    return Pipeline(
        [
            ("features", features),
            ("classifier", LogisticRegression(max_iter=2000, random_state=7, class_weight="balanced")),
        ]
    )


def main() -> int:
    """Train, evaluate on a held-out split and save the model."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.data.is_file():
        print(f"Training data not found: {args.data}", file=sys.stderr)
        return 1
    texts, labels = load_rows(args.data)
    if len(texts) < 400:
        print(f"Need at least 400 examples, found {len(texts)}", file=sys.stderr)
        return 1

    x_train, x_test, y_train, y_test = train_test_split(
        texts, labels, test_size=0.2, random_state=7, stratify=labels
    )
    pipeline = build_pipeline()
    pipeline.fit(x_train, y_train)
    accuracy = float(pipeline.score(x_test, y_test))
    predictions = pipeline.predict(x_test)
    true_positives = sum(1 for pred, actual in zip(predictions, y_test, strict=True) if pred == 1 and actual == 1)
    false_negatives = sum(1 for pred, actual in zip(predictions, y_test, strict=True) if pred == 0 and actual == 1)
    recall = true_positives / max(1, true_positives + false_negatives)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, args.out)

    sample = x_test[:50]
    started = time.perf_counter()
    for text in sample:
        pipeline.predict_proba([text])
    average_ms = (time.perf_counter() - started) * 1000 / max(1, len(sample))

    print(f"Trained on {len(x_train)} examples; held out {len(x_test)}.")
    print(f"Held-out accuracy: {accuracy:.4f}; recall on sensitive: {recall:.4f}")
    print(f"Average inference: {average_ms:.2f} ms per sample")
    print(f"Saved model to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
