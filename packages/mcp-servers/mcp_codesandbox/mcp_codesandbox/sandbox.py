"""Subprocess-based Python sandbox with hard resource limits.

Trust boundary:
    The sandbox picks the strongest isolation tier the host supports
    (`_build_command` probes each at first use):

    1. bubblewrap (`bwrap --unshare-all`) — equivalent to a fresh
       container: pid/net/ipc/uts/cgroup/mount/user namespaces all
       dropped, all capabilities dropped, /usr + /lib read-only,
       /tmp + /home are ephemeral tmpfs, only the run's own tempdir
       is writable. Available on bare-metal / VM hosts where bwrap is
       installed and CAP_SYS_ADMIN allows the namespace unshare.
    2. `unshare -n -r` — network-namespace drop only (no fs / cgroup
       isolation). Used when bwrap isn't available or its smoke
       test fails (e.g. inside Docker with default seccomp).
    3. plain `python -I` — env-strip + PYTHONPATH isolation only.
       Last-resort fallback.

    Each tier additionally enforces, at the OS level:
      - resource caps (CPU, memory, fs writes, fds, output bytes)
      - wall-clock SIGKILL backstop
      - clean env: only HOME, TMPDIR, PATH passed in; no API keys,
        DATABASE_URL, or other host secrets leak in
      - fresh cwd: agent-written files vanish when the run ends
      - PYTHONPATH / user-site stripped via `python -I`

    Caveats — tier 2/3 hosts (no bwrap):
      - `/` is still visible to the sandbox process (reads work, writes
        to the host fs would fail because cwd is the tempdir).
      - System site-packages remain importable; `import api` works if
        chemclaw2 is `pip install -e .`-installed. `python -I` only
        blocks PYTHONPATH / user-site, not system-installed deps.
      - Network unblocked on tier 3.

    Tier 1 (bwrap) closes all three caveats. The agent's prompt safety
    gates + the SDK's tool-use hooks are layered on top regardless.

What it does enforce:
    - CPU seconds (RLIMIT_CPU)         — kills runaway loops
    - Address-space memory (RLIMIT_AS) — blocks malloc bombs
    - Output file size (RLIMIT_FSIZE)  — blocks fs-blowout writes
    - Open file descriptors (RLIMIT_NOFILE) — caps fork+open scaling
    - Wall-clock timeout (asyncio)     — SIGKILL backstop after timeout
    - Clean env, fresh cwd            — no inherited PATH / API keys
    - Captured stdout/stderr w/ caps  — prevents output flood
"""
from __future__ import annotations

import asyncio
import base64
import functools
import logging
import os
import resource
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Hard caps. Tuned for "reasonable analytical work" — descriptive stats,
# small numpy arrays, a few thousand RDKit ops. NOT sized for training
# a neural net. Tighten further if a customer reports the limit being
# the wrong shape; document trade-offs in the BACKLOG.

CPU_SECONDS_DEFAULT = 30
WALL_SECONDS_DEFAULT = 35           # wall-clock SIGKILL backstop (CPU+5)
MEMORY_BYTES_DEFAULT = 512 * 1024 * 1024     # 512 MB
OUTPUT_FILE_BYTES = 10 * 1024 * 1024         # 10 MB any single fs write
OPEN_FILES = 64
STDOUT_CAP_BYTES = 1 * 1024 * 1024           # 1 MB stdout
STDERR_CAP_BYTES = 256 * 1024                # 256 KB stderr
CODE_BYTE_CAP = 200 * 1024                   # 200 KB source code

# Sentinel exit codes the runner emits when the child is killed.
EXIT_TIMEOUT = 124   # `timeout(1)` convention
EXIT_KILLED = 137    # 128 + SIGKILL(9)

# Figure-capture caps (§M, Tier 3 plan). PNG only in V1 — SVG/PDF/HTML
# expand the parser surface and weren't worth it for the 80% case.
ARTIFACTS_TOTAL_CAP_BYTES = 1_500_000   # 1.5 MB across all attached PNGs
ARTIFACTS_PER_FILE_CAP_BYTES = 1_000_000  # any single file > 1 MB is dropped
ARTIFACT_EXTENSIONS = (".png",)

# Prelude script prepended to every user submission so matplotlib defaults
# to a headless backend before pyplot is imported anywhere. Wrapped in
# try/except so hosts without matplotlib don't crash the prelude — user
# code that doesn't import matplotlib still runs normally. Users who
# explicitly call `matplotlib.use("...")` afterwards can still override
# (and will fail at draw time inside the sandbox — no display).
_FIGURE_PRELUDE = (
    "try:\n"
    "    import matplotlib as _mpl\n"
    "    _mpl.use('Agg')\n"
    "except ImportError:\n"
    "    pass\n"
)


