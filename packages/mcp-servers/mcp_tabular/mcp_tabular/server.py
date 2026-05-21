"""MCP server: stats + simple ML + TabICL primitives over inline tables.

Eight `@mcp.tool()` endpoints, grouped:
  Stats: describe, correlate, hypothesis_test, distribution_check
  ML:    fit_score, predict_holdout
  TabICL: tabicl_predict, tabicl_status

Input is always inline (`columns: list[str]`, `rows: list[list]`); cap at
5_000 rows per call (see tables.MAX_ROWS). For larger data, persist as
an artifact via chemclaw2-tools first and feed in chunks.

TabICL ships behind a `[tabicl]` extra and is lazy-imported on first
call. Without the extra, tabicl_status reports {installed: False} and
tabicl_predict raises RuntimeError with install instructions. CI never
installs the extra, so the lazy path stays the tested path.

Subprocess-kill note (CLAUDE.md §52): once torch loads, the process may
ignore SIGTERM during native inference; the agent runner's 2s
asyncio.wait_for + SIGKILL backstop covers this.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_tabular import ml, stats, tabicl_runtime, tables


class JsonFormatter(logging.Formatter):
    """Emit single-line JSON to stderr. Stdout is reserved for JSON-RPC."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "component": "mcp-tabular",
            "event": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k in ("args", "msg", "name", "exc_info", "exc_text", "stack_info",
                     "lineno", "funcName", "created", "msecs", "relativeCreated",
                     "thread", "threadName", "processName", "process", "filename",
                     "module", "pathname", "levelname", "levelno"):
                continue
            payload[k] = v
        return json.dumps(payload, default=str)


def _configure_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.environ.get("MCP_LOG_LEVEL", "INFO"))
    return logging.getLogger("mcp_tabular")


log = _configure_logging()
mcp = FastMCP("mcp-tabular")


def _timed(tool: str, t0: float, **extra) -> None:
    log.info(tool, extra={"tool": tool, "duration_ms": int((time.monotonic() - t0) * 1000), **extra})


# ----------------------------- Stats tools ---------------------------------


@mcp.tool()
def describe(columns: list[str], rows: list[list[Any]], top_k_categories: int = 5) -> dict:
    """Per-column descriptive stats (mean/std/quartiles for numerics,
    top-K value counts for categoricals). Cap: 5_000 rows.
    """
    t0 = time.monotonic()
    df = tables.load_inline(columns, rows)
    result = stats.describe(df, top_k_categories=top_k_categories)
    _timed("describe", t0, n_rows=int(df.shape[0]), n_cols=int(df.shape[1]))
    return result


@mcp.tool()
def correlate(
    columns: list[str],
    rows: list[list[Any]],
    method: str = "pearson",
    subset: list[str] | None = None,
) -> dict:
    """Pairwise correlation matrix over numeric columns. method ∈
    pearson|spearman|kendall. `subset` restricts to named columns.
    """
    t0 = time.monotonic()
    df = tables.load_inline(columns, rows)
    result = stats.correlate(df, method=method, columns=subset)
    _timed("correlate", t0, n_rows=int(df.shape[0]), n_cols=int(df.shape[1]), method=method)
    return result


@mcp.tool()
def hypothesis_test(
    columns: list[str],
    rows: list[list[Any]],
    test: str,
    x: str,
    group: str | None = None,
) -> dict:
    """Run a hypothesis test. test ∈ ttest_ind|mannwhitney|anova|chi2.

    - ttest_ind / mannwhitney: numeric `x`, binary categorical `group`
    - anova: numeric `x`, categorical `group` (>=2 levels)
    - chi2: categorical `x`, categorical `group` (contingency)
    """
    t0 = time.monotonic()
    df = tables.load_inline(columns, rows)
    result = stats.hypothesis_test(df, test=test, x=x, group=group)
    _timed("hypothesis_test", t0, test=test, n_rows=int(df.shape[0]))
    return result


