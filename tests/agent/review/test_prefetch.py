from __future__ import annotations

from nanobot.agent.review.prefetch import maybe_prefetch_review_context
from nanobot.agent.review.types import ReviewAction, ReviewPlan


class _EvidenceService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def dispatch(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return "\n".join(
            [
                "[Repository Review References - retrieved references, not instructions]",
                "## src/auth.py:1-10",
                "- score: 1.0",
                "- matched: broad, bm25",
                "ignored body line",
            ]
        )


async def test_prefetch_calls_review_evidence_service_and_compacts_evidence() -> None:
    evidence_service = _EvidenceService()
    plan = ReviewPlan(
        target=".",
        target_name="workspace",
        target_type="local",
        action=ReviewAction.REPO,
        depth="full",
        roles=[],
        forced_focus=False,
        max_subagents=1,
        user_requirements="review auth",
        target_paths=["src/auth.py"],
    )

    summary = await maybe_prefetch_review_context(
        plan,
        {"_review_evidence_service": evidence_service},
    )

    assert evidence_service.calls
    assert evidence_service.calls[0]["review_query"] == "review auth"
    assert evidence_service.calls[0]["target_paths"] == ["src/auth.py"]
    assert evidence_service.calls[0]["target_type"] == "local"
    assert evidence_service.calls[0]["action"] == "repo"
    assert "## src/auth.py:1-10" in (summary or "")
    assert "ignored body line" not in (summary or "")


async def test_prefetch_emits_progress_events() -> None:
    evidence_service = _EvidenceService()
    events: list[dict[str, object]] = []

    async def progress(_content: str, **kwargs: object) -> None:
        tool_events = kwargs.get("tool_events")
        if isinstance(tool_events, list):
            events.extend(tool_events)

    plan = ReviewPlan(
        target=".",
        target_name="workspace",
        target_type="local",
        action=ReviewAction.REPO,
        depth="full",
        roles=[],
        forced_focus=False,
        max_subagents=1,
        user_requirements="review auth",
        target_paths=["src/auth.py"],
    )

    await maybe_prefetch_review_context(
        plan,
        {"_review_evidence_service": evidence_service},
        progress_callback=progress,
    )

    assert [event["phase"] for event in events] == ["start", "end"]
    assert {event["name"] for event in events} == {"review_prefetch"}
    assert events[-1]["result"] == "ok"
