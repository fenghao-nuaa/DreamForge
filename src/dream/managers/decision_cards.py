"""Human-readable AI decision card persistence."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re

from dream.artifacts import ArtifactVersion, AtomicArtifactStore
from dream.review.models import ArtifactKind, ReviewAction
from dream.rollback import RollbackService
from dream.scope import ScopePaths


_CARD_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class DecisionCardManager:
    def __init__(self, paths: ScopePaths) -> None:
        self.paths = paths
        self.artifacts = AtomicArtifactStore(paths.agent_root)
        self.rollback = RollbackService(paths)
        self.last_snapshot_id = ""

    def apply(self, action: ReviewAction) -> ArtifactVersion:
        if action.kind is not ArtifactKind.DECISION_CARD:
            raise ValueError("decision card manager only accepts decision cards")
        if action.tool_name != "decision_card_manage":
            raise ValueError("decision card action must use decision_card_manage")
        card_id = str(action.payload.get("id", ""))
        if not _CARD_ID.fullmatch(card_id):
            raise ValueError("invalid decision card id")
        relative = Path("decision-cards") / f"{card_id}.md"
        self.last_snapshot_id = self.rollback.capture((relative,))
        now = datetime.now(timezone.utc).isoformat()
        title = str(action.payload.get("title", "")).strip()
        scenario = str(action.payload.get("scenario", "")).strip()
        principle = str(action.payload.get("principle", "")).strip()
        outcome = str(action.payload.get("outcome", "")).strip()
        boundaries = str(action.payload.get("boundaries", "")).strip()
        signals = action.payload.get("signals", [])
        confidence = float(action.payload.get("confidence", 0.5))
        if not all((title, scenario, principle, outcome, boundaries)):
            raise ValueError("decision card fields must be non-empty")
        if not isinstance(signals, list) or not all(isinstance(item, str) for item in signals):
            raise ValueError("decision card signals must be a list of strings")
        signal_lines = "\n".join(f"- {item}" for item in signals) or "- 无"
        rendered = (
            "---\n"
            f"id: {json.dumps(card_id, ensure_ascii=False)}\n"
            "status: active\n"
            f"confidence: {confidence:.2f}\n"
            f"created_at: {json.dumps(now)}\n"
            f"updated_at: {json.dumps(now)}\n"
            "source_event_ids:\n"
            f"  - {json.dumps(action.source_event_id)}\n"
            "---\n\n"
            f"# {title}\n\n"
            f"## 使用场景\n\n{scenario}\n\n"
            f"## 决策信号\n\n{signal_lines}\n\n"
            f"## 决策原则\n\n{principle}\n\n"
            f"## 本次结果\n\n{outcome}\n\n"
            f"## 反例与边界\n\n{boundaries}\n"
        )
        return self.artifacts.write_text(relative, rendered)

