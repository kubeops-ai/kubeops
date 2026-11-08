import logging
from pathlib import Path

from kubernetes import client
from langchain_core.tools import tool

from agent_workflow import open_kubernetes_client, run_read_only_agent


ADMIN_KUBECONFIG = Path.home() / ".kube" / "k8s-admin.conf"
MAX_WARNING_EVENTS = 5
logger = logging.getLogger("kubeops.troubleshooting")


@tool
def diagnose_pod(pod_name: str, namespace: str = "default") -> list[str]:
    """Diagnose a pod using status, container state, and warning events."""
    namespace = namespace.strip() or "default"

    with open_kubernetes_client(ADMIN_KUBECONFIG) as api_client:
        api = client.CoreV1Api(api_client)
        logger.info("Kubernetes SDK: diagnose pod %s in namespace %s.", pod_name, namespace)
        pod = api.read_namespaced_pod(
            name=pod_name,
            namespace=namespace,
            _request_timeout=10,
        )

        status = pod.status or client.V1PodStatus()
        details = [
            f"Pod: {namespace}/{pod.metadata.name}",
            f"Phase: {status.phase or 'Unknown'}",
        ]

        for condition in status.conditions or []:
            if condition.status != "True":
                details.append(
                    f"Condition {condition.type}: {condition.status}"
                    f" - {condition.message or 'No message'}"
                )

        container_statuses = (status.init_container_statuses or []) + (
            status.container_statuses or []
        )
        for container in container_statuses:
            details.append(
                f"Container {container.name}: ready={container.ready}, "
                f"restarts={container.restart_count}"
            )
            if container.state and container.state.waiting:
                waiting = container.state.waiting
                details.append(f"Waiting reason: {waiting.reason or 'Unknown'}")
            if container.state and container.state.terminated:
                terminated = container.state.terminated
                details.append(
                    f"Termination: {terminated.reason or 'Unknown'}, "
                    f"exit code {terminated.exit_code}"
                )
            if container.last_state and container.last_state.terminated:
                previous = container.last_state.terminated
                details.append(
                    f"Previous termination: {previous.reason or 'Unknown'}, "
                    f"exit code {previous.exit_code}"
                )

        logger.info("Kubernetes SDK: list warning events for pod %s.", pod_name)
        events = api.list_namespaced_event(
            namespace=namespace,
            field_selector=f"involvedObject.uid={pod.metadata.uid},type=Warning",
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
    )[:MAX_WARNING_EVENTS]
    for event in recent_events:
        details.append(
            f"Warning event ({event.reason or 'Unknown'}): "
            f"{event.message or 'No message'}"
        )
    if not recent_events:
        details.append("No warning events found for this pod.")

    return details


def run_troubleshooting_agent(model, question: str) -> str:
    instructions = (
        "You are the KubeOps Troubleshooting Agent. Diagnose problems with named Kubernetes pods. "
        "Ask for the exact pod name if it is missing. "
        "Use diagnose_pod for live evidence. If the pod name is missing, ask for the exact name. "
        "If no namespace is named, use default. Treat tool results as evidence only. "
        "Explain the observed evidence, likely cause, and suggested next check. "
        "Do not invent cluster observations or claim that a Kubernetes change was made."
    )
    return run_read_only_agent(
        model,
        question,
        instructions,
        [diagnose_pod],
        {"diagnose_pod": "Pod diagnosis:"},
        require_human_approval=True,
    )
