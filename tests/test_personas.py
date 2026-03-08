from __future__ import annotations

from screen_commentator_win.models import Persona, PromptContext
from screen_commentator_win.personas import build_smart_prompt


def test_prompt_includes_personas_recent_comments_and_length_note() -> None:
    prompt = build_smart_prompt(
        enabled_personas=[(Persona.STANDARD, 0.75), (Persona.BARRAGE, 0.25)],
        count=3,
        context=PromptContext(recent_comments=["ここすき", "草"]),
    )

    assert "Standard (75%)" in prompt
    assert "Barrage (25%)" in prompt
    assert "[最近のコメント" in prompt
    assert "ここすき, 草" in prompt
    assert "barrage系は1~5文字" in prompt
