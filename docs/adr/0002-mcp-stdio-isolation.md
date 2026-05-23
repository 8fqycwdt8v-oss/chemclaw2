# ADR 0002: MCP stdio subprocesses for chemistry libraries

## Status

Accepted.

## Context

The agent needs RDKit (Morgan fingerprints, descriptors, SMILES
canonicalisation), DRFP (reaction fingerprints), AiZynthFinder (deep
retrosynthesis), and BOFIRE (Bayesian optimisation). These libraries
bring large native dependencies (RDKit ships C++ extensions; AiZynth
brings PyTorch + ~500 MB of policy models; BOFIRE brings torch as a
transitive).

Calling them in-process couples the API's start-up time to the slowest
import, and exposes the event loop to long-running CPU work. RDKit and
DRFP can also ignore SIGTERM until a C frame returns, which is bad if
the API needs to drain on Fly's rolling deploy.

## Decision

Each chemistry library lives in its own MCP stdio subprocess server
(`packages/mcp-servers/mcp_*`). The Claude Agent SDK launches the
subprocess on first tool call; the API process never imports RDKit or
torch.

For non-agent paths that need fingerprints (the `fp_worker`), the
worker also calls the MCP subprocess via stdio, with a per-call
`asyncio.wait_for` cap and a SIGTERM → SIGKILL backstop.

## Consequences

**Wins**
- API cold-start in seconds, not 30+ seconds (no torch import on the
  hot path).
- A wedged C frame in RDKit hangs one subprocess, not the API.
- We can ship a small base Docker image; the `[opt]` and `[retrosynth]`
  extras only install on workers that need them.

**Costs**
- One stdio round-trip per tool call (~5–20 ms overhead).
- Two-tier install: each MCP server is its own pip package with its
  own `pyproject.toml`.
- Wheel-layout bugs in MCP packages produce metadata-only wheels that
  pass `pip install` but fail at runtime. The `Dockerfile` install
  step needs the nested-package layout to be correct everywhere (fixed
  in wave-4).

## Triggers to revisit

- If the per-call overhead becomes a measured bottleneck (chat path
  doing 50+ chained tool calls), consider keeping one persistent
  subprocess per worker rather than spawning per call. The SDK supports
  long-lived stdio servers.
