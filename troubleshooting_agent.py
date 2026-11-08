"""Troubleshooting Agent backed by dynamically discovered MCP tools."""

import asyncio
import logging

from agent_workflow import run_read_only_agent
from insight_agent import _discover_mcp_tools, _make_langchain_tool


logger = logging.getLogger("kubeops.troubleshooting")


def run_troubleshooting_agent(model, question: str) -> str:
    """Discover troubleshooting tools and run them with HITL approval."""
    mcp_tools = asyncio.run(_discover_mcp_tools())
    troubleshooting_tools = [
        mcp_tool
        for mcp_tool in mcp_tools
        if mcp_tool.name.startswith("diagnose_")
    ]
    tools = [_make_langchain_tool(mcp_tool) for mcp_tool in troubleshooting_tools]
    headings = {tool.name: f"{tool.name.replace('_', ' ').title()}:" for tool in tools}
    logger.info(
        "Discovered troubleshooting MCP tools: %s",
        ", ".join(tool.name for tool in tools) or "none",
    )

    instructions = (
        "You are the KubeOps Troubleshooting Agent. Diagnose Kubernetes problems "
        "with the discovered troubleshooting MCP tools. Ask for the exact pod name "
        "if it is missing. If no namespace is named, use default. Treat tool results "
        "as evidence only. Explain the observed evidence, likely cause, and suggested "
        "next check. Do not invent cluster observations or claim that a change was made."
    )
    return run_read_only_agent(
        model,
        question,
        instructions,
        tools,
        headings,
        require_human_approval=True,
    )
