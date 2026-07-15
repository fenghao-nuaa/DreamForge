from datetime import datetime, timezone

from dream.curators.registry import CuratorRegistry
from dream.events import TaskCompletedEvent
from dream.scheduler import DreamScheduler
from dream.scope import ScopeIds


def _event(event_id: str, *, interrupted: bool = False) -> TaskCompletedEvent:
    return TaskCompletedEvent(
        event_id=event_id,
        task_id=f"task-{event_id}",
        scope=ScopeIds("acme", "assistant", "alice"),
        completed_at="2026-07-15T10:00:00+08:00",
        interrupted=interrupted,
        tool_iterations=12,
        transcript=({"role": "user", "content": "I prefer concise answers"},),
        final_response="Understood." if not interrupted else "",
        source_refs=(),
    )


def test_scheduler_ignores_interrupted_conversations() -> None:
    scheduler = DreamScheduler(review_threshold=10)
    scheduler.enqueue(_event("evt-interrupted", interrupted=True))
    scheduler.enqueue(_event("evt-completed"))
    assert scheduler.pending_event_ids() == ("evt-completed",)


def test_curator_registry_runs_only_due_curators() -> None:
    class RecordingCurator:
        name = "recording"

        def __init__(self) -> None:
            self.ran = False

        def should_run(self, now: datetime) -> bool:
            return True

        def run(self) -> str:
            self.ran = True
            return "ok"

    curator = RecordingCurator()
    results = CuratorRegistry([curator]).run_due(datetime.now(timezone.utc))
    assert curator.ran is True
    assert results == {"recording": "ok"}
