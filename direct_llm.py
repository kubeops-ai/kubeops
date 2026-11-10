"""Temporary direct LLM mode used while MCP and RAG are disabled."""

import os
import json

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate


def create_model():
    """Create the configured language model."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY in the kubeops .env file.")
    return ChatOpenAI(
        model="gpt-5.6-luna",
        api_key=api_key,
        use_responses_api=True,
    )


def ask_llm(question: str, history=None) -> str:
    """Use a simple LangChain prompt chain without tools or RAG."""
    messages = []
    for item in history or []:
        messages.append((item["role"], item["content"]))

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a helpful Kubernetes operations assistant. "
                "Answer the user's question clearly and directly.",
            ),
            *messages,
            ("human", question),
        ]
    )
    chain = prompt | create_model()
    response = chain.invoke({})
    # Some Responses API versions return structured content instead of text.
    if isinstance(response.content, str):
        return response.content
    if isinstance(response.content, list):
        text_parts = []
        for item in response.content:
            if isinstance(item, dict) and item.get("text"):
                text_parts.append(str(item["text"]))
            elif isinstance(item, str):
                text_parts.append(item)
        if text_parts:
            return "\n".join(text_parts)
    return json.dumps(response.content, ensure_ascii=False)
