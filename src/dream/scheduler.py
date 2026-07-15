"""Conversation review queue and Hermes-style iteration bookkeeping."""

from collections import defaultdict, deque

from dream.events import TaskCompletedEvent
from dream.scope import ScopeIds


class DreamScheduler:
    def __init__(self, review_threshold: int = 10) -> None:
        if review_threshold < 1:
            raise ValueError("review_threshold must be positive")
        self.review_threshold = review_threshold
        self._pending: deque[TaskCompletedEvent] = deque()
        self._iterations: dict[ScopeIds, int] = defaultdict(int)

    def enqueue(self, event: TaskCompletedEvent) -> None:
        if event.interrupted or not event.final_response:
            return
        self._pending.append(event)
        self._iterations[event.scope] += max(0, event.tool_iterations)

    def pending_event_ids(self) -> tuple[str, ...]:
        return tuple(event.event_id for event in self._pending)

    def pop_pending(self) -> TaskCompletedEvent | None:
        if not self._pending:
            return None
        return self._pending.popleft()

    def scope_is_ready(self, scope: ScopeIds) -> bool:
        return self._iterations[scope] >= self.review_threshold

    def mark_review_accepted(self, scope: ScopeIds) -> None:
        self._iterations[scope] = 0
