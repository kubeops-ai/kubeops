import logging
from typing import Literal

from pydantic import BaseModel

from insight_agent import run_insight_agent
from troubleshooting_agent import run_troubleshooting_agent


logger = logging.getLogger("kubeops.supervisor")


class AgentRoute(BaseModel):
    agent: Literal["insight", "troubleshooting"]


def run_supervisor_agent(
    model, question: str, chat_id: str | None = None, interactive_approval=True
) -> str:
    # Remediation verbs must use the Troubleshooting Agent because it owns the
    # admin MCP tools and the human approval flow.
    remediation_words = {
        "fix", "repair", "update", "change", "replace", "restart",
        "delete", "scale", "remediate", "apply",
    }
    question_words = set(question.lower().replace("/", " ").split())
    lower_question = question.lower()
    has_negated_change = any(
        phrase in lower_question
        for phrase in ("do not fix", "don't fix", "do not change", "don't change")
    )
    allow_mutations = bool(question_words.intersection(remediation_words)) and not has_negated_change
    if allow_mutations:
        logger.info("Supervisor detected a remediation request.")
        return run_troubleshooting_agent(
            model, question, chat_id, interactive_approval, allow_mutations=True
        )

    # The Supervisor selects an agent. The selected agent runs its own tools.
    router = model.with_structured_output(AgentRoute, method="json_schema")
    messages = [
        (
            "system",
            "You are the KubeOps Supervisor Agent. Route inventory, status, pod logs, "
            "pod events, and pod diagnosis to the Insight Agent. Route requests to "
            "change or remediate the cluster to the Troubleshooting Agent. The "
            "Troubleshooting Agent uses admin tools and requires human approval. "
            "When unsure, choose insight.",
        ),
        ("human", question),
    ]
    logger.info("Supervisor Agent is choosing where to route the request.")
    route = router.invoke(messages)
    if route is None:
        raise SystemExit("Supervisor Agent could not choose an agent.")

    if route.agent == "troubleshooting":
        logger.info("Supervisor Agent routed the request to Troubleshooting Agent.")
        return run_troubleshooting_agent(
            model, question, chat_id, interactive_approval, allow_mutations=False
        )

    logger.info("Supervisor Agent routed the request to Insight Agent.")
    return run_insight_agent(model, question, chat_id)
