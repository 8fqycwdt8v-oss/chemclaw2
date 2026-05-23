"""Simple ML over inline tables: fit/CV scoring + train/test holdout.

Only sklearn classics — Logistic/Linear, RandomForest, GradientBoosting.
No model persistence (v1). No hyperparameter search.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)
from sklearn.model_selection import KFold, StratifiedKFold, cross_validate, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

_MODELS_CLF = {
    "logreg": lambda rs: LogisticRegression(max_iter=1000, random_state=rs),
    "rf": lambda rs: RandomForestClassifier(n_estimators=100, random_state=rs, n_jobs=1),
    "gbm": lambda rs: GradientBoostingClassifier(random_state=rs),
}
_MODELS_REG = {
    "linear": lambda rs: LinearRegression(),
    "rf": lambda rs: RandomForestRegressor(n_estimators=100, random_state=rs, n_jobs=1),
    "gbm": lambda rs: GradientBoostingRegressor(random_state=rs),
}


def _round(x: Any) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if np.isnan(f) or np.isinf(f):
        return None
    return round(f, 6)


def _build_pipeline(model_key: str, task: str, random_state: int,
                    numeric_cols: list[str], categorical_cols: list[str]) -> Pipeline:
    if task == "classification":
        if model_key not in _MODELS_CLF:
            raise ValueError(f"model must be one of {sorted(_MODELS_CLF)}, got {model_key!r}")
        est = _MODELS_CLF[model_key](random_state)
    elif task == "regression":
        if model_key not in _MODELS_REG:
            raise ValueError(f"model must be one of {sorted(_MODELS_REG)}, got {model_key!r}")
        est = _MODELS_REG[model_key](random_state)
    else:
        raise ValueError(f"task must be classification|regression, got {task!r}")

    transformers = []
    if numeric_cols:
        # Tree-based models don't need scaling, but it's cheap and keeps the
        # pipeline shape consistent across models.
        transformers.append(("num", Pipeline([
            ("imputer", SimpleImputer(strategy="mean")),
            ("scaler", StandardScaler(with_mean=model_key in ("logreg", "linear"))),
        ]), numeric_cols))
    if categorical_cols:
        transformers.append(("cat", Pipeline([
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("ohe", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]), categorical_cols))
    pre = ColumnTransformer(transformers, remainder="drop")
    return Pipeline([("pre", pre), ("est", est)])


def _split_columns(df: pd.DataFrame, features: list[str]) -> tuple[list[str], list[str]]:
    numeric_cols = [c for c in features if pd.api.types.is_numeric_dtype(df[c])]
    categorical_cols = [c for c in features if c not in numeric_cols]
    return numeric_cols, categorical_cols


def _resolve_features(df: pd.DataFrame, target: str, features: list[str] | None) -> list[str]:
    if target not in df.columns:
        raise ValueError(f"target column not found: {target!r}")
    if features is None:
        features = [c for c in df.columns if c != target]
    else:
        missing = set(features) - set(df.columns)
        if missing:
            raise ValueError(f"unknown feature columns: {sorted(missing)}")
        if target in features:
            raise ValueError(f"target {target!r} appears in features")
    if not features:
        raise ValueError("no feature columns available")
    return features


def fit_score(
    df: pd.DataFrame,
    target: str,
    features: list[str] | None,
    model: str,
    task: str,
    cv: int = 5,
    random_state: int = 0,
) -> dict:
    """K-fold CV fit + scoring. Returns per-fold metrics + aggregate +
    feature importances (where the estimator supports it)."""
    feats = _resolve_features(df, target, features)
    # Drop rows where target is null — imputation only applies to features.
    work = df.dropna(subset=[target]).copy()
    if len(work) < cv:
        raise ValueError(f"need at least {cv} rows after dropping null targets (got {len(work)})")

    numeric_cols, categorical_cols = _split_columns(work, feats)
    pipe = _build_pipeline(model, task, random_state, numeric_cols, categorical_cols)

    X = work[feats]
    y = work[target]

    if task == "classification":
        # Stratified only when every class has >= cv members; otherwise plain KFold.
        class_counts = y.value_counts()
        if class_counts.min() >= cv:
            splitter = StratifiedKFold(n_splits=cv, shuffle=True, random_state=random_state)
        else:
            splitter = KFold(n_splits=cv, shuffle=True, random_state=random_state)
        scoring = {"accuracy": "accuracy", "f1_weighted": "f1_weighted"}
        if y.nunique() == 2:
            scoring["roc_auc"] = "roc_auc"
    else:
        splitter = KFold(n_splits=cv, shuffle=True, random_state=random_state)
        scoring = {"r2": "r2", "neg_rmse": "neg_root_mean_squared_error", "neg_mae": "neg_mean_absolute_error"}

    cv_result = cross_validate(
        pipe, X, y, cv=splitter, scoring=scoring,
        return_estimator=True, n_jobs=1, error_score="raise",
    )

    # Aggregate metrics (mean across folds, flipping sign for neg_ scorers).
    metrics: dict[str, float | None] = {}
    per_fold: list[dict[str, float | None]] = []
    for fold_i in range(cv):
        row: dict[str, float | None] = {}
        for key in scoring:
            v = float(cv_result[f"test_{key}"][fold_i])
            if key.startswith("neg_"):
                row[key[4:]] = _round(-v)
            else:
                row[key] = _round(v)
        per_fold.append(row)
    for key in scoring:
        vals = cv_result[f"test_{key}"]
        agg = float(vals.mean())
        if key.startswith("neg_"):
            metrics[key[4:]] = _round(-agg)
        else:
            metrics[key] = _round(agg)

    # Feature importances from the last fitted estimator (rough but useful).
    last_est = cv_result["estimator"][-1].named_steps["est"]
    importances = _extract_importances(last_est, numeric_cols, categorical_cols,
                                       cv_result["estimator"][-1].named_steps["pre"])

    return {
        "task": task,
        "model": model,
        "n_train": int(len(work)),
        "n_features": len(feats),
        "features": feats,
        "cv": cv,
        "metrics": metrics,
        "per_fold": per_fold,
        "feature_importances": importances,
    }


def _extract_importances(est, numeric_cols, categorical_cols, pre) -> dict[str, float] | None:
    try:
        feature_names = list(pre.get_feature_names_out())
    except Exception:
        feature_names = None

    if hasattr(est, "feature_importances_"):
        importances = est.feature_importances_
    elif hasattr(est, "coef_"):
        coef = est.coef_
        if coef.ndim == 2:
            importances = np.abs(coef).mean(axis=0)
        else:
            importances = np.abs(coef)
    else:
        return None

    if feature_names is None or len(feature_names) != len(importances):
        # Fall back to raw indices
        return {f"f{i}": _round(v) for i, v in enumerate(importances)}
    return {n: _round(v) for n, v in zip(feature_names, importances, strict=True)}


def predict_holdout(
    df: pd.DataFrame,
    target: str,
    features: list[str] | None,
    model: str,
    task: str,
    test_size: float = 0.2,
    random_state: int = 0,
    max_predictions: int = 10_000,
) -> dict:
    """Single train/test split. Returns metrics + capped predictions."""
    if not (0.05 <= test_size <= 0.5):
        raise ValueError(f"test_size must be in [0.05, 0.5], got {test_size}")
    feats = _resolve_features(df, target, features)
    work = df.dropna(subset=[target]).copy()
    if len(work) < 10:
        raise ValueError(f"need at least 10 rows after dropping null targets (got {len(work)})")

    numeric_cols, categorical_cols = _split_columns(work, feats)
    pipe = _build_pipeline(model, task, random_state, numeric_cols, categorical_cols)

    X = work[feats]
    y = work[target]
    stratify = y if task == "classification" and y.nunique() > 1 and y.value_counts().min() >= 2 else None
    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=test_size, random_state=random_state, stratify=stratify
    )
    pipe.fit(X_tr, y_tr)
    y_pred = pipe.predict(X_te)

    metrics: dict[str, float | None] = {}
    if task == "classification":
        metrics["accuracy"] = _round(accuracy_score(y_te, y_pred))
        metrics["f1_weighted"] = _round(f1_score(y_te, y_pred, average="weighted"))
        if y.nunique() == 2 and hasattr(pipe.named_steps["est"], "predict_proba"):
            proba = pipe.predict_proba(X_te)[:, 1]
            try:
                metrics["roc_auc"] = _round(roc_auc_score(y_te, proba))
            except ValueError:
                metrics["roc_auc"] = None
    else:
        metrics["r2"] = _round(r2_score(y_te, y_pred))
        metrics["rmse"] = _round(float(np.sqrt(mean_squared_error(y_te, y_pred))))
        metrics["mae"] = _round(mean_absolute_error(y_te, y_pred))

    preds = [_jsonable(v) for v in y_pred.tolist()][:max_predictions]
    truth = [_jsonable(v) for v in y_te.tolist()][:max_predictions]

    return {
        "task": task,
        "model": model,
        "n_train": int(len(X_tr)),
        "n_test": int(len(X_te)),
        "n_features": len(feats),
        "metrics": metrics,
        "predictions": preds,
        "y_true": truth,
        "truncated": len(y_pred) > max_predictions,
    }


def _jsonable(v: Any) -> Any:
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if np.isnan(f) else f
    if isinstance(v, np.bool_):
        return bool(v)
    return v
