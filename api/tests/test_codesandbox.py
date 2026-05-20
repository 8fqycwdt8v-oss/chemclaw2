"""Tests for the Python sandbox MCP.

Pure subprocess behaviour — no DB. Verifies the resource-limit
enforcement and result-shape contract. Skipped when the
`mcp_codesandbox` package isn't installed (CI installs it via the
"Install MCP servers" step).
"""
from __future__ import annotations

import pytest

pytest.importorskip("mcp_codesandbox")

from mcp_codesandbox.sandbox import (  # noqa: E402
    CODE_BYTE_CAP,
    EXIT_TIMEOUT,
    STDOUT_CAP_BYTES,
    run_python,
)


async def test_simple_print_runs_to_completion() -> None:
    result = await run_python("print('hello sandbox')")
    assert result.status == "completed"
    assert result.exit_code == 0
    assert "hello sandbox" in result.stdout


async def test_non_zero_exit_reported_as_completed() -> None:
    """Status 'completed' covers any normal program exit — even error
    exits — so the caller can read stderr for context."""
    result = await run_python("import sys; sys.exit(3)")
    assert result.exit_code == 3
    assert result.status == "completed"


async def test_empty_code_returns_error() -> None:
    result = await run_python("")
    assert result.status == "error"
    assert result.exit_code == 2
    assert "empty code" in result.stderr


async def test_oversize_code_returns_error() -> None:
    big = "x = 1\n" * (CODE_BYTE_CAP // 4)
    result = await run_python(big)
    assert result.status == "error"
    assert "exceeds" in result.stderr


async def test_invalid_cpu_seconds_returns_error() -> None:
    result = await run_python("print(1)", cpu_seconds=0)
    assert result.status == "error"
    result = await run_python("print(1)", cpu_seconds=1000)
    assert result.status == "error"


async def test_wall_timeout_kills_runaway() -> None:
    """An infinite Python loop should hit the wall-clock cap and be killed
    with exit_code = EXIT_TIMEOUT."""
    code = "while True: pass"
    # Short wall budget — keep the test snappy. CPU budget set lower so
    # RLIMIT_CPU also has a shot at firing (whichever signals first).
    result = await run_python(code, cpu_seconds=3, wall_seconds=4)
    assert result.status in ("timeout", "killed")
    assert result.exit_code in (EXIT_TIMEOUT, 137, -9)
    # Wall-clock timeout annotation lands in stderr.
    if result.status == "timeout":
        assert "wall-clock timeout" in result.stderr.lower()


async def test_stdout_truncated_at_cap() -> None:
    """A program emitting >> 1 MB should land at the cap and carry a
    truncation marker."""
    # Emit ~2 MB of A's, line-buffered.
    code = (
        "import sys\n"
        "for _ in range(20000):\n"
        "    sys.stdout.write('A' * 100 + chr(10))\n"
    )
    result = await run_python(code, cpu_seconds=10, wall_seconds=15)
    assert result.status in ("completed",)
    # Effective stdout (post-decode) bytes can be ≤ STDOUT_CAP_BYTES +
    # the truncation marker; allow a 1 KB grace for the marker line.
    assert len(result.stdout.encode()) <= STDOUT_CAP_BYTES + 1024
    if len(result.stdout.encode()) >= STDOUT_CAP_BYTES:
        assert "truncated" in result.stdout


async def test_env_isolation_blocks_inherited_secrets() -> None:
    """The sandbox launches the child with `env={"HOME": tmpdir,
    "TMPDIR": tmpdir, "PATH": "/usr/bin:/bin"}` — no ANTHROPIC_API_KEY,
    DATABASE_URL, AWS keys, or anything else from the host.

    (Note: `python -I` blocks PYTHONPATH but not the system
    site-packages, so installed packages remain importable — the
    sandbox's trust boundary explicitly carves that out.)
    """
    code = (
        "import os\n"
        "leaked = [k for k in os.environ "
        "if k.startswith(('ANTHROPIC_', 'OPENAI_', 'DATABASE_', 'CLERK_', 'AWS_'))]\n"
        "print('LEAKED:' + ','.join(leaked) if leaked else 'isolated')\n"
    )
    result = await run_python(code)
    assert result.status == "completed"
    assert "isolated" in result.stdout
    assert "LEAKED" not in result.stdout


async def test_result_shape_via_summary() -> None:
    """The summary dict is the JSON contract the agent tool returns."""
    from mcp_codesandbox.sandbox import summary
    result = await run_python("print('ok')")
    out = summary(result)
    assert set(out.keys()) == {"exit_code", "status", "duration_ms", "stdout", "stderr"}
    assert out["exit_code"] == 0
    assert out["status"] == "completed"
    assert out["duration_ms"] >= 0
