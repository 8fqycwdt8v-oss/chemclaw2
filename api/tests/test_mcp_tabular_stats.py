"""Tests for `mcp_tabular` stats tools.

CI installs the server via `pip install packages/mcp-servers/mcp_tabular`
Locally run the same install to make these tests
runnable. Tests are skipped automatically when the package is missing.
"""
from __future__ import annotations

import pytest

pytest.importorskip("mcp_tabular")

import numpy as np  # noqa: E402
from mcp_tabular import stats, tables  # noqa: E402


def _toy_table():
    rng = np.random.default_rng(0)
    n = 60
    rows = []
    for _ in range(n):
        age = float(rng.normal(40, 10))
        score = float(age * 0.5 + rng.normal(0, 5))
        group = "A" if rng.random() < 0.5 else "B"
        rows.append([age, score, group])
    return ["age", "score", "group"], rows


def test_load_inline_caps():
    # Row cap
    with pytest.raises(ValueError, match="rows exceeds"):
        tables.load_inline(["x"], [[1]] * (tables.MAX_ROWS + 1))
    # Col mismatch
    with pytest.raises(ValueError, match="row 0 has"):
        tables.load_inline(["a", "b"], [[1]])
    # Duplicate columns
    with pytest.raises(ValueError, match="unique"):
        tables.load_inline(["a", "a"], [[1, 2]])


def test_load_inline_numeric_coercion():
    df = tables.load_inline(["v"], [["1.5"], ["2.5"], ["3.5"]])
    assert df["v"].dtype.kind == "f"
    assert df["v"].sum() == 7.5


def test_describe_numeric_and_categorical():
    cols, rows = _toy_table()
    df = tables.load_inline(cols, rows)
    out = stats.describe(df)
    assert out["n_rows"] == 60
    age = out["columns"]["age"]
    assert age["dtype"].startswith("float")
    assert age["mean"] is not None
    assert age["q50"] is not None
    group = out["columns"]["group"]
    assert "top_categories" in group
    assert group["n_unique"] in (1, 2)


def test_correlate_pearson():
    cols, rows = _toy_table()
    df = tables.load_inline(cols, rows)
    out = stats.correlate(df, method="pearson")
    assert "age" in out["columns"]
    assert "score" in out["columns"]
    # diagonal is 1
    age_idx = out["columns"].index("age")
    assert abs(out["matrix"][age_idx][age_idx] - 1.0) < 1e-9
    # age and score are positively correlated by construction
    score_idx = out["columns"].index("score")
    assert out["matrix"][age_idx][score_idx] > 0.5


def test_correlate_rejects_non_numeric_subset():
    cols, rows = _toy_table()
    df = tables.load_inline(cols, rows)
    with pytest.raises(ValueError, match="non-numeric"):
        stats.correlate(df, columns=["age", "group"])


def test_ttest_ind_two_groups():
    cols, rows = _toy_table()
    df = tables.load_inline(cols, rows)
    result = stats.hypothesis_test(df, test="ttest_ind", x="score", group="group")
    assert result["test"] == "ttest_ind"
    assert result["p_value"] is not None
    assert result["n"] == 60


def test_chi2_categorical():
    rows = [["A", "X"]] * 20 + [["A", "Y"]] * 5 + [["B", "X"]] * 5 + [["B", "Y"]] * 20
    df = tables.load_inline(["c1", "c2"], rows)
    result = stats.hypothesis_test(df, test="chi2", x="c1", group="c2")
    assert result["test"] == "chi2"
    assert result["p_value"] is not None and result["p_value"] < 0.05


def test_distribution_check_shapiro_normal():
    rng = np.random.default_rng(42)
    rows = [[float(v)] for v in rng.normal(0, 1, size=200)]
    df = tables.load_inline(["x"], rows)
    result = stats.distribution_check(df, column="x", test="shapiro")
    assert result["test"] == "shapiro"
    assert result["normal"] is True


def test_distribution_check_anderson():
    rng = np.random.default_rng(42)
    rows = [[float(v)] for v in rng.normal(0, 1, size=200)]
    df = tables.load_inline(["x"], rows)
    result = stats.distribution_check(df, column="x", test="anderson")
    assert result["test"] == "anderson"
    assert "critical_values" in result
    assert "normal_at_5pct" in result