@dataclass(slots=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    status: str  # 'completed' | 'timeout' | 'killed' | 'error'
    artifacts: list[dict[str, Any]] = field(default_factory=list)


def _set_rlimits(
    cpu_seconds: int,
    memory_bytes: int,
) -> None:
    """preexec_fn — runs in the child after fork() before exec()."""
    # CPU seconds — when consumed the kernel sends SIGXCPU then SIGKILL.
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    # Address space — caps virtual memory; malloc beyond raises MemoryError.
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    # File size — any single write past this raises SIGXFSZ.
    resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_FILE_BYTES, OUTPUT_FILE_BYTES))
    # Open files — caps the child's fd table.
    resource.setrlimit(resource.RLIMIT_NOFILE, (OPEN_FILES, OPEN_FILES))
    # Disallow core dumps — they leak memory contents on crash.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


@functools.cache
def _unshare_available() -> bool:
    """Return True iff `unshare -n -r` actually works on this host.

    `unshare -n` needs CAP_SYS_ADMIN; GitHub Actions runners and other
    unprivileged environments will fail with "unshare: operation not
    permitted". `functools.cache` means we probe at most once per
    process — first sandbox call eats the ~ms cost, subsequent calls
    are dict lookups. Module-import cost stays at zero. The cache is
    process-lifetime, which is fine: kernel capabilities don't change
    mid-process in any realistic deploy.
    """
    unshare = shutil.which("unshare")
    if unshare is None:
        return False
    import subprocess
    try:
        r = subprocess.run(
            [unshare, "-n", "-r", "true"],
            capture_output=True, timeout=2.0, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("unshare probe raised: %s", e)
        return False
    return r.returncode == 0


@functools.cache
def _bwrap_available() -> bool:
    """Return True iff `bwrap` (bubblewrap) is installed AND
    `--unshare-all` actually works on this host.

    bwrap is a userspace sandboxer (the one Flatpak uses). On bare-metal
    or VM hosts it gives us container-grade isolation; inside a Docker
    container with the default seccomp profile, `--unshare-all` fails
    with EPERM — those hosts fall back to the `unshare`/subprocess
    path. The smoke test below differentiates the two cases.

    Two checks: (1) `bwrap --version` exits 0 (binary is present),
    (2) a minimal `bwrap --unshare-all true` invocation succeeds
    (caps + namespaces actually work). If either fails we return False
    and `_build_command` falls back to the unshare/plain path.
    """
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        return False
    import subprocess
    try:
        version_check = subprocess.run(
            [bwrap, "--version"],
            capture_output=True, timeout=2.0, check=False,
        )
        if version_check.returncode != 0:
            return False
        # Minimal smoke — full profile would fail for the wrong reason
        # if e.g. /usr ro-bind didn't exist on this host. This pins
        # only the cap + namespace requirements.
        smoke = subprocess.run(
            [bwrap, "--unshare-all", "--die-with-parent",
             "--ro-bind", "/usr", "/usr",
             "/usr/bin/true"],
            capture_output=True, timeout=2.0, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("bwrap probe raised: %s", e)
        return False
    return smoke.returncode == 0


def _build_command(code: str, tmpdir: str) -> list[str]:
    """Build the subprocess argv. Picks the strongest isolation the host
    supports:

      1. bubblewrap (`bwrap`) when available + smoke-tested — full
         namespace + cap-drop, equivalent to running in a fresh container.
      2. `unshare -n -r` — network-namespace drop only (no cgroup / no
         filesystem isolation).
      3. plain `python -I` — env-strip + PYTHONPATH isolation only.

    `python -I` runs in isolated mode: ignores PYTHONPATH, PYTHONHOME,
    PYTHONSTARTUP, doesn't add user site-packages to sys.path. Combined
    with `env={}` in the asyncio call this keeps the child off the
    host's import surface even when unshare isn't available — at the
    cost of leaving network access intact.

    User code is prepended with `_FIGURE_PRELUDE` so matplotlib runs
    headless. Users who don't import matplotlib at all pay only the
    cost of `import matplotlib`; figure capture is opt-in by the user
    code's own `plt.savefig(...)` call.
    """
    wrapped = _FIGURE_PRELUDE + code
    py_argv = [sys.executable, "-I", "-c", wrapped]

    if _bwrap_available():
        bwrap = shutil.which("bwrap") or "bwrap"
        # Minimal profile: read-only system libs + ephemeral writable
        # /tmp and /home (the tmpdir-as-cwd already gives the user a
        # writable workspace; --tmpfs is belt-and-suspenders for code
        # that hard-codes /tmp). --unshare-all drops every namespace
        # (pid/net/ipc/uts/cgroup/mount/user). --die-with-parent reaps
        # the child if our process crashes. --cap-drop ALL ensures
        # setuid binaries inside the sandbox can't escalate.
        return [
            bwrap,
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            # /lib64 isn't present on all distros (e.g. Alpine); skip
            # gracefully via --ro-bind-try, which is a no-op when the
            # source path doesn't exist.
            "--ro-bind-try", "/lib64", "/lib64",
            "--ro-bind-try", "/etc/alternatives", "/etc/alternatives",
            "--tmpfs", "/tmp",
            "--tmpfs", "/home",
            "--proc", "/proc",
            "--dev", "/dev",
            "--bind", tmpdir, tmpdir,  # the sandbox writes here
            "--chdir", tmpdir,
            "--unshare-all",
            "--die-with-parent",
            "--cap-drop", "ALL",
            *py_argv,
        ]

    if _unshare_available():
        # `unshare -n` creates a new (empty) network namespace for the
        # child — no DNS, no routes, no inherited sockets. -r runs as
        # an unprivileged user inside the namespace (no UID 0 inside).
        return [shutil.which("unshare") or "unshare", "-n", "-r", *py_argv]
    return py_argv


def _scan_artifacts(tmpdir: str) -> tuple[list[dict[str, Any]], bool]:
    """Walk `tmpdir` for PNG figures and return them base64-encoded.

    Returns (artifacts, truncated). `truncated` is True iff the cap was
    hit and at least one PNG was dropped. Each artifact is a dict:
        {filename, mime, size_bytes, b64}

    Quietly drops files that exceed `ARTIFACTS_PER_FILE_CAP_BYTES`
    individually (≥ 1 MB single PNG = user is misusing figures as
    data dumps).
    """
    artifacts: list[dict[str, Any]] = []
    total = 0
    truncated = False
    try:
        # Deterministic order so multi-figure tests are stable.
        files = sorted(Path(tmpdir).iterdir())
    except OSError:
        return artifacts, False
    for path in files:
        try:
            if not path.is_file():
                continue
            if path.suffix.lower() not in ARTIFACT_EXTENSIONS:
                continue
            size = path.stat().st_size
            if size > ARTIFACTS_PER_FILE_CAP_BYTES:
                truncated = True
                continue
            if total + size > ARTIFACTS_TOTAL_CAP_BYTES:
                truncated = True
                continue
            data = path.read_bytes()
        except OSError:
            continue
        artifacts.append({
            "filename": path.name,
            "mime": "image/png",
            "size_bytes": size,
            "b64": base64.b64encode(data).decode("ascii"),
        })
        total += size
    return artifacts, truncated


async def run_python(
    code: str,
    *,
    cpu_seconds: int = CPU_SECONDS_DEFAULT,
    wall_seconds: int = WALL_SECONDS_DEFAULT,
    memory_bytes: int = MEMORY_BYTES_DEFAULT,
) -> SandboxResult:
    """Execute a Python snippet under sandbox limits. Returns a result
    record suitable for direct persistence into `code_executions`.

    Raises nothing: any failure to even start the child is captured as
    status='error' with stderr describing the cause.
    """
    if not code or not code.strip():
        return SandboxResult(
            exit_code=2, stdout="", stderr="empty code",
            duration_ms=0, status="error",
        )
    if len(code.encode()) > CODE_BYTE_CAP:
        return SandboxResult(
            exit_code=2, stdout="",
            stderr=f"code exceeds {CODE_BYTE_CAP} bytes",
            duration_ms=0, status="error",
        )
    if cpu_seconds < 1 or cpu_seconds > 300:
        return SandboxResult(
            exit_code=2, stdout="", stderr="cpu_seconds must be 1..300",
            duration_ms=0, status="error",
        )

    with tempfile.TemporaryDirectory(prefix="sbx-") as tmpdir:
        argv = _build_command(code, tmpdir)
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tmpdir,
                env={"HOME": tmpdir, "TMPDIR": tmpdir, "PATH": "/usr/bin:/bin"},
                preexec_fn=lambda: _set_rlimits(cpu_seconds, memory_bytes),
            )
        except FileNotFoundError as e:
            # `unshare` missing or similar — surface as error, not crash.
            return SandboxResult(
                exit_code=127, stdout="",
                stderr=f"sandbox launch failed: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
                status="error",
            )
        except Exception as e:
            return SandboxResult(
                exit_code=1, stdout="",
                stderr=f"sandbox setup error: {e}",
                duration_ms=int((time.monotonic() - start) * 1000),
                status="error",
            )

        # Outer try/finally: no matter HOW communicate() exits — happy
        # path, timeout, KeyboardInterrupt, MemoryError, asyncio
        # CancelledError, BrokenPipeError — the child process gets
        # reaped. Without this, any exception path other than
        # TimeoutError leaks the subprocess.
        try:
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=wall_seconds,
                )
                status = "completed"
            except TimeoutError:
                # Wall-clock blew past the budget — SIGTERM then SIGKILL.
                # Guard each cleanup step per CLAUDE.md observability rules:
                # raises inside a finally cancel the outer coroutine.
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except (TimeoutError, ProcessLookupError):
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        await proc.wait()
                    except Exception:
                        pass
                # The pipes may have unread bytes; try one final non-blocking read.
                try:
                    stdout_b = await proc.stdout.read() if proc.stdout else b""
                except Exception:
                    stdout_b = b""
                try:
                    stderr_b = await proc.stderr.read() if proc.stderr else b""
                except Exception:
                    stderr_b = b""
                duration_ms = int((time.monotonic() - start) * 1000)
                return SandboxResult(
                    exit_code=EXIT_TIMEOUT,
                    stdout=stdout_b[:STDOUT_CAP_BYTES].decode("utf-8", errors="replace"),
                    stderr=(stderr_b[:STDERR_CAP_BYTES].decode("utf-8", errors="replace")
                            + f"\n[sandbox] wall-clock timeout at {wall_seconds}s"),
                    duration_ms=duration_ms,
                    status="timeout",
                )

            duration_ms = int((time.monotonic() - start) * 1000)
            rc = proc.returncode if proc.returncode is not None else 1
            if rc == -signal.SIGKILL or rc == EXIT_KILLED:
                status = "killed"
            elif rc != 0:
                status = "completed"  # non-zero exit is a normal "error in user code"

            stdout_cut = stdout_b[:STDOUT_CAP_BYTES]
            stderr_cut = stderr_b[:STDERR_CAP_BYTES]
            if len(stdout_b) > STDOUT_CAP_BYTES:
                stdout_cut += b"\n[sandbox] stdout truncated"
            if len(stderr_b) > STDERR_CAP_BYTES:
                stderr_cut += b"\n[sandbox] stderr truncated"

            # Capture PNG artefacts from the tempdir. Only on the happy
            # path — timeout / killed runs skip the scan (their tempdir
            # state is undefined). Failure to scan never crashes the run.
            artifacts: list[dict[str, Any]] = []
            if status == "completed":
                try:
                    artifacts, art_truncated = _scan_artifacts(tmpdir)
                    if art_truncated:
                        stderr_cut += b"\n[sandbox] artifact truncated"
                except Exception as e:
                    logger.warning("sandbox artifact scan failed: %s", e)

            return SandboxResult(
                exit_code=rc,
                stdout=stdout_cut.decode("utf-8", errors="replace"),
                stderr=stderr_cut.decode("utf-8", errors="replace"),
                duration_ms=duration_ms,
                status=status,
                artifacts=artifacts,
            )
        finally:
            # Reaper: kill + wait if the child is still alive. Only fires
            # on exception paths the inner try/except didn't already drain
            # (CancelledError, MemoryError, KeyboardInterrupt, etc.).
            # Each step guarded — a raise here would mask the original
            # exception.
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                except Exception as e:
                    logger.warning("sandbox reaper kill failed: %s", e)
                try:
                    await proc.wait()
                except Exception as e:
                    logger.warning("sandbox reaper wait failed: %s", e)


def summary(result: SandboxResult) -> dict[str, Any]:
    """Convenience: convert a SandboxResult to a JSON-serialisable dict
    matching what the agent tool exposes. Includes the full artefact
    payloads (b64). Callers that want metadata-only — e.g.
    `list_code_executions` — should strip `b64` from each artefact."""
    return {
        "exit_code": result.exit_code,
        "status": result.status,
        "duration_ms": result.duration_ms,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "artifacts": result.artifacts,
    }


# Compatibility re-export so tests + the api wrapper can use either
# the dataclass or the dict shape.
__all__ = [
    "CODE_BYTE_CAP",
    "CPU_SECONDS_DEFAULT",
    "EXIT_TIMEOUT",
    "MEMORY_BYTES_DEFAULT",
    "STDERR_CAP_BYTES",
    "STDOUT_CAP_BYTES",
    "WALL_SECONDS_DEFAULT",
    "SandboxResult",
    "run_python",
    "summary",
]


# Silence the unused-import warning for `os` — kept for documentation
# of the os.setrlimit dependency tree even though `resource` does the
# actual work.
_ = os
