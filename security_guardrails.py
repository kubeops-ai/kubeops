"""Project-level checks that run before Supervisor and LangGraph."""


MAX_QUESTION_LENGTH = 2000
READ_ONLY_COMMANDS = {"get", "describe", "logs", "events", "top", "explain"}
MUTATING_COMMANDS = {
    "delete", "apply", "create", "patch", "edit", "scale", "exec", "attach",
    "drain", "cordon", "uncordon", "taint", "rollout",
}
SHELL_REQUESTS = ("execute shell", "run a command", "run this command", "run bash", "command line")
POD_ACCESS_REQUESTS = ("exec into", "attach to pod", "port-forward", "port forward")
PROMPT_INJECTION_REQUESTS = (
    "ignore previous instructions",
    "ignore system prompt",
    "reveal your system prompt",
    "bypass your safety rules",
)
DESTRUCTIVE_WORDS = ("delete", "remove", "scale down", "drain", "restart")
PROTECTED_NAMESPACES = ("kube-system", "kube-public", "kube-node-lease")
SENSITIVE_WORDS = (
    "secret", "password", "token", "credential", "api key", "private key",
    "kubeconfig", "environment variable", "env var", "configmap",
)


def check_request(question: str) -> str | None:
    """Return a safe response when a request fails a project-level check."""
    normalized_question = " ".join(question.lower().split())
    if not normalized_question:
        return "Please enter a Kubernetes question."
    if len(normalized_question) > MAX_QUESTION_LENGTH:
        return "The question is too long. Please keep it below 2,000 characters."
    if any(word in normalized_question for word in SENSITIVE_WORDS):
        return (
            "Sensitive Kubernetes data is not available through this application. "
            "Only non-sensitive read-only information can be requested."
        )
    command_result = classify_kubectl_request(normalized_question)
    if command_result == "read_only":
        return None
    if command_result == "mutation":
        return (
            "Direct execution of disruptive kubectl commands is not allowed. "
            "Request the Kubernetes change in plain language so the approved MCP "
            "workflow can validate it and ask for HITL approval."
        )
    if command_result == "blocked":
        return "This kubectl operation is not supported by the approved Kubernetes tools."
    if any(pattern in normalized_question for pattern in SHELL_REQUESTS):
        return (
            "Direct command-line execution is not allowed. I cannot run kubectl "
            "commands from the chat. Ask me to inspect a resource or request a "
            "supported Kubernetes operation through the approved MCP workflow."
        )
    if any(pattern in normalized_question for pattern in POD_ACCESS_REQUESTS):
        return "Interactive pod access is disabled. Ask for read-only logs or events instead."
    if any(pattern in normalized_question for pattern in PROMPT_INJECTION_REQUESTS):
        return "I cannot follow requests to bypass the application's safety instructions."
    has_destructive_word = any(
        word in normalized_question.split() for word in DESTRUCTIVE_WORDS
    )
    if has_destructive_word and any(
        phrase in normalized_question
        for phrase in ("all namespaces", "every namespace", "cluster-wide")
    ):
        return "Cluster-wide changes are blocked. Specify one namespace and one resource."
    if has_destructive_word and any(
        f"{namespace} namespace" in normalized_question
        for namespace in PROTECTED_NAMESPACES
    ):
        return "Changes in protected Kubernetes system namespaces are blocked."
    if has_destructive_word and "namespace" not in normalized_question:
        return "Specify the namespace before requesting a resource change."
    if has_destructive_word and normalized_question.count(" and ") > 1:
        return "Handle one resource change per request."
    # Normal single-resource changes continue to the agent workflow. The
    # Troubleshooting Agent displays the exact change and obtains HITL approval.
    return None


def classify_kubectl_request(question: str) -> str | None:
    """Classify explicit kubectl text without executing the command."""
    words = question.replace('"', "").replace("'", "").split()
    if "kubectl" not in words:
        return None
    kubectl_index = words.index("kubectl")
    if kubectl_index + 1 >= len(words):
        return "blocked"
    operation = words[kubectl_index + 1]
    if operation in READ_ONLY_COMMANDS:
        return "read_only"
    if operation in MUTATING_COMMANDS:
        return "mutation"
    return "blocked"
