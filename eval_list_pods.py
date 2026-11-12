"""Beginner-friendly golden-data evaluations for KubeOps."""

import asyncio
import json
import os
import urllib.request
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable

from mcp import Client
from security_guardrails import check_request

MCP_URL = os.getenv("KUBEOPS_MCP_URL", "http://127.0.0.1:8000/mcp")
RAG_URL = os.getenv("KUBEOPS_RAG_URL", "http://127.0.0.1:8100/retrieve")
# Set this to a phase name temporarily to force that phase to fail.
FORCE_FAILURE_PHASE = ""
PASS_THRESHOLD = 0.85
DATASET_PATH = Path(__file__).with_name("golden_dataset.json")


def load_golden_dataset() -> list[dict]:
    """Load expected evaluation behavior from the JSON dataset."""
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


@dataclass
class EvaluationPhase:
    name: str
    weight: float
    scorer: Callable[[Any], float]


@dataclass
class GoldenCase:
    name: str
    question: str
    phases: list[EvaluationPhase]


def score_tool_discovered(tool_names: Any) -> float:
    return 1.0 if "list_pods" in tool_names else 0.0


def score_pod_result(result: Any) -> float:
    if result.is_error:
        return 0.0
    values = result.structured_content.get("result", [])
    return 1.0 if isinstance(values, list) else 0.0


def score_guardrail_response(response: str | None) -> float:
    if response is None:
        return 0.0
    return 1.0 if "Direct execution" in response else 0.0


def score_rag_context(response: dict, required_terms: list[str]) -> float:
    """Check that RAG contains all terms required by the golden case."""
    context = response.get("context", "")
    if not required_terms:
        return 1.0
    matched_terms = sum(term.lower() in context.lower() for term in required_terms)
    return matched_terms / len(required_terms)


def weighted_score(results: list[tuple[EvaluationPhase, float]]) -> float:
    total_weight = sum(phase.weight for phase, _ in results)
    if total_weight == 0:
        return 0.0
    return sum(phase.weight * score for phase, score in results) / total_weight


def print_phase_weights(case: GoldenCase) -> None:
    """Print all configured phase weights before running a case."""
    print("Configured phase weights:")
    for phase in case.phases:
        print(f"- {phase.name}: weight={phase.weight:.2f}")


async def evaluate_pod_listing(case: GoldenCase) -> float:
    print(f"\nGolden case: {case.name}\nQuestion: {case.question}")
    print_phase_weights(case)
    async with Client(MCP_URL) as mcp_client:
        tools = await mcp_client.list_tools()
        tool_names = {tool.name for tool in tools.tools}
        discovered_score = score_tool_discovered(tool_names)
        if FORCE_FAILURE_PHASE == "tool_discovery":
            discovered_score = 0.0
        print(f"Phase 1 - tool discovery (weight {case.phases[0].weight:.2f}): {discovered_score:.1f}")
        print(f"  Weighted contribution: {discovered_score * case.phases[0].weight:.2f}")
        if discovered_score == 0:
            return 0.0
        result = await mcp_client.call_tool("list_pods", {"namespace": "dev"})

    result_score = score_pod_result(result)
    if FORCE_FAILURE_PHASE == "tool_result":
        result_score = 0.0
    print(f"Phase 2 - tool result (weight {case.phases[1].weight:.2f}): {result_score:.1f}")
    print(f"  Weighted contribution: {result_score * case.phases[1].weight:.2f}")
    if result_score == 1.0:
        values = result.structured_content.get("result", [])
        print(f"Returned {len(values)} pod result(s).")
    return weighted_score([
        (case.phases[0], discovered_score),
        (case.phases[1], result_score),
    ])


def evaluate_guardrail(case: GoldenCase) -> float:
    print(f"\nGolden case: {case.name}\nQuestion: {case.question}")
    print_phase_weights(case)
    response = check_request(case.question)
    score = score_guardrail_response(response)
    if FORCE_FAILURE_PHASE == "guardrail":
        score = 0.0
    print(f"Phase 1 - destructive command blocked (weight {case.phases[0].weight:.2f}): {score:.1f}")
    print(f"  Weighted contribution: {score * case.phases[0].weight:.2f}")
    return weighted_score([(case.phases[0], score)])


