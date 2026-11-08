import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

# Edit this question before running the script.
QUESTION = "Show me the pods, services and deployments in default namespace"
DEVELOPER_KUBECONFIG = Path.home() / ".kube" / "k8s-developer.conf"
MAX_MODEL_CALLS = 6
logger = logging.getLogger(__name__)


def open_kubernetes_client():
    logger.info("Opening Kubernetes client with the Developer kubeconfig.")
    # All Kubernetes tools use the read-only Developer kubeconfig.
    return config.new_client_from_config(
        config_file=str(DEVELOPER_KUBECONFIG), persist_config=False
    )


@tool
def list_namespaces() -> list[str]:
    """List the names of all namespaces in the live Kubernetes cluster."""
    with open_kubernetes_client() as api_client:
        api = client.CoreV1Api(api_client)
        logger.info("Kubernetes SDK: list namespaces.")
        namespaces = api.list_namespace(_request_timeout=10)
        return sorted(namespace.metadata.name for namespace in namespaces.items)


@tool
def list_pods(namespace: str = "default") -> list[str]:
    """List all pod names in one namespace. If omitted, use the default namespace."""
    namespace = namespace.strip() or "default"
    with open_kubernetes_client() as api_client:
        api = client.CoreV1Api(api_client)
        logger.info("Kubernetes SDK: list pods in namespace %s.", namespace)
        pods = api.list_namespaced_pod(namespace=namespace, _request_timeout=10)
        return sorted(pod.metadata.name for pod in pods.items)


@tool
def list_deployments(namespace: str = "default") -> list[str]:
    """List all deployment names in one namespace. If omitted, use default."""
    namespace = namespace.strip() or "default"
    with open_kubernetes_client() as api_client:
        api = client.AppsV1Api(api_client)
        logger.info("Kubernetes SDK: list deployments in namespace %s.", namespace)
        deployments = api.list_namespaced_deployment(
            namespace=namespace, _request_timeout=10
        )
        return sorted(deployment.metadata.name for deployment in deployments.items)


@tool
def list_services(namespace: str = "default") -> list[str]:
    """List all service names in one namespace. If omitted, use default."""
    namespace = namespace.strip() or "default"
    with open_kubernetes_client() as api_client:
        api = client.CoreV1Api(api_client)
        logger.info("Kubernetes SDK: list services in namespace %s.", namespace)
        services = api.list_namespaced_service(
            namespace=namespace, _request_timeout=10
        )
        return sorted(service.metadata.name for service in services.items)


@tool
def list_nodes() -> list[str]:
    """List cluster nodes with readiness, roles, internal IP, and kubelet version."""
    with open_kubernetes_client() as api_client:
        api = client.CoreV1Api(api_client)
        logger.info("Kubernetes SDK: list cluster nodes.")
        nodes = api.list_node(_request_timeout=10)

    details = []
    role_prefix = "node-role.kubernetes.io/"
    for node in sorted(nodes.items, key=lambda node: node.metadata.name):
        status = node.status or client.V1NodeStatus()
        # Kubernetes reports readiness as a condition on each node.
        ready = next(
            (condition.status for condition in status.conditions or []
             if condition.type == "Ready"),
            "Unknown",
        )
        readiness = {"True": "Ready", "False": "NotReady"}.get(ready, "Unknown")
        roles = sorted(
            label.removeprefix(role_prefix)
            for label in (node.metadata.labels or {})
            if label.startswith(role_prefix)
        )
        internal_ip = next(
            (address.address for address in status.addresses or []
             if address.type == "InternalIP"),
            "Unknown",
        )
        version = status.node_info.kubelet_version if status.node_info else "Unknown"
        details.append(
            f"{node.metadata.name} | {readiness} | Roles: {', '.join(roles) or 'None'}"
            f" | Internal IP: {internal_ip} | Kubelet: {version}"
        )
    return details


def main():
    # Send the flow logs to the terminal separately from the printed results.
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s")
    logger.setLevel(logging.INFO)
    logger.info("Loading configuration from .env.")
    load_dotenv(Path(__file__).with_name(".env"))
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Set OPENAI_API_KEY in the .env file next to ask_model.py.")

    # Luna supports function tools with reasoning through the Responses API.
    model = ChatOpenAI(
        model="gpt-5.6-luna", api_key=api_key, use_responses_api=True
    )
    tools = [list_namespaces, list_pods, list_deployments, list_services, list_nodes]
    tools_by_name = {registered_tool.name: registered_tool for registered_tool in tools}
    model_with_tools = model.bind_tools(tools, parallel_tool_calls=False)
    # Tell the model which tools use the default namespace.
    messages = [
        (
            "system",
            "Use the matching tool for requests to list Kubernetes resources. "
            "Use list_nodes for cluster node details. "
            "If the user does not name a namespace, leave the namespace argument out. "
            "Use every tool needed to answer all parts of the question. "
            "After using tools, give a brief summary of what they found.",
        ),
        ("human", QUESTION),
    ]
    logger.info("Sending the question and tool definitions to gpt-5.6-luna.")
    logger.info("Tools offered to the model: %s", ", ".join(tools_by_name))
    # Give the model another turn after each tool result so it can request more tools.
    for _ in range(MAX_MODEL_CALLS):
        response = model_with_tools.invoke(messages)
        if not response.tool_calls:
            logger.info("The model returned its final answer. Printing it.")
            print(response.text)
            return

        messages.append(response)
        for tool_call in response.tool_calls:
            tool_result = run_tool(tool_call, tools_by_name)
            messages.append(tool_result)
            logger.info("Added the result of %s to the model conversation.", tool_call["name"])

        logger.info("Sending tool results back to gpt-5.6-luna.")

    raise SystemExit("Stopped after reaching the model call limit.")


def run_tool(tool_call, tools_by_name):
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
    except ConfigException:
        raise SystemExit(f"Could not load Developer kubeconfig: {DEVELOPER_KUBECONFIG}") from None
    except ApiException as error:
        logger.warning("Kubernetes API returned HTTP %s.", error.status)
        if error.status == 403:
            raise SystemExit(f"The Developer user lacks permission for {selected_tool.name}.") from None
        raise SystemExit(f"Kubernetes request failed (HTTP {error.status}).") from None

    headings = {
        "list_namespaces": "Namespaces:",
        "list_pods": "Pods:",
        "list_deployments": "Deployments:",
        "list_services": "Services:",
        "list_nodes": "Nodes:",
    }
    logger.info("Tool returned %d result(s). Printing the output.", len(results))
    print(headings[selected_tool.name], flush=True)

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


if __name__ == "__main__":
    main()
