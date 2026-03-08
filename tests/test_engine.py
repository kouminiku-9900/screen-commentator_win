from __future__ import annotations

from screen_commentator_win.engine import CommentEngine
from screen_commentator_win.models import AppConfig, CapturedFrame, CommentBatch


class StaticFrameSource:
    def __init__(self, frame: CapturedFrame) -> None:
        self.frame = frame

    def grab_primary_display(self) -> CapturedFrame:
        return self.frame


class FakeInferenceClient:
    def __init__(self, batch: CommentBatch) -> None:
        self.batch = batch
        self.prompts: list[str] = []

    def generate_comments(self, prompt: str, image_base64: str) -> CommentBatch:
        self.prompts.append(prompt)
        return self.batch


class ManualClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class DeterministicRandom:
    def random(self) -> float:
        return 0.0

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2.0

    def choice(self, seq):
        return seq[0]


def test_capture_cycle_schedules_and_releases_comments() -> None:
    clock = ManualClock()
    statuses: list[str] = []
    frame = CapturedFrame(
        jpeg_base64="QUJD",
        thumbnail_rgb=b"\x10" * 12,
        width=2,
        height=2,
    )
    inference = FakeInferenceClient(
        CommentBatch(comments=["ここすき", "草"], mood="funny", excitement=7)
    )
    engine = CommentEngine(
        config=AppConfig(),
        inference_client=inference,
        frame_source=StaticFrameSource(frame),
        on_status=statuses.append,
        on_comment=lambda _: None,
        clock=clock,
        random_source=DeterministicRandom(),
    )

    engine.capture_once()
    assert "以下のJSON形式" in inference.prompts[0]
    assert engine.release_due_comments() == []

    clock.advance(5.0)
    released = engine.release_due_comments()

    assert [comment.text for comment in released] == ["ここすき", "草"]
    assert statuses[-1] == "Running | mood: funny | excitement: 7 | comments: 2"
