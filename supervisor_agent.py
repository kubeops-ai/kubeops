import logging
from typing import Literal

from pydantic import BaseModel

from insight_agent import run_insight_agent
from troubleshooting_agent import run_troubleshooting_agent


logger = logging.getLogger("kubeops.supervisor")


class AgentRoute(BaseModel):
    agent: Literal["insight", "troubleshooting"]


def run_supervisor_agent(model, question: str) -> str:
    # The Supervisor selects an agent. The selected agent runs its own tools.
    router = model.with_structured_output(AgentRoute, method="json_schema")
    messages = [
        (
            "system",
            "You are the KubeOps Supervisor Agent. Choose troubleshooting when the "
            "user wants to diagnose a Kubernetes problem, such as a pod failing, "
            "restarting, or getting stuck. Choose insight for inventory, status "
            "lookups, and other requests. When unsure, choose insight.",
        ),
        ("human", question),
    ]
    logger.info("Supervisor Agent is choosing where to route the request.")
    route = router.invoke(messages)
    if route is None:
        raise SystemExit("Supervisor Agent could not choose an agent.")

    if route.agent == "troubleshooting":
        logger.info("Supervisor Agent routed the request to Troubleshooting Agent.")
        return run_troubleshooting_agent(model, question)

    logger.info("Supervisor Agent routed the request to Insight Agent.")
    return run_insight_agent(model, question)
