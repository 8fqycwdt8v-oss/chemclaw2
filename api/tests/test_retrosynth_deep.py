"""Tests for the deep-retrosynthesis wrapper module.

End-to-end AiZynthFinder runs aren't exercised in CI — the library
requires ~500 MB of model files and is intentionally behind the
`[retrosynth]` extras (which CI doesn't install). The tests below
pin the contract that matters most: when AiZynthFinder isn't
installed, the wrapper raises ImportError cleanly (so the tool layer
can return its install-hint error). Argument validation runs even
without the extras.
"""
from __future__ import annotations

from typing import Any

import pytest


def test_run_deep_retrosynthesis_rejects_empty_smiles() -> None:
    """ValueError before any AiZynthFinder import — args validate first."""
    from api.agent.retrosynth_deep import run_deep_retrosynthesis
    with pytest.raises(ValueError, match="target_smiles is required"):
        run_deep_retrosynthesis("")
    with pytest.raises(ValueError, match="target_smiles is required"):
        run_deep_retrosynthesis("   ")


def test_run_deep_retrosynthesis_raises_importerror_without_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `aizynthfinder` isn't importable, the wrapper must raise
    ImportError. The tool layer relies on this to surface a clean
    install-hint error instead of pretending success."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "aizynthfinder" or name.startswith("aizynthfinder."):
            raise ImportError(f"simulated missing dep: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    from api.agent.retrosynth_deep import run_deep_retrosynthesis
    with pytest.raises(ImportError):
        run_deep_retrosynthesis("CCO")


def test_build_finder_respects_aizynth_config_path_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`AIZYNTH_CONFIG_PATH` should be picked up at construction time
    so operators can point at a non-default bundle (e.g. full USPTO).
    Verified by stubbing AiZynthFinder and checking the kwarg it was
    called with."""
    import sys
    import types

    captured: dict[str, object] = {}

    class _StubAiZynthFinder:
        def __init__(self, configfile: str | None = None) -> None:
            captured["configfile"] = configfile

    # Provide a fake `aizynthfinder.aizynthfinder` module so
    # `_build_finder`'s import succeeds without the real package.
    fake_pkg = types.ModuleType("aizynthfinder")
    fake_submodule = types.ModuleType("aizynthfinder.aizynthfinder")
    fake_submodule.AiZynthFinder = _StubAiZynthFinder  # type: ignore[attr-defined]
    fake_pkg.aizynthfinder = fake_submodule  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aizynthfinder", fake_pkg)
    monkeypatch.setitem(sys.modules, "aizynthfinder.aizynthfinder", fake_submodule)

    monkeypatch.setenv("AIZYNTH_CONFIG_PATH", "/custom/aizynth.yml")

    from api.agent.retrosynth_deep import _build_finder
    _build_finder()
    assert captured["configfile"] == "/custom/aizynth.yml"


def test_build_finder_uses_default_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty `AIZYNTH_CONFIG_PATH` → AiZynthFinder() with no args
    (library picks up its bundled demo config)."""
    import sys
    import types

    captured: dict[str, object] = {}

    class _StubAiZynthFinder:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs

    fake_pkg = types.ModuleType("aizynthfinder")
    fake_submodule = types.ModuleType("aizynthfinder.aizynthfinder")
    fake_submodule.AiZynthFinder = _StubAiZynthFinder  # type: ignore[attr-defined]
    fake_pkg.aizynthfinder = fake_submodule  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aizynthfinder", fake_pkg)
    monkeypatch.setitem(sys.modules, "aizynthfinder.aizynthfinder", fake_submodule)

    monkeypatch.delenv("AIZYNTH_CONFIG_PATH", raising=False)

    from api.agent.retrosynth_deep import _build_finder
    _build_finder()
    assert captured["args"] == ()
    assert captured["kwargs"] == {}
