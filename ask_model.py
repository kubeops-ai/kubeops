import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from supervisor_agent import run_supervisor_agent
from security_guardrails import check_request

# Edit this question before running the script.
QUESTION = "list out all the namesapces in the cluster"
logger = logging.getLogger("kubeops")


def main():
    # Send the flow logs to the terminal separately from the printed results.
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s")
    logger.setLevel(logging.INFO)
    logger.info("Loading configuration from .env.")
    load_dotenv(Path(__file__).with_name(".env"))
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Set OPENAI_API_KEY in the .env file next to ask_model.py.")

    blocked_response = check_request(QUESTION)
    if blocked_response:
        print(blocked_response)
        return

    model = ChatOpenAI(
        model="gpt-5.6-luna", api_key=api_key, use_responses_api=True
    )
    logger.info("Starting Supervisor Agent with %s.", model.model_name)
    answer = run_supervisor_agent(model, QUESTION)
    logger.info("Printing the final answer.")
    print(answer)


if __name__ == "__main__":
    main()
