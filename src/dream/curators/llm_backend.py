"""OpenAI-compatible semantic consolidation for AI and User Curators."""

from dataclasses import dataclass
import json
from typing import Any, Protocol

from dream.hermes_compat.curator_prompts import (
    AI_CURATOR_PROMPT,
    USER_CURATOR_PROMPT,
)


@dataclass(frozen=True)
class AICurationPlan:
    decision_rules_markdown: str
    archive_card_ids: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class UserCurationPlan:
    user_profile_markdown: str
    summary: str


class SemanticCuratorBackend(Protocol):
    def curate_ai(
        self, *, cards: tuple[tuple[str, str], ...], current_rules: str
    ) -> AICurationPlan: ...

    def curate_user(self, profile: str) -> UserCurationPlan: ...


_AI_TOOL = {
    "type": "function",
    "function": {
        "name": "curate_ai_decisions",
        "description": "Return the consolidated decision rules and recoverable archives.",
        "parameters": {
            "type": "object",
            "properties": {
                "decision_rules_markdown": {"type": "string"},
                "archive_card_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "summary": {"type": "string"},
            },
            "required": [
                "decision_rules_markdown",
                "archive_card_ids",
                "summary",
            ],
            "additionalProperties": False,
        },
    },
}

_USER_TOOL = {
    "type": "function",
    "function": {
        "name": "curate_user_profile",
        "description": "Return one consolidated USER.md with all evidence citations preserved.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_profile_markdown": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["user_profile_markdown", "summary"],
            "additionalProperties": False,
        },
    },
}


def _attribute(value: object, name: str, default: object = None) -> object:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


class OpenAICuratorBackend:
    def __init__(
        self, *, client: Any, model: str, max_completion_tokens: int = 3000
    ) -> None:
        self.client = client
        self.model = model
        self.max_completion_tokens = max_completion_tokens

    def _call(self, *, prompt: str, content: str, tool: dict[str, object]) -> dict[str, object]:
        tool_name = str(tool["function"]["name"])
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": content},
            ],
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": tool_name}},
            temperature=0,
            max_completion_tokens=self.max_completion_tokens,
        )
        choices = _attribute(response, "choices", [])
        if not choices:
            raise RuntimeError("curator LLM returned no choices")
        message = _attribute(choices[0], "message")
        calls = _attribute(message, "tool_calls", []) or []
        if not calls:
            raise RuntimeError("curator LLM returned no management plan")
        function = _attribute(calls[0], "function")
        if _attribute(function, "name", "") != tool_name:
            raise RuntimeError("curator LLM returned an unexpected tool")
        payload = json.loads(str(_attribute(function, "arguments", "{}")))
        if not isinstance(payload, dict):
            raise RuntimeError("curator plan must be a JSON object")
        return payload

    def curate_ai(
        self, *, cards: tuple[tuple[str, str], ...], current_rules: str
    ) -> AICurationPlan:
        serialized_cards = "\n\n".join(
            f"<decision_card id={json.dumps(card_id)}>\n{content}\n</decision_card>"
            for card_id, content in cards
        ) or "(no active decision cards)"
        payload = self._call(
            prompt=AI_CURATOR_PROMPT,
            content=(
                f"<current_rules>\n{current_rules or '(empty)'}\n</current_rules>\n\n"
                f"{serialized_cards}"
            ),
            tool=_AI_TOOL,
        )
        return AICurationPlan(
            decision_rules_markdown=str(payload["decision_rules_markdown"]),
            archive_card_ids=tuple(str(value) for value in payload["archive_card_ids"]),
            summary=str(payload["summary"]),
        )

    def curate_user(self, profile: str) -> UserCurationPlan:
        payload = self._call(
            prompt=USER_CURATOR_PROMPT,
            content=f"<current_user_profile>\n{profile or '(empty)'}\n</current_user_profile>",
            tool=_USER_TOOL,
        )
        return UserCurationPlan(
            user_profile_markdown=str(payload["user_profile_markdown"]),
            summary=str(payload["summary"]),
        )
