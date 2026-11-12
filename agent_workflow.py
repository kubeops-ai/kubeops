import json
import logging
import os
from uuid import uuid4
from pathlib import Path

from kubernetes import config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command, interrupt


MAX_MODEL_CALLS = 10
logger = logging.getLogger("kubeops.workflow")
_postgres_checkpointer = None
_postgres_context = None


class HumanApprovalRequired(Exception):
    """Raised so a web caller can display the HITL decision in its UI."""

    def __init__(self, graph, state, config, message):
        super().__init__(message)
        self.graph = graph
        self.state = state
        self.config = config
        self.message = message


def get_checkpointer():
    """Use PostgreSQL for history when KUBEOPS_POSTGRES_URL is configured."""
    global _postgres_checkpointer, _postgres_context
    database_url = os.getenv("KUBEOPS_POSTGRES_URL", "").strip()
    if not database_url:
        logger.warning("KUBEOPS_POSTGRES_URL is not set; using memory history.")
        return MemorySaver()
    if _postgres_checkpointer is None:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError as error:
            raise RuntimeError(
                "Install langgraph-checkpoint-postgres to use PostgreSQL history."
            ) from error
        _postgres_context = PostgresSaver.from_conn_string(database_url)
        _postgres_checkpointer = _postgres_context.__enter__()
        _postgres_checkpointer.setup()
        logger.info("PostgreSQL checkpointer is ready.")
    return _postgres_checkpointer


def delete_chat_history(chat_id: str):
    """Delete one chat's checkpoints from PostgreSQL."""
    checkpointer = get_checkpointer()
    delete_thread = getattr(checkpointer, "delete_thread", None)
    if delete_thread:
        delete_thread(chat_id)


class RequestState(MessagesState):
    # MessagesState keeps the conversation. This adds a per-request call count.
    model_calls: int
    approved: bool


def open_kubernetes_client(kubeconfig: Path):
    # Each agent passes its own fixed path. A missing file stops the request.
    logger.info("Opening Kubernetes client with %s.", kubeconfig.name)
    try:
        return config.new_client_from_config(
            config_file=str(kubeconfig), persist_config=False
        )
    except ConfigException:
        raise SystemExit(f"Could not load kubeconfig: {kubeconfig}") from None


def run_read_only_agent(
    model, question, instructions, tools, headings, require_human_approval=False,
    chat_id=None,
    interactive_approval=True,
    allow_mutations=False,
):
    # Each agent supplies its own prompt and allowed tools.
    tools_by_name = {registered_tool.name: registered_tool for registered_tool in tools}
    model_with_tools = model.bind_tools(tools, parallel_tool_calls=False)
    messages = [("system", instructions), ("human", question)]
    logger.info("Sending the question and tool definitions to the model.")
    logger.info("Tools offered to the model: %s", ", ".join(tools_by_name))

    graph = build_request_graph(
        model_with_tools,
        tools_by_name,
        headings,
        require_human_approval=require_human_approval,
        checkpointer=get_checkpointer(),
        allow_mutations=allow_mutations,
    )
    # Use a fresh checkpoint thread for each question turn. Reusing one thread
    # after a failed tool call can make the model resume an incomplete exchange.
    # The application chat_id still groups all turns in PostgreSQL.
    thread_id = f"{chat_id}:{uuid4().hex}" if chat_id else uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}
    state = graph.invoke({"messages": messages, "model_calls": 0}, config)

    # A Troubleshooting request pauses here until a human approves the admin read.
    while "__interrupt__" in state:
        request = state["__interrupt__"][0].value
        if not interactive_approval:
            raise HumanApprovalRequired(
                graph, state, config, request["message"]
            )
        print(request["message"], flush=True)
        decision = input("Approve? Type yes or no: ").strip().lower()
        state = graph.invoke(Command(resume=decision), config)

    return state["messages"][-1].text


