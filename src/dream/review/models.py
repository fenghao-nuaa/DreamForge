"""Review requests, classifications, and scoped management actions."""

from dataclasses import dataclass
from enum import Enum


class ArtifactKind(str, Enum):
    DECISION_CARD = "decision_card"
    USER_PROFILE = "user_profile"
    USER_TODO = "user_todo"
    AGENT_MEMORY = "agent_memory"
    SKILL = "skill"
    WIKI_INGEST = "wiki_ingest"
    NOTHING = "nothing"


@dataclass(frozen=True)
class ReviewRequest:
    event_id: str
    transcript_text: str
    final_response: str
    allowed_tools: frozenset[str]
    current_user_profile: str = ""
    current_decision_rules: str = ""
    current_decision_cards: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewAction:
    kind: ArtifactKind
    tool_name: str
    payload: dict[str, object]
    source_event_id: str


@dataclass(frozen=True)
class ReviewResult:
    actions: tuple[ReviewAction, ...]
    summary: str
    status: str = "success"
    error: str | None = None
