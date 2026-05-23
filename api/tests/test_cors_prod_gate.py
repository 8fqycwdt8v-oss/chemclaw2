"""Unit tests for the production CORS gate in `api.main.create_app`.

CLAUDE.md §security: gates must fail closed. In production, an empty,
wildcard, or localhost-containing CORS_ALLOWED_ORIGINS list is almost
always a misconfiguration that exposes the API to drive-by browser
attacks. `create_app` raises at startup so the misconfig surfaces as
a crashloop, never as a silently-permissive deployment.
"""
from __future__ import annotations

import importlib

import pytest


def _reload_main(monkeypatch: pytest.MonkeyPatch, **env: str | None) -> None:
    """Re-import api.main with the given env vars so create_app sees them.

    Returns the imported module so tests can call create_app() on it.
    """
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    import api.main as main_module
    return importlib.reload(main_module)


@pytest.mark.parametrize("env_value", ["prod", "production", "PROD", "Production"])
def test_prod_refuses_empty_cors(monkeypatch: pytest.MonkeyPatch, env_value: str) -> None:
    main_module = _reload_main(monkeypatch, ENV=env_value, CORS_ALLOWED_ORIGINS="")
    with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
        main_module.create_app()


def test_prod_refuses_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    main_module = _reload_main(monkeypatch, ENV="prod", CORS_ALLOWED_ORIGINS="*")
    with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
        main_module.create_app()


def test_prod_refuses_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    main_module = _reload_main(
        monkeypatch,
        ENV="prod",
        CORS_ALLOWED_ORIGINS="https://app.example.com,http://localhost:3000",
    )
    with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
        main_module.create_app()


def test_prod_accepts_explicit_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    main_module = _reload_main(
        monkeypatch,
        ENV="prod",
        CORS_ALLOWED_ORIGINS="https://app.example.com,https://staging.example.com",
    )
    app = main_module.create_app()
    assert app is not None


def test_dev_falls_back_to_localhost_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    main_module = _reload_main(monkeypatch, ENV="test", CORS_ALLOWED_ORIGINS="")
    app = main_module.create_app()
    assert app is not None


def teardown_module(_module: object) -> None:
    """Reload api.main once more so other tests see the test-env config.

    The CORS-gate tests reload `api.main` with mutated env; without this,
    subsequent tests in the same pytest session would inherit a state
    that depends on the last parametrize iteration's env vars.
    """
    import os
    os.environ["ENV"] = "test"
    os.environ.pop("CORS_ALLOWED_ORIGINS", None)
    import api.main as main_module
    importlib.reload(main_module)