def build_request_graph(
    model_with_tools, tools_by_name, headings, require_human_approval=False,
    checkpointer=None,
    allow_mutations=False,
):
    def call_model(state: RequestState):
        call_number = state["model_calls"] + 1
        if call_number > MAX_MODEL_CALLS:
            logger.warning("Stopped after reaching the model call limit.")
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "The investigation reached its tool-call limit. "
                            "Please use the evidence already collected."
                        )
                    )
                ],
                "model_calls": call_number,
            }

        logger.info("LangGraph model step (%d/%d).", call_number, MAX_MODEL_CALLS)
        response = model_with_tools.invoke(state["messages"])
        return {"messages": [response], "model_calls": call_number}

    def choose_next_step(state: RequestState):
        # Route to the tools if the model requests a call. Otherwise finish.
        if state["messages"][-1].tool_calls:
            requested_tools = state["messages"][-1].tool_calls
            if any(
                call["name"].startswith("troubleshoot_")
                for call in requested_tools
            ) and not allow_mutations:
                return "blocked_mutation"
            needs_approval = any(
                call["name"].startswith("troubleshoot_")
                for call in requested_tools
            )
            if require_human_approval and needs_approval:
                return "approval"
            return "tools"
        return END

    def request_human_approval(state: RequestState):
        requested_tools = state["messages"][-1].tool_calls
        tool_details = "\n".join(
            f"- {call['name']} with arguments: {call['args']}"
            for call in requested_tools
        )
        approval = interrupt(
            {
                "message": (
                    "The Troubleshooting Agent wants approval for these admin "
                    "Kubernetes operation(s):\n" + tool_details
                )
            }
        )
        approved = str(approval).strip().lower() in {"yes", "y"}
        if not approved:
            logger.info("Human denied the Troubleshooting Agent tool call.")
            return {
                "approved": False,
                "messages": [
                    AIMessage(
                        content=(
                            "The troubleshooting diagnostic was not run because "
                            "human approval was denied."
                        )
                    )
                ],
            }
        logger.info("Human approved the Troubleshooting Agent tool call.")
        return {"approved": True}

    def block_mutation(state: RequestState):
        logger.warning("Blocked admin mutation because the user did not request a change.")
        return {
            "messages": [
                AIMessage(
                    content=(
                        "I can inspect the Kubernetes resource, but I will not make "
                        "a cluster change because the user did not explicitly request "
                        "a fix or update."
                    )
                )
            ]
        }

    def choose_after_approval(state: RequestState):
        return "tools" if state.get("approved", False) else END

    def call_tools(state: RequestState):
        logger.info("LangGraph tool step.")
        tool_results = []
        for tool_call in state["messages"][-1].tool_calls:
            result = run_tool(tool_call, tools_by_name, headings)
            tool_results.append(result)
            logger.info("Added the result of %s to the model conversation.", tool_call["name"])

        # MessagesState appends these tool results to the conversation.
        logger.info("Sending tool results back to the model.")
        return {"messages": tool_results}

    builder = StateGraph(RequestState)
    builder.add_node("model", call_model)
    builder.add_node("approval", request_human_approval)
    builder.add_node("tools", call_tools)
    builder.add_node("blocked_mutation", block_mutation)
    builder.add_edge(START, "model")
    builder.add_conditional_edges(
        "model", choose_next_step, ["approval", "tools", "blocked_mutation", END]
    )
    builder.add_conditional_edges("approval", choose_after_approval, ["tools", END])
    builder.add_edge("tools", "model")
    builder.add_edge("blocked_mutation", END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())


def run_tool(tool_call, tools_by_name, headings):
    # Look up the requested tool and run it with the model's arguments.
    logger.info(
        "Tool call requested by the model: %s, arguments: %s",
        tool_call["name"],
        tool_call["args"],
    )
    selected_tool = tools_by_name.get(tool_call["name"])
    if selected_tool is None:
        raise SystemExit(f"Unknown tool requested: {tool_call['name']}")

    try:
        results = selected_tool.invoke(tool_call["args"])
    except ApiException as error:
        logger.warning("Kubernetes API returned HTTP %s.", error.status)
        if error.status == 403:
            raise SystemExit(f"Kubernetes denied permission for {selected_tool.name}.") from None
        raise SystemExit(f"Kubernetes request failed (HTTP {error.status}).") from None

    logger.info("Tool returned %d result(s). Printing the output.", len(results))
    print(headings.get(selected_tool.name, f"{selected_tool.name}:"), flush=True)

    if not results:
        print("No results found.", flush=True)
    for result in results:
        print(f"- {result}", flush=True)

    # The call ID tells the model which request this result belongs to.
    return ToolMessage(
        content=json.dumps(results),
        tool_call_id=tool_call["id"],
        name=selected_tool.name,
    )
