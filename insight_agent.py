"""Insight Agent that discovers its tools from the KubeOps MCP server."""

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Optional

from langchain_core.tools import StructuredTool
from mcp import Client
from pydantic import create_model

from agent_workflow import run_read_only_agent


logger = logging.getLogger("kubeops.insight")


def retrieve_knowledge(question: str) -> str:
    """Retrieve documented guidance from the independent RAG service."""
    url = os.getenv("KUBEOPS_RAG_URL", "http://127.0.0.1:8100/retrieve")
    logger.info("Requesting knowledge from the independent RAG service.")
    request = urllib.request.Request(
        url,
        data=json.dumps({"question": question}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
        context = result.get("context", "")
        logger.info("RAG returned %d context characters.", len(context))
        print("===== RAG context received by KubeOps =====", flush=True)
        print(context or "No matching RAG context returned.", flush=True)
        print("===== End RAG context =====", flush=True)
        return context
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as error:
        logger.warning("RAG service unavailable: %s", error)
        return "RAG context is unavailable for this request. Use live MCP evidence."


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


async def _call_mcp_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    async with Client(_server_url()) as mcp_client:
        result = await mcp_client.call_tool(tool_name, arguments)

    if result.is_error:
        error_text = "MCP tool returned an error."
        for content in result.content:
            if getattr(content, "text", None):
                error_text = content.text
                break
        raise RuntimeError(f"{tool_name}: {error_text}")

    if result.structured_content and "result" in result.structured_content:
        return result.structured_content["result"]

    for content in result.content:
        if getattr(content, "text", None):
            try:
                return json.loads(content.text)
            except json.JSONDecodeError:
                return content.text
    return ""


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
        # LangChain may send None for an optional field. MCP tools expect the
        # same defaults as the Kubernetes functions.
        if arguments.get("namespace") is None:
            arguments["namespace"] = "default"
        if arguments.get("container") is None:
            arguments["container"] = ""
        if arguments.get("tail_lines") is None:
            arguments["tail_lines"] = 100
        try:
            result = asyncio.run(_call_mcp_tool(mcp_tool.name, arguments))
        except Exception as error:
            # A missing log stream is common for pending or failed pods.
            # Return the error to the model so it can try another tool.
            logger.warning("MCP tool %s failed: %s", mcp_tool.name, error)
            return [f"Tool {mcp_tool.name} could not complete: {error}"]

        if isinstance(result, list):
            return result
        return [str(result)]

    return StructuredTool.from_function(
        func=call_discovered_tool,
        name=mcp_tool.name,
        description=mcp_tool.description or f"Call MCP tool {mcp_tool.name}.",
        args_schema=arguments_model,
    )


def run_insight_agent(model, question: str, chat_id: str | None = None) -> str:
    """Discover MCP tools and let LangGraph use the discovered tool schemas."""
    discovered_tools = asyncio.run(_discover_mcp_tools())
    # Insight must not receive admin-only troubleshooting tools.
    mcp_tools = [
        mcp_tool
        for mcp_tool in discovered_tools
        if (
            mcp_tool.name not in {"ping", "retrieve_context"}
            and not mcp_tool.name.startswith("troubleshoot_")
            and not mcp_tool.name.startswith("diagnose_")
        )
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
        "If a tool returns an error, do not call that same tool repeatedly. "
        "Use the successful tool results and explain when logs are unavailable. "
        "After using tools, give a brief summary of what they found."
    )
    rag_context = retrieve_knowledge(question)
    instructions += (
        "\n\nDocumented RAG guidance for this request:\n"
        f"{rag_context}\n"
        "Use documented guidance together with live MCP results. "
        "Live MCP results take priority for current cluster state."
    )
    return run_read_only_agent(model, question, instructions, tools, headings, chat_id=chat_id)
