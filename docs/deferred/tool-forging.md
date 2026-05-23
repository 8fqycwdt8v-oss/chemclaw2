# Tool forging

## Status

Deferred. Trigger: v3 milestone (NL synthesis of new agent tools).

## Sketch

The agent receives a tool description in natural language, generates
Python that implements the tool, runs it in `mcp_codesandbox` (bwrap
isolation), and on success persists it as a callable. Equivalent to
chemclaw2's own `MetaTool` from the original spec §3.13.

## Why not yet

- Sandbox is in place (PR #125) but the persisted-tool path needs
  audit-log + owner-scope + a strict `tool_permissions` allow gate.
- LLM-generated code that runs unattended is a class of risk we
  haven't measured. CLAUDE.md security rules apply (fail-closed,
  audit-log every invocation, no shell=True, etc.).
- The v2 surface (existing tools) hasn't been a measured bottleneck
  for new agent behaviours.
