"""Insight Agent that discovers its tools from the KubeOps MCP server."""

import asyncio
import json
import logging
import os
from typing import Any, Optional

from langchain_core.tools import StructuredTool
from mcp import Client
from pydantic import create_model

from agent_workflow import run_read_only_agent


logger = logging.getLogger("kubeops.insight")


def _server_url() -> str:
    """Read the MCP URL when it is needed, after .env has been loaded."""
    return os.getenv("KUBEOPS_MCP_URL", "http://127.0.0.1:8000/mcp")


def _python_type(schema: dict[str, Any]) -> type:
    """Convert common JSON Schema types into simple Python types."""
    schema_type = schema.get("type", "string")
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }.get(schema_type, str)


async def _discover_mcp_tools() -> list[Any]:
    async with Client(_server_url()) as mcp_client:
        result = await mcp_client.list_tools()
    return result.tools


async def _call_mcp_tool(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    async with Client(_server_url()) as mcp_client:
        result = await mcp_client.call_tool(tool_name, arguments)

    if result.is_error:
        raise RuntimeError(f"MCP tool failed: {tool_name}")

    if result.structured_content and "result" in result.structured_content:
        return result.structured_content["result"]

    for content in result.content:
        if getattr(content, "text", None):
            value = json.loads(content.text)
            return value if isinstance(value, list) else [str(value)]
    return []


def _make_langchain_tool(mcp_tool: Any) -> StructuredTool:
    """Create a LangChain tool from one MCP tool description."""
    # MCP SDK versions use either snake_case or camelCase for this field.
    schema = getattr(mcp_tool, "input_schema", None)
    if schema is None:
        schema = getattr(mcp_tool, "inputSchema", None)
    schema = schema or {}
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields = {}

    for name, property_schema in properties.items():
        value_type = _python_type(property_schema)
        default = property_schema.get("default", ... if name in required else None)
        if name not in required:
            value_type = Optional[value_type]
        fields[name] = (value_type, default)

    arguments_model = create_model(f"{mcp_tool.name.title()}Arguments", **fields)

    def call_discovered_tool(**arguments):
        logger.info("Calling discovered MCP tool %s.", mcp_tool.name)
        return asyncio.run(_call_mcp_tool(mcp_tool.name, arguments))

    return StructuredTool.from_function(
        func=call_discovered_tool,
        name=mcp_tool.name,
        description=mcp_tool.description or f"Call MCP tool {mcp_tool.name}.",
        args_schema=arguments_model,
    )


def run_insight_agent(model, question: str) -> str:
    """Discover MCP tools and let LangGraph use the discovered tool schemas."""
    discovered_tools = asyncio.run(_discover_mcp_tools())
    # Insight must not receive admin-only troubleshooting tools.
    mcp_tools = [
        mcp_tool
        for mcp_tool in discovered_tools
        if mcp_tool.name != "ping" and not mcp_tool.name.startswith("diagnose_")
    ]
    tools = [_make_langchain_tool(mcp_tool) for mcp_tool in mcp_tools]
    headings = {tool.name: f"{tool.name.replace('_', ' ').title()}:" for tool in tools}
    logger.info("Discovered MCP tools: %s", ", ".join(tool.name for tool in tools))

    instructions = (
        "You are the KubeOps Insight Agent for read-only cluster lookups. "
        "Use the discovered MCP tool that matches the user's request. "
        "If the tools cannot retrieve the requested data, explain that limitation. "
        "Decline requests to change the cluster. Never invent live results. "
        "For namespace-scoped tools, an omitted namespace always means the default "
        "namespace. For example, 'list all pods' means list pods in default. "
        "Do not call list_namespaces first unless the user asks for namespaces or "
        "explicitly asks for resources across every namespace. "
        "Use every tool needed to answer all parts of the question. "
        "After using tools, give a brief summary of what they found."
    )
    return run_read_only_agent(model, question, instructions, tools, headings)
