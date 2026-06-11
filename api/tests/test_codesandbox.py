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
        "if k.startswith(('ANTHROPIC_', 'OPENAI_', 'DATABASE_', 'AZURE_', 'AWS_'))]\n"
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
    assert set(out.keys()) == {
        "exit_code", "status", "duration_ms", "stdout", "stderr", "artifacts",
    }
    assert out["exit_code"] == 0
    assert out["status"] == "completed"
    assert out["duration_ms"] >= 0
    assert out["artifacts"] == []


# ── §M figure capture ────────────────────────────────────────────────────────


async def test_no_artifacts_when_user_code_writes_nothing() -> None:
    """A user run that doesn't call savefig returns artifacts=[]."""
    result = await run_python("print('no figure produced')")
    assert result.status == "completed"
    assert result.artifacts == []


async def test_matplotlib_figure_captured_as_png() -> None:
    """End-to-end: user calls plt.savefig, sandbox finds the PNG and
    returns it as a base64-encoded artefact. Skips if matplotlib isn't
    importable (the host might not have it; figure capture is opt-in
    by the user code's own matplotlib import)."""
    import base64

    code = (
        "try:\n"
        "    import matplotlib.pyplot as plt\n"
        "except ImportError:\n"
        "    print('NOMATPLOTLIB')\n"
        "else:\n"
        "    plt.figure()\n"
        "    plt.plot([1, 2, 3], [1, 4, 9])\n"
        "    plt.savefig('plot.png')\n"
        "    print('saved')\n"
    )
    result = await run_python(code)
    assert result.status == "completed"
    if "NOMATPLOTLIB" in result.stdout:
        pytest.skip("matplotlib not installed in the sandbox's import path")
    assert "saved" in result.stdout
    assert len(result.artifacts) == 1
    art = result.artifacts[0]
    assert art["filename"] == "plot.png"
    assert art["mime"] == "image/png"
    assert art["size_bytes"] > 0
    # b64 decodes to a real PNG (magic header 89 50 4E 47)
    raw = base64.b64decode(art["b64"])
    assert raw[:4] == b"\x89PNG"


async def test_non_png_files_are_ignored() -> None:
    """Only *.png is captured. A *.txt the user writes shouldn't surface."""
    code = (
        "open('notes.txt', 'w').write('not a figure')\n"
        "print('done')\n"
    )
    result = await run_python(code)
    assert result.status == "completed"
    assert result.artifacts == []


