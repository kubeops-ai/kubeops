import logging
from pathlib import Path

from kubernetes import client
from langchain_core.tools import tool

from agent_workflow import open_kubernetes_client, run_read_only_agent


DEVELOPER_KUBECONFIG = Path.home() / ".kube" / "k8s-developer.conf"
logger = logging.getLogger("kubeops.insight")
MAX_POD_EVENTS = 10


@tool
def list_namespaces() -> list[str]:
    """List the names of all namespaces in the live Kubernetes cluster."""
    with open_kubernetes_client(DEVELOPER_KUBECONFIG) as api_client:
        api = client.CoreV1Api(api_client)
        logger.info("Kubernetes SDK: list namespaces.")
        namespaces = api.list_namespace(_request_timeout=10)
        return sorted(namespace.metadata.name for namespace in namespaces.items)


@tool
def list_pods(namespace: str = "default") -> list[str]:
    """List all pod names in one namespace. If omitted, use the default namespace."""
    namespace = namespace.strip() or "default"
    with open_kubernetes_client(DEVELOPER_KUBECONFIG) as api_client:
        api = client.CoreV1Api(api_client)
        logger.info("Kubernetes SDK: list pods in namespace %s.", namespace)
        pods = api.list_namespaced_pod(namespace=namespace, _request_timeout=10)
        return sorted(pod.metadata.name for pod in pods.items)


@tool
def list_deployments(namespace: str = "default") -> list[str]:
    """List all deployment names in one namespace. If omitted, use default."""
    namespace = namespace.strip() or "default"
    with open_kubernetes_client(DEVELOPER_KUBECONFIG) as api_client:
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
    with open_kubernetes_client(DEVELOPER_KUBECONFIG) as api_client:
        api = client.CoreV1Api(api_client)
        logger.info("Kubernetes SDK: list services in namespace %s.", namespace)
        services = api.list_namespaced_service(
            namespace=namespace, _request_timeout=10
        )
        return sorted(service.metadata.name for service in services.items)


@tool
def list_nodes() -> list[str]:
    """List cluster nodes with readiness, roles, internal IP, and kubelet version."""
    with open_kubernetes_client(DEVELOPER_KUBECONFIG) as api_client:
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


@tool
def get_pod_logs(
    pod_name: str,
    namespace: str = "default",
    container: str = "",
    tail_lines: int = 100,
) -> list[str]:
    """Get recent logs from a pod. If omitted, use the default namespace."""
    namespace = namespace.strip() or "default"
    tail_lines = max(1, min(tail_lines, 500))
    with open_kubernetes_client(DEVELOPER_KUBECONFIG) as api_client:
        api = client.CoreV1Api(api_client)
        logger.info("Kubernetes SDK: get logs for pod %s in namespace %s.", pod_name, namespace)
        log_text = api.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container.strip() or None,
            tail_lines=tail_lines,
            timestamps=True,
            _request_timeout=10,
        )

    if not log_text.strip():
        return ["No log lines found."]
    return log_text.splitlines()


@tool
def get_pod_events(pod_name: str, namespace: str = "default") -> list[str]:
    """Get recent Kubernetes events for a named pod."""
    namespace = namespace.strip() or "default"
    with open_kubernetes_client(DEVELOPER_KUBECONFIG) as api_client:
        api = client.CoreV1Api(api_client)
        logger.info("Kubernetes SDK: get events for pod %s in namespace %s.", pod_name, namespace)
        pod = api.read_namespaced_pod(
            name=pod_name, namespace=namespace, _request_timeout=10
        )
        events = api.list_namespaced_event(
            namespace=namespace,
            field_selector=f"involvedObject.uid={pod.metadata.uid}",
            _request_timeout=10,
        )

    recent_events = sorted(
        events.items,
        key=lambda event: str(
            event.last_timestamp
            or event.event_time
            or event.metadata.creation_timestamp
        ),
        reverse=True,
    )[:MAX_POD_EVENTS]
    if not recent_events:
        return ["No events found."]

    return [
        f"{event.type or 'Unknown'} ({event.reason or 'Unknown'}): "
        f"{event.message or 'No message'}"
        for event in recent_events
    ]


def run_insight_agent(model, question: str) -> str:
    tools = [
        list_namespaces,
        list_pods,
        list_deployments,
        list_services,
        list_nodes,
        get_pod_logs,
        get_pod_events,
    ]
    headings = {
        "list_namespaces": "Namespaces:",
        "list_pods": "Pods:",
        "list_deployments": "Deployments:",
        "list_services": "Services:",
        "list_nodes": "Nodes:",
        "get_pod_logs": "Pod logs:",
        "get_pod_events": "Pod events:",
    }
    instructions = (
        "You are the KubeOps Insight Agent for read-only cluster lookups. "
        "Use the matching tool for live cluster data. "
        "If the tools cannot retrieve the requested data, explain that limitation. "
        "Decline requests to change the cluster. Never invent live results. "
        "Use list_nodes for cluster node details. "
        "Use get_pod_logs when the user asks for logs from a named pod. "
        "Use get_pod_events when the user asks for events from a named pod. "
        "If the user does not name a namespace, leave the namespace argument out. "
        "Use every tool needed to answer all parts of the question. "
        "After using tools, give a brief summary of what they found."
    )
    return run_read_only_agent(model, question, instructions, tools, headings)
