from datetime import datetime, timezone
from pathlib import Path

from dream.events import TaskCompletedEvent
from dream.scope import ScopeIds
from dream.service import DreamService


def test_service_discovers_active_scope_and_runs_curators_only_when_due(
    tmp_path: Path,
) -> None:
    service = DreamService(tmp_path)
    scope = ScopeIds("acme", "assistant", "alice")
    service.ingest_conversation(
        TaskCompletedEvent(
            event_id="evt-periodic",
            task_id="conversation-periodic",
            scope=scope,
            completed_at="2026-07-15T10:00:00+08:00",
            interrupted=False,
            tool_iterations=12,
            transcript=(
                {"role": "user", "content": "I prefer concise answers"},
                {
                    "role": "assistant",
                    "content": "verify before risky action because it is irreversible",
                },
            ),
            final_response="Verified.",
            source_refs=(),
        )
    )
    service.run_pending()
    now = datetime.now(timezone.utc)

    first = service.run_due_curators(now)
    second = service.run_due_curators(now)

    assert set(first) == {"acme/assistant/alice"}
    assert set(first["acme/assistant/alice"]) == {"ai", "user"}
    assert second == {}
