"""Agent tools — Python port of packages/agent-tools/src/*.ts.

`build_chemclaw_mcp_server(user_id, session_id, session_factory)` is the
public entry point called from `api/agent/runner.py`. It assembles the
~43 in-process MCP tools from the thematic `tools_*` modules and hands
the resulting list to `claude_agent_sdk.create_sdk_mcp_server` for the
agent runtime to wire into Claude's tool surface.

Each tool body is plain async Python (kwargs in, raw-dict out) and is
adapted to the SDK contract by `tool_adapter.wrap_tool`. The SDK
contract (handler takes `args: dict`, returns
`{"content": [...], "is_error": bool}`) does not bleed into the
tool implementations.
"""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.agent.tools_campaign import build_campaign_tools
from api.agent.tools_chem import build_chem_tools
from api.agent.tools_external import build_external_tools
from api.agent.tools_investigation import build_investigation_tools
from api.agent.tools_knowledge import build_knowledge_tools


def build_chemclaw_mcp_server(
    user_id: str,
    session_id: str | None,
    session_factory: async_sessionmaker[AsyncSession],
) -> Any:
    """Build an in-process MCP server with all chemclaw2 agent tools.

    Returns the dict shape `create_sdk_mcp_server` produces — caller
    passes it to `McpSdkServerConfig(server=...)` in the agent runner.
    """
    tools = [
        *build_chem_tools(session_factory),
        *build_knowledge_tools(user_id, session_factory),
        *build_investigation_tools(user_id, session_id, session_factory),
        *build_external_tools(user_id, session_factory),
        *build_campaign_tools(user_id, session_id, session_factory),
    ]
    return create_sdk_mcp_server("chemclaw2-tools", tools=tools)
