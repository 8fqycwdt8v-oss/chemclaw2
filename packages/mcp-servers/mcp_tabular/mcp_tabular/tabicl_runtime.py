"""Lazy TabICL wrapper.

TabICL is a pretrained tabular foundation model (in-context learning).
The dep (and torch + the pretrained weights) are heavy, so they live in
an optional `[tabicl]` extra and are imported on first call only.

CI installs the base server without the `tabicl` extra, so the import
guard below is exercised every CI run. Production deployments that
want TabICL install with `pip install 'mcp-tabular[tabicl]'`.

Subprocess-kill note (CLAUDE.md §52): once torch loads, the process may
ignore SIGTERM during native inference. The agent runner's 2s
`asyncio.wait_for` + SIGKILL backstop handles this; no signal handlers
here.
"""
from __future__ import annotations

import importlib.util
import logging
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger("mcp_tabular.tabicl")

_classes: dict[str, Any] | None = None
_load_error: str | None = None


def is_installed() -> bool:
    """Cheap check — does NOT import torch."""
    return importlib.util.find_spec("tabicl") is not None and \
        importlib.util.find_spec("torch") is not None


def get_classes() -> dict[str, Any]:
    """Import + cache tabicl's classifier/regressor classes. First call
    pays the torch+tabicl import cost; subsequent calls are constant-time.

    Raises RuntimeError if the [tabicl] extra is not installed.
    """
    global _classes, _load_error
    if _classes is not None:
        return _classes
    if _load_error is not None:
        raise RuntimeError(_load_error)
    try:
        import torch  # noqa: F401
        from tabicl import TabICLClassifier, TabICLRegressor
    except ImportError as e:
        _load_error = (
            "tabicl extra not installed. Install with: "
            "pip install 'mcp-tabular[tabicl]'"
        )
        log.warning("tabicl_import_failed", extra={"error": str(e)})
        raise RuntimeError(_load_error) from e
    _classes = {"clf": TabICLClassifier, "reg": TabICLRegressor}
    log.info("tabicl_imported")
    return _classes


def predict(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    target: str,
    task: str,
    max_train_rows: int = 10_000,
    max_predictions: int = 10_000,
) -> dict:
    """Run TabICL on a train/test pair. Returns predictions + (for
    classification) probabilities."""
    if task not in ("classification", "regression"):
        raise ValueError(f"task must be classification|regression, got {task!r}")
    if target not in train_df.columns:
        raise ValueError(f"target {target!r} not in train_df")
    if target in test_df.columns:
        # Test target is allowed but ignored — drop it for prediction.
        test_df = test_df.drop(columns=[target])

    train_df = train_df.dropna(subset=[target])
    if len(train_df) > max_train_rows:
        train_df = train_df.head(max_train_rows)
    if len(train_df) < 2:
        raise ValueError("need at least 2 training rows")

    classes = get_classes()
    Estimator = classes["clf"] if task == "classification" else classes["reg"]
    # Fresh estimator per call: TabICL's `.fit(...).predict(...)` is in-context,
    # so the train data is the only state. Keeping the imported classes hot
    # avoids paying the torch import + weights load on every call.
    est = Estimator()

    X_train = train_df.drop(columns=[target])
    y_train = train_df[target]
    X_test = test_df

    est.fit(X_train, y_train)
    preds = est.predict(X_test)
    out: dict[str, Any] = {
        "task": task,
        "n_train": int(len(train_df)),
        "n_test": int(len(X_test)),
        "n_features": int(X_train.shape[1]),
        "predictions": _to_jsonable_list(preds, max_predictions),
        "truncated": len(preds) > max_predictions,
    }

    if task == "classification" and hasattr(est, "predict_proba"):
        proba = est.predict_proba(X_test)
        out["probabilities"] = [
            [round(float(v), 6) for v in row] for row in proba[:max_predictions]
        ]
        out["classes"] = [str(c) for c in getattr(est, "classes_", [])]

    log.info(
        "tabicl_predicted",
        extra={"task": task, "n_train": int(len(train_df)), "n_test": int(len(X_test))},
    )
    return out


def _to_jsonable_list(arr, cap: int) -> list:
    out = []
    for v in arr[:cap]:
        if isinstance(v, (np.integer,)):
            out.append(int(v))
        elif isinstance(v, (np.floating,)):
            f = float(v)
            out.append(None if np.isnan(f) else round(f, 6))
        elif isinstance(v, np.bool_):
            out.append(bool(v))
        else:
            out.append(v)
    return out


def status() -> dict:
    """Cheap status check. Does not import torch/tabicl."""
    return {
        "installed": is_installed(),
        "loaded": _classes is not None,
        "load_error": _load_error,
    }
