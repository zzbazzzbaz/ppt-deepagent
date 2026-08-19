from __future__ import annotations

from agent.tools.outline import SlideOutline, submit_outline


def test_successful_outline_submission_signals_approved_generation_stage() -> None:
    """Catches the Agent receiving a success result that does not unlock its next phase."""
    result = submit_outline.func(
        title="Demo",
        markdown="# Demo",
        slides=[
            SlideOutline(
                index=1,
                title="One",
                key_points=["A", "B"],
                markdown="## One",
            )
        ],
    )

    assert result == "大纲已批准，可以开始生成演示文稿。"
