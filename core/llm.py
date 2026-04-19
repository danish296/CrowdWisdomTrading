"""
OpenRouter client + a Hermes-style tool-calling agent loop.

`hermes-agent` is not a published pip package; the design here mirrors
NousResearch's Hermes Function-Calling pattern (system prompt + JSON tool
schema + iterative tool-use loop) and targets Hermes models hosted on
OpenRouter. The loop is model-agnostic — any OpenRouter-supported model
that respects the tool schema will work.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings
from core.logger import audit, get_logger

log = get_logger("llm")


# --------------------------------------------------------------------------- #
# Client                                                                      #
# --------------------------------------------------------------------------- #

_client: Optional[OpenAI] = None


def get_client() -> Optional[OpenAI]:
    """Return a cached OpenRouter client, or None if no key is configured."""
    global _client
    if _client is not None:
        return _client
    if not settings.has_llm:
        return None
    _client = OpenAI(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": settings.openrouter_referer,
            "X-Title": settings.openrouter_title,
        },
    )
    return _client


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=12),
    reraise=True,
)
def chat(
    messages: List[Dict[str, Any]],
    *,
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> Any:
    """Single chat completion (with retries)."""
    client = get_client()
    if client is None:
        raise RuntimeError("No OPENROUTER_API_KEY set; LLM unavailable.")
    return client.chat.completions.create(
        model=settings.openrouter_model,
        messages=messages,
        tools=tools,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# --------------------------------------------------------------------------- #
# Hermes-style agent loop                                                     #
# --------------------------------------------------------------------------- #


class HermesTool:
    """A single callable tool exposed to the model."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        fn: Callable[..., Any],
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.fn = fn

    def to_openai_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def run_hermes_loop(
    system_prompt: str,
    user_prompt: str,
    tools: List[HermesTool],
    *,
    max_iters: int = 5,
    temperature: float = 0.2,
) -> str:
    """
    Run the Hermes tool-calling loop:

        user -> [model thinks] -> tool_calls -> tool_results -> ... -> answer

    Returns the model's final text answer. If no LLM is configured, the
    caller should not invoke this — agents have deterministic fallbacks.
    """
    if not get_client():
        raise RuntimeError("Hermes loop requires OPENROUTER_API_KEY.")

    tool_map = {t.name: t for t in tools}
    schema = [t.to_openai_schema() for t in tools]
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for step in range(max_iters):
        resp = chat(messages, tools=schema or None, temperature=temperature)
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        # Persist assistant turn (must include tool_calls if present)
        assistant_turn: Dict[str, Any] = {
            "role": "assistant",
            "content": msg.content or "",
        }
        if tool_calls:
            assistant_turn["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ]
        messages.append(assistant_turn)

        if not tool_calls:
            audit("hermes_done", step=step, content_len=len(msg.content or ""))
            return msg.content or ""

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool = tool_map.get(name)
            if tool is None:
                result: Any = {"error": f"unknown tool: {name}"}
            else:
                try:
                    result = tool.fn(**args)
                except Exception as exc:  # noqa: BLE001
                    log.exception("Tool %s failed", name)
                    result = {"error": f"{type(exc).__name__}: {exc}"}
            audit("hermes_tool", step=step, tool=name, args=args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": json.dumps(result, default=str)[:6000],
                }
            )

    # Out of iterations — ask for a final summary.
    messages.append(
        {"role": "user", "content": "Stop using tools and give the final answer."}
    )
    resp = chat(messages, temperature=temperature)
    return resp.choices[0].message.content or ""