async def test_artifact_per_file_cap_drops_giant_png(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PNG written that exceeds the single-file cap is dropped with the
    truncated marker. We can't easily generate a real >1 MB PNG inline,
    so monkeypatch the cap down for this test."""
    import mcp_codesandbox.sandbox as sb
    monkeypatch.setattr(sb, "ARTIFACTS_PER_FILE_CAP_BYTES", 50)
    code = (
        "with open('big.png', 'wb') as f:\n"
        "    f.write(b'\\x89PNG' + b'X' * 200)\n"
        "print('wrote')\n"
    )
    result = await run_python(code)
    assert result.status == "completed"
    assert result.artifacts == []
    assert "[sandbox] artifact truncated" in result.stderr


async def test_artifact_total_cap_drops_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple PNGs that exceed the total cap are dropped past the
    threshold. Per-file is fine; total isn't."""
    import mcp_codesandbox.sandbox as sb
    # Two ~100 byte files but total cap of 150 → first lands, second drops.
    monkeypatch.setattr(sb, "ARTIFACTS_PER_FILE_CAP_BYTES", 200)
    monkeypatch.setattr(sb, "ARTIFACTS_TOTAL_CAP_BYTES", 150)
    code = (
        "with open('a.png', 'wb') as f:\n"
        "    f.write(b'\\x89PNG' + b'A' * 100)\n"
        "with open('b.png', 'wb') as f:\n"
        "    f.write(b'\\x89PNG' + b'B' * 100)\n"
        "print('wrote')\n"
    )
    result = await run_python(code)
    assert result.status == "completed"
    assert len(result.artifacts) == 1
    assert result.artifacts[0]["filename"] in ("a.png", "b.png")
    assert "[sandbox] artifact truncated" in result.stderr


async def test_artifacts_skipped_on_timeout() -> None:
    """Killed / timeout runs don't scan — tempdir state is undefined."""
    code = (
        "with open('plot.png', 'wb') as f:\n"
        "    f.write(b'\\x89PNG' + b'data')\n"
        "while True:\n"
        "    pass\n"
    )
    result = await run_python(code, cpu_seconds=2, wall_seconds=3)
    assert result.status in ("timeout", "killed")
    assert result.artifacts == []


# ── §B bubblewrap isolation tier ─────────────────────────────────────────────


def test_bwrap_probe_returns_bool() -> None:
    """The probe must always return a bool — no exceptions allowed even
    on hosts without bwrap. Cached via functools.cache so repeated calls
    are dict lookups."""
    from mcp_codesandbox.sandbox import _bwrap_available
    result = _bwrap_available()
    assert isinstance(result, bool)
    # Cached — second call yields same value.
    assert _bwrap_available() is result


def test_build_command_uses_bwrap_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the bwrap probe reports True, the argv must start with the
    bwrap binary and include --unshare-all + --cap-drop ALL."""
    import mcp_codesandbox.sandbox as sb

    monkeypatch.setattr(sb, "_bwrap_available", lambda: True)
    monkeypatch.setattr(sb, "_unshare_available", lambda: False)
    # `shutil.which` resolves bwrap's path; stub to a known string for
    # the assertion to be portable.
    monkeypatch.setattr(sb.shutil, "which",
                        lambda name: "/usr/bin/bwrap" if name == "bwrap" else None)

    argv = sb._build_command("print('x')", tmpdir="/tmp/test-sbx")
    assert argv[0] == "/usr/bin/bwrap"
    assert "--unshare-all" in argv
    assert "--die-with-parent" in argv
    assert "--cap-drop" in argv and "ALL" in argv
    assert "--chdir" in argv
    # Tempdir bound writable inside the sandbox.
    assert argv[argv.index("--bind") + 1] == "/tmp/test-sbx"


def test_build_command_falls_back_to_unshare_when_bwrap_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bwrap missing → unshare path. Same trust boundary as pre-§B."""
    import mcp_codesandbox.sandbox as sb

    monkeypatch.setattr(sb, "_bwrap_available", lambda: False)
    monkeypatch.setattr(sb, "_unshare_available", lambda: True)
    monkeypatch.setattr(sb.shutil, "which",
                        lambda name: "/usr/bin/unshare" if name == "unshare" else None)

    argv = sb._build_command("print('x')", tmpdir="/tmp/test-sbx")
    assert argv[0] == "/usr/bin/unshare"
    assert "-n" in argv and "-r" in argv
    assert "--unshare-all" not in argv  # no bwrap involvement


def test_build_command_falls_back_to_plain_when_neither_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither isolator works → plain `python -I` with stripped env.
    Last-resort tier 3."""
    import mcp_codesandbox.sandbox as sb

    monkeypatch.setattr(sb, "_bwrap_available", lambda: False)
    monkeypatch.setattr(sb, "_unshare_available", lambda: False)

    argv = sb._build_command("print('x')", tmpdir="/tmp/test-sbx")
    # First arg is the Python interpreter; -I is the isolation flag.
    assert argv[0].endswith("python") or "python" in argv[0]
    assert "-I" in argv
    assert "bwrap" not in " ".join(argv)
    assert "unshare" not in " ".join(argv)


async def test_sandbox_run_succeeds_under_whichever_tier_is_active() -> None:
    """End-to-end: regardless of which tier the host supports, a simple
    `print` should run successfully. This pins the contract that no
    tier's argv has a typo or missing required flag."""
    result = await run_python("print('tier-check')")
    assert result.status == "completed"
    assert result.exit_code == 0
    assert "tier-check" in result.stdout


# ── P3 review-finding: end-to-end exec under each tier the host supports ─────


async def test_each_supported_tier_runs_actual_subprocess() -> None:
    """`run_python` selects whichever isolation tier the host supports
    (bwrap > unshare > plain). The existing tests monkeypatch the probe
    functions to verify argv shape. This test does NOT mock — it
    actually invokes the subprocess on whichever tier the CI runner
    supports, proving the argv we picked is real (not just syntactically
    correct).

    On Ubuntu-latest CI (no bwrap installed by default, no
    CAP_SYS_ADMIN granted to unshare): expect the plain `python -I`
    tier. Tier validation: stdout returns and the env is stripped."""
    from mcp_codesandbox.sandbox import _bwrap_available, _unshare_available
    bwrap_ok = _bwrap_available()
    unshare_ok = _unshare_available()
    # Whichever tier was picked, this must execute the user code and
    # surface stdout. The selection logic is in _build_command; if any
    # tier's argv is malformed the subprocess won't reach `print`.
    result = await run_python(
        "import os; print('cwd_writable=', os.access(os.getcwd(), os.W_OK))"
    )
    assert result.status == "completed", (
        f"tier-picker chose bwrap={bwrap_ok}, unshare={unshare_ok}, "
        f"but the subprocess failed: stderr={result.stderr!r}"
    )
    # tempdir cwd must be writable on every tier (bwrap binds it; the
    # other tiers leave the temp filesystem permission alone).
    assert "cwd_writable= True" in result.stdout


async def test_env_strip_under_active_tier() -> None:
    """End-to-end pin: regardless of selected tier, environment
    variables passed to the parent process do NOT leak into the child.
    Tested by setting a sentinel env, then verifying it's absent."""
    import os
    sentinel = "CHEMCLAW2_SANDBOX_TEST_LEAK"
    os.environ[sentinel] = "should-not-leak"
    try:
        result = await run_python(
            f"import os; print('LEAKED' if os.environ.get('{sentinel}') else 'isolated')",
        )
        assert result.status == "completed"
        assert "isolated" in result.stdout, (
            f"env var {sentinel} leaked into sandbox; "
            f"stdout={result.stdout!r}"
        )
    finally:
        os.environ.pop(sentinel, None)


async def test_tier_picker_picks_a_real_tier() -> None:
    """`_build_command` always returns a non-empty argv. The first
    element is either the python interpreter (tier 3), `unshare`
    (tier 2), or `bwrap` (tier 1). This catches any future bug where
    the selector returns `[]` or starts with an unexpected program."""
    from mcp_codesandbox.sandbox import _build_command
    argv = _build_command("print('x')", tmpdir="/tmp/test-sbx-x")
    assert len(argv) >= 3, f"argv too short: {argv}"
    head = argv[0]
    assert (
        "python" in head or head.endswith(("/unshare", "/bwrap")) or head == "unshare" or head == "bwrap"
    ), f"unexpected leader in tier argv: {head!r}"
