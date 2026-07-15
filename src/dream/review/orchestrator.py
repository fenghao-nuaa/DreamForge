"""Best-effort background review orchestration modeled on Hermes."""

from dream.events import TaskCompletedEvent
from dream.review.backend import ReviewBackend
from dream.review.models import ReviewRequest, ReviewResult
from dream.snapshots import ContextSnapshot


class BackgroundReviewOrchestrator:
    def __init__(self, backend: ReviewBackend) -> None:
        self.backend = backend

    def review(
        self,
        event: TaskCompletedEvent,
        *,
        allowed_tools: frozenset[str],
        snapshot: ContextSnapshot | None = None,
    ) -> ReviewResult:
        if event.interrupted or not event.final_response:
            return ReviewResult(
                actions=(), summary="Interrupted or empty task was not reviewed.", status="skipped"
            )
        transcript_text = "\n".join(
            f"{message.get('role', 'unknown')}: {message.get('content', '')}"
            for message in event.transcript
        )
        request = ReviewRequest(
            event_id=event.event_id,
            transcript_text=transcript_text,
            final_response=event.final_response,
            allowed_tools=allowed_tools,
            current_user_profile=(
                snapshot.files[f"users/{event.scope.user_id}/USER.md"].content
                if snapshot is not None
                else ""
            ),
            current_decision_rules=(
                snapshot.files["DECISION_RULES.md"].content
                if snapshot is not None
                else ""
            ),
            current_decision_cards=(
                tuple(
                    snapshot_file.content
                    for key, snapshot_file in sorted(snapshot.files.items())
                    if key.startswith("decision-cards/") and key.endswith(".md")
                )
                if snapshot is not None
                else ()
            ),
        )
        try:
            result = self.backend.review(request)
        except Exception as exc:  # Background failure must not escape to foreground.
            return ReviewResult(
                actions=(),
                summary="Background review failed.",
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
        filtered = tuple(
            action for action in result.actions if action.tool_name in allowed_tools
        )
        return ReviewResult(
            actions=filtered,
            summary=result.summary,
            status=result.status,
            error=result.error,
        )
