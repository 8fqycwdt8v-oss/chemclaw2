"""Tests for the TabICL lazy-load guard.

These tests do NOT install the [tabicl] extra — they verify the
contract that the server stays usable (and reports unavailable) when
the extra is missing, which is the CI configuration. Full end-to-end
TabICL inference is exercised manually in a dev environment with the
extra installed; see the package README.
"""
from __future__ import annotations

import pytest

pytest.importorskip("mcp_tabular")

from mcp_tabular import tabicl_runtime  # noqa: E402


def test_status_does_not_import_torch():
    """status() must work without [tabicl] installed."""
    status = tabicl_runtime.status()
    assert "installed" in status
    assert "loaded" in status
    # In CI the extra is not installed.
    assert status["installed"] is False
    assert status["loaded"] is False


def test_get_classes_raises_when_extra_missing():
    """Without [tabicl] extra, get_classes raises a clear RuntimeError."""
    with pytest.raises(RuntimeError, match="tabicl extra not installed"):
        tabicl_runtime.get_classes()


def test_predict_raises_when_extra_missing():
    """predict() surfaces the same error so the agent gets a helpful message."""
    import pandas as pd
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [0, 1, 0]})
    test = pd.DataFrame({"x": [1.5, 2.5]})
    with pytest.raises(RuntimeError, match="tabicl extra not installed"):
        tabicl_runtime.predict(train, test, target="y", task="classification")
