"""Descriptive + inferential stats. Pure functions over DataFrames."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats


def _to_float(x: Any) -> float | None:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    return round(float(x), 6)


def describe(df: pd.DataFrame, top_k_categories: int = 5) -> dict:
    """Per-column summary. Numeric columns get quantiles; object/category
    columns get top-k value counts. Always returns dtype + n_missing.
    """
    out: dict[str, dict] = {}
    for col in df.columns:
        s = df[col]
        n = int(s.shape[0])
        n_missing = int(s.isna().sum())
        col_info: dict[str, Any] = {
            "dtype": str(s.dtype),
            "n": n,
            "n_missing": n_missing,
        }
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            non_null = s.dropna()
            if len(non_null) > 0:
                col_info.update({
                    "mean": _to_float(non_null.mean()),
                    "std": _to_float(non_null.std(ddof=1)) if len(non_null) > 1 else None,
                    "min": _to_float(non_null.min()),
                    "q25": _to_float(non_null.quantile(0.25)),
                    "q50": _to_float(non_null.quantile(0.50)),
                    "q75": _to_float(non_null.quantile(0.75)),
                    "max": _to_float(non_null.max()),
                })
        else:
            vc = s.dropna().value_counts().head(top_k_categories)
            col_info["top_categories"] = [
                {"value": str(k), "count": int(v)} for k, v in vc.items()
            ]
            col_info["n_unique"] = int(s.nunique(dropna=True))
        out[col] = col_info
    return {"n_rows": int(df.shape[0]), "n_cols": int(df.shape[1]), "columns": out}


def correlate(
    df: pd.DataFrame,
    method: str = "pearson",
    columns: list[str] | None = None,
) -> dict:
    """Pairwise correlation matrix over numeric columns."""
    if method not in ("pearson", "spearman", "kendall"):
        raise ValueError(f"method must be pearson|spearman|kendall, got {method!r}")
    numeric = df.select_dtypes(include="number")
    if columns is not None:
        missing = set(columns) - set(numeric.columns)
        if missing:
            raise ValueError(f"non-numeric or unknown columns: {sorted(missing)}")
        numeric = numeric[columns]
    if numeric.shape[1] < 2:
        raise ValueError("correlate needs at least 2 numeric columns")
    matrix = numeric.corr(method=method)
    return {
        "method": method,
        "columns": list(matrix.columns),
        "matrix": [[_to_float(v) for v in row] for row in matrix.values],
    }


def hypothesis_test(
    df: pd.DataFrame,
    test: str,
    x: str,
    group: str | None = None,
) -> dict:
    """Two-sample/contingency hypothesis tests via scipy.stats.

    test:
      - ttest_ind:   x is numeric, group is binary categorical
      - mannwhitney: x is numeric, group is binary categorical (non-parametric)
      - anova:       x is numeric, group is categorical (>=2 levels)
      - chi2:        x and group are both categorical (contingency)
    """
    if x not in df.columns:
        raise ValueError(f"column not found: {x!r}")
    if test in ("ttest_ind", "mannwhitney", "anova") and group is None:
        raise ValueError(f"{test} requires `group`")
    if group is not None and group not in df.columns:
        raise ValueError(f"group column not found: {group!r}")

    if test == "chi2":
        if group is None:
            raise ValueError("chi2 requires `group`")
        ct = pd.crosstab(df[x], df[group])
        chi2, p, dof, _expected = scipy_stats.chi2_contingency(ct)
        return {
            "test": "chi2",
            "statistic": _to_float(chi2),
            "p_value": _to_float(p),
            "df": int(dof),
            "n": int(ct.values.sum()),
        }

    # Numeric-x tests below
    if not pd.api.types.is_numeric_dtype(df[x]):
        raise ValueError(f"{test} requires numeric `{x}`")
    groups = df.dropna(subset=[x, group])[[x, group]]  # type: ignore[list-item]
    levels = groups[group].unique().tolist()

    if test == "ttest_ind":
        if len(levels) != 2:
            raise ValueError(f"ttest_ind requires exactly 2 group levels, got {len(levels)}")
        a = groups.loc[groups[group] == levels[0], x].to_numpy()
        b = groups.loc[groups[group] == levels[1], x].to_numpy()
        stat, p = scipy_stats.ttest_ind(a, b, equal_var=False)
        return {
            "test": "ttest_ind",
            "statistic": _to_float(stat),
            "p_value": _to_float(p),
            "n": int(len(a) + len(b)),
            "groups": [str(levels[0]), str(levels[1])],
        }

    if test == "mannwhitney":
        if len(levels) != 2:
            raise ValueError(f"mannwhitney requires exactly 2 group levels, got {len(levels)}")
        a = groups.loc[groups[group] == levels[0], x].to_numpy()
        b = groups.loc[groups[group] == levels[1], x].to_numpy()
        stat, p = scipy_stats.mannwhitneyu(a, b, alternative="two-sided")
        return {
            "test": "mannwhitney",
            "statistic": _to_float(stat),
            "p_value": _to_float(p),
            "n": int(len(a) + len(b)),
            "groups": [str(levels[0]), str(levels[1])],
        }

    if test == "anova":
        if len(levels) < 2:
            raise ValueError("anova requires >=2 group levels")
        samples = [
            groups.loc[groups[group] == lv, x].to_numpy() for lv in levels
        ]
        stat, p = scipy_stats.f_oneway(*samples)
        return {
            "test": "anova",
            "statistic": _to_float(stat),
            "p_value": _to_float(p),
            "df_between": len(levels) - 1,
            "df_within": int(sum(len(s) for s in samples) - len(levels)),
            "n": int(sum(len(s) for s in samples)),
            "groups": [str(lv) for lv in levels],
        }

    raise ValueError(f"unknown test: {test!r}")


def distribution_check(df: pd.DataFrame, column: str, test: str = "shapiro") -> dict:
    """Normality test on a numeric column. test ∈ shapiro|ks_normal|anderson."""
    if column not in df.columns:
        raise ValueError(f"column not found: {column!r}")
    if not pd.api.types.is_numeric_dtype(df[column]):
        raise ValueError(f"distribution_check requires numeric column, got {df[column].dtype}")
    x = df[column].dropna().to_numpy()
    if len(x) < 3:
        raise ValueError("need at least 3 non-null values")

    if test == "shapiro":
        # Shapiro-Wilk is unreliable above ~5000 samples; cap.
        sample = x if len(x) <= 5000 else np.random.default_rng(0).choice(x, size=5000, replace=False)
        stat, p = scipy_stats.shapiro(sample)
        return {
            "test": "shapiro",
            "statistic": _to_float(stat),
            "p_value": _to_float(p),
            "normal": bool(p > 0.05),
            "n": int(len(sample)),
        }

    if test == "ks_normal":
        # KS against a standard-normal fit to (mean, std) of the sample.
        mu, sigma = float(x.mean()), float(x.std(ddof=1)) if len(x) > 1 else 1.0
        if sigma == 0:
            raise ValueError("ks_normal: column has zero variance")
        stat, p = scipy_stats.kstest(x, "norm", args=(mu, sigma))
        return {
            "test": "ks_normal",
            "statistic": _to_float(stat),
            "p_value": _to_float(p),
            "normal": bool(p > 0.05),
            "n": int(len(x)),
        }

    if test == "anderson":
        result = scipy_stats.anderson(x, dist="norm")
        # Anderson returns a tuple-like (statistic, critical_values, significance_level)
        critical = [float(c) for c in result.critical_values]
        sig_levels = [float(s) for s in result.significance_level]
        # "normal" at the 5% level: statistic < critical value for 5%.
        try:
            idx_5 = sig_levels.index(5.0)
            normal = bool(result.statistic < critical[idx_5])
        except ValueError:
            normal = False
        return {
            "test": "anderson",
            "statistic": _to_float(result.statistic),
            "critical_values": critical,
            "significance_levels_pct": sig_levels,
            "normal_at_5pct": normal,
            "n": int(len(x)),
        }

    raise ValueError(f"unknown test: {test!r}")
