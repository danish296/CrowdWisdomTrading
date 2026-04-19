"""
Base class for Hermes-style agents.

Every agent has:
  - a name and role (used in logs and Hermes system prompt)
  - a deterministic `run` method that does the real work
  - an optional `narrate` method that, when an LLM is available, asks
    Hermes to add a short, human-readable commentary on the result

Agents that want full LLM tool-use can use `core.llm.run_hermes_loop`
directly; this base class is intentionally lean.
"""
from __future__ import annotations

import abc
from typing import Any

from core.llm import chat, get_client
from core.logger import audit, get_logger


class Agent(abc.ABC):
    name: str = "agent"
    role: str = "abstract"

    def __init__(self) -> None:
        self.log = get_logger(f"agent.{self.name}")

    @abc.abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def narrate(self, summary_prompt: str, max_words: int = 60) -> str:
        """Optional Hermes commentary; silently no-op if no API key."""
        if get_client() is None:
            return ""
        try:
            resp = chat(
                [
                    {
                        "role": "system",
                        "content": (
                            f"You are the {self.role} agent in a crypto-prediction "
                            "pipeline. Reply with a single crisp sentence "
                            f"(<= {max_words} words). No emojis, no preamble."
                        ),
                    },
                    {"role": "user", "content": summary_prompt},
                ],
                temperature=0.4,
                max_tokens=160,
            )
            text = (resp.choices[0].message.content or "").strip()
            audit("agent_narrate", agent=self.name, len=len(text))
            return text
        except Exception as exc:  # noqa: BLE001
            self.log.warning("Narration failed: %s", exc)
            return ""
