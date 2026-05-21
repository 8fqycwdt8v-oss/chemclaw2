"""Tests for `mcp_tabular` ML tools (fit_score, predict_holdout)."""
from __future__ import annotations

import pytest

pytest.importorskip("mcp_tabular")
pytest.importorskip("sklearn")

import numpy as np  # noqa: E402

from mcp_tabular import ml, tables  # noqa: E402


def _classification_table(n: int = 120, seed: int = 0):
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        x1 = float(rng.normal(0, 1))
        x2 = float(rng.normal(0, 1))
        group = "P" if rng.random() < 0.5 else "Q"
        # Label is deterministic in (x1, x2) — easy classification.
        y = int(x1 + x2 > 0)
        rows.append([x1, x2, group, y])
    return ["x1", "x2", "group", "y"], rows


def _regression_table(n: int = 120, seed: int = 0):
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        x1 = float(rng.normal(0, 1))
        x2 = float(rng.normal(0, 1))
        # y = 2*x1 + x2 + small noise
        y = float(2.0 * x1 + x2 + rng.normal(0, 0.1))
        rows.append([x1, x2, y])
    return ["x1", "x2", "y"], rows


def test_fit_score_rf_classification():
    cols, rows = _classification_table()
    df = tables.load_inline(cols, rows)
    result = ml.fit_score(df, target="y", features=["x1", "x2", "group"],
                          model="rf", task="classification", cv=5)
    assert result["task"] == "classification"
    assert result["n_features"] == 3
    assert result["metrics"]["accuracy"] is not None
    assert result["metrics"]["accuracy"] > 0.85
    assert "roc_auc" in result["metrics"]
    assert len(result["per_fold"]) == 5
    assert result["feature_importances"] is not None


def test_fit_score_linear_regression():
    cols, rows = _regression_table()
    df = tables.load_inline(cols, rows)
    result = ml.fit_score(df, target="y", features=None,
                          model="linear", task="regression", cv=5)
    assert result["task"] == "regression"
    assert result["metrics"]["r2"] > 0.95
    assert result["metrics"]["rmse"] is not None


def test_fit_score_rejects_bad_model():
    cols, rows = _classification_table()
    df = tables.load_inline(cols, rows)
    with pytest.raises(ValueError, match="model must be"):
        ml.fit_score(df, target="y", features=["x1"], model="nope", task="classification")


def test_fit_score_rejects_bad_task():
    cols, rows = _classification_table()
    df = tables.load_inline(cols, rows)
    with pytest.raises(ValueError, match="task must be"):
        ml.fit_score(df, target="y", features=["x1"], model="rf", task="nope")


def test_predict_holdout_classification():
    cols, rows = _classification_table(n=200)
    df = tables.load_inline(cols, rows)
    result = ml.predict_holdout(df, target="y", features=["x1", "x2", "group"],
                                model="logreg", task="classification", test_size=0.25)
    assert result["metrics"]["accuracy"] > 0.8
    assert len(result["predictions"]) == result["n_test"]
    assert len(result["y_true"]) == result["n_test"]


def test_predict_holdout_caps_predictions():
    cols, rows = _classification_table(n=200)
    df = tables.load_inline(cols, rows)
    result = ml.predict_holdout(df, target="y", features=["x1"],
                                model="rf", task="classification",
                                test_size=0.5, max_predictions=10)
    assert len(result["predictions"]) == 10
    assert result["truncated"] is True