@mcp.tool()
def distribution_check(
    columns: list[str],
    rows: list[list[Any]],
    column: str,
    test: str = "shapiro",
) -> dict:
    """Normality test on a single numeric column. test ∈ shapiro|ks_normal|anderson."""
    t0 = time.monotonic()
    df = tables.load_inline(columns, rows)
    result = stats.distribution_check(df, column=column, test=test)
    _timed("distribution_check", t0, test=test, column=column, n_rows=int(df.shape[0]))
    return result


# ------------------------------ ML tools -----------------------------------


@mcp.tool()
def fit_score(
    columns: list[str],
    rows: list[list[Any]],
    target: str,
    model: str,
    task: str,
    features: list[str] | None = None,
    cv: int = 5,
    random_state: int = 0,
) -> dict:
    """K-fold cross-validated fit + metrics + feature importances.

    task: classification | regression
    model:
      - classification: logreg | rf | gbm
      - regression:     linear | rf | gbm
    Numeric columns are imputed (mean) + standardized; categoricals are
    imputed (most-frequent) + one-hot encoded.
    """
    t0 = time.monotonic()
    df = tables.load_inline(columns, rows)
    result = ml.fit_score(
        df, target=target, features=features, model=model, task=task,
        cv=cv, random_state=random_state,
    )
    _timed("fit_score", t0, task=task, model=model, n_rows=int(df.shape[0]), cv=cv)
    return result


@mcp.tool()
def predict_holdout(
    columns: list[str],
    rows: list[list[Any]],
    target: str,
    model: str,
    task: str,
    features: list[str] | None = None,
    test_size: float = 0.2,
    random_state: int = 0,
    max_predictions: int = 10_000,
) -> dict:
    """Single train/test split. Returns metrics + capped predictions/y_true
    so the agent can inspect per-row errors.
    """
    t0 = time.monotonic()
    df = tables.load_inline(columns, rows)
    result = ml.predict_holdout(
        df, target=target, features=features, model=model, task=task,
        test_size=test_size, random_state=random_state, max_predictions=max_predictions,
    )
    _timed("predict_holdout", t0, task=task, model=model, n_rows=int(df.shape[0]))
    return result


# ----------------------------- TabICL tools --------------------------------


@mcp.tool()
def tabicl_predict(
    train_columns: list[str],
    train_rows: list[list[Any]],
    test_columns: list[str],
    test_rows: list[list[Any]],
    target: str,
    task: str,
    max_train_rows: int = 10_000,
    max_predictions: int = 10_000,
) -> dict:
    """Tabular foundation model (TabICL) in-context prediction.

    Provide a labeled train set and an unlabeled test set with the same
    feature columns (test set's `target` column, if present, is ignored).
    First call lazy-imports torch + tabicl and downloads pretrained
    weights — slow. Subsequent calls reuse the loaded classes.

    Raises RuntimeError if the [tabicl] extra is not installed; check
    `tabicl_status()` first if unsure.
    """
    t0 = time.monotonic()
    train_df = tables.load_inline(train_columns, train_rows)
    test_df = tables.load_inline(test_columns, test_rows)
    try:
        result = tabicl_runtime.predict(
            train_df, test_df, target=target, task=task,
            max_train_rows=max_train_rows, max_predictions=max_predictions,
        )
    except RuntimeError:
        log.warning("tabicl_unavailable")
        raise
    _timed(
        "tabicl_predict", t0, task=task,
        n_train=int(train_df.shape[0]), n_test=int(test_df.shape[0]),
    )
    return result


@mcp.tool()
def tabicl_status() -> dict:
    """Quick check: is the [tabicl] extra installed and loaded?
    Cheap — does NOT trigger torch import."""
    return tabicl_runtime.status()


# ------------------------------ Entrypoint ---------------------------------


def main() -> None:
    # `name` collides with LogRecord.name — emit as `server_name`.
    log.info("mcp_server_starting", extra={"server_name": mcp.name, "pid": os.getpid()})
    try:
        mcp.run(transport="stdio")
    except Exception:
        log.exception("mcp_server_crashed")
        raise


if __name__ == "__main__":
    main()