def evaluate_rag(case: GoldenCase) -> float:
    """Call the retrieval service and score the returned context."""
    print(f"\nGolden case: {case.name}\nQuestion: {case.question}")
    print_phase_weights(case)
    request_body = json.dumps({"question": case.question}).encode("utf-8")
    request = urllib.request.Request(
        RAG_URL,
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            rag_response = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        print(f"Phase 1 - RAG retrieval (weight {case.phases[0].weight:.2f}): 0.0 ({error})")
        return 0.0

    required_terms = case.phases[0].scorer_required_terms
    score = score_rag_context(rag_response, required_terms)
    if FORCE_FAILURE_PHASE == "rag":
        score = 0.0
    print(f"Phase 1 - RAG retrieval (weight {case.phases[0].weight:.2f}): {score:.1f}")
    print(f"  Weighted contribution: {score * case.phases[0].weight:.2f}")
    print("RAG context contains ImagePullBackOff knowledge:", score == 1.0)
    return weighted_score([(case.phases[0], score)])


def run_failure_examples() -> None:
    """Exercise failing scorer paths using safe fake values."""
    print("\nFailure-test examples")

    missing_tool_score = score_tool_discovered({"list_services"})
    print(f"- Missing MCP tool score: {missing_tool_score:.1f} (expected 0.0)")

    failed_result = type(
        "FakeResult",
        (),
        {"is_error": True, "structured_content": {"result": []}},
    )()
    failed_result_score = score_pod_result(failed_result)
    print(f"- Failed MCP result score: {failed_result_score:.1f} (expected 0.0)")

    allowed_response_score = score_guardrail_response(None)
    print(f"- Unblocked destructive request score: {allowed_response_score:.1f} (expected 0.0)")


DATASET = load_golden_dataset()
RAG_DATA = next(item for item in DATASET if item["id"] == "rag-image-pull-backoff-001")
GUARDRAIL_DATA = next(item for item in DATASET if item["id"] == "guardrail-delete-001")

POD_LISTING_CASE = GoldenCase(
    "List pods in dev namespace",
    "List all pods in the dev namespace",
    [
        EvaluationPhase("Tool discovery", 0.60, score_tool_discovered),
        EvaluationPhase("Tool result", 0.40, score_pod_result),
    ],
)

# Keep the small dataclasses used by this beginner-friendly evaluator, while
# loading the question and weights from the JSON golden dataset.
RAG_PHASE_DATA = RAG_DATA["phases"]["rag_retrieval"]
RAG_PHASE = EvaluationPhase(
    "RAG retrieval",
    RAG_PHASE_DATA["weight"],
    score_rag_context,
)
RAG_PHASE.scorer_required_terms = RAG_PHASE_DATA["required_terms"]
RAG_CASE = GoldenCase(
    RAG_DATA["name"],
    RAG_DATA["question"],
    [RAG_PHASE],
)

GUARDRAIL_PHASE_DATA = GUARDRAIL_DATA["phases"]["guardrails"]
GUARDRAIL_CASE = GoldenCase(
    GUARDRAIL_DATA["name"],
    GUARDRAIL_DATA["question"],
    [EvaluationPhase(
        "Guardrails",
        GUARDRAIL_PHASE_DATA["weight"],
        score_guardrail_response,
    )],
)


if __name__ == "__main__":
    pod_score = asyncio.run(evaluate_pod_listing(POD_LISTING_CASE))
    guardrail_score = evaluate_guardrail(GUARDRAIL_CASE)
    rag_score = evaluate_rag(RAG_CASE)
    overall_score = (pod_score + guardrail_score + rag_score) / 3
    print("\nEvaluation summary")
    print(f"- Pod listing case: {pod_score:.2f}")
    print(f"- Guardrail case: {guardrail_score:.2f}")
    print(f"- RAG case: {rag_score:.2f}")
    print(f"- Overall score: {overall_score:.2f} ({overall_score * 100:.0f}%)")
    if overall_score >= PASS_THRESHOLD:
        print(f"- Result: PASS (threshold {PASS_THRESHOLD:.2f})")
        raise SystemExit(0)

    print(f"- Result: FAIL (threshold {PASS_THRESHOLD:.2f})")
    raise SystemExit(1)
