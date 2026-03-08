from __future__ import annotations

import heapq
import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from .capture import ScreenCaptureService
from .contracts import FrameSource, InferenceClient
from .models import (
    AppConfig,
    CommentColor,
    CommentStyle,
    PendingComment,
    Persona,
    PromptContext,
)
from .personas import build_smart_prompt


logger = logging.getLogger(__name__)
StatusCallback = Callable[[str], None]
CommentCallback = Callable[[PendingComment], None]


class RandomSource(Protocol):
    def random(self) -> float: ...

    def uniform(self, a: float, b: float) -> float: ...

    def choice(self, seq): ...


@dataclass(order=True, slots=True)
class ScheduledComment:
    release_at: float
    pending: PendingComment


class CommentEngine:
    def __init__(
        self,
        config: AppConfig,
        inference_client: InferenceClient,
        on_status: StatusCallback,
        on_comment: CommentCallback,
        frame_source: FrameSource | None = None,
        clock: Callable[[], float] | None = None,
        random_source: RandomSource | None = None,
    ) -> None:
        self.config = config
        self.inference_client = inference_client
        self.on_status = on_status
        self.on_comment = on_comment
        self.frame_source = frame_source or ScreenCaptureService(
            thumbnail_size=config.capture.thumbnail_size,
            jpeg_quality=config.capture.jpeg_quality,
        )
        self.clock = clock or time.monotonic
        self.random_source = random_source or random.Random()
        self._stop_event = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._release_thread: threading.Thread | None = None
        self._release_lock = threading.Lock()
        self._scheduled: list[ScheduledComment] = []
        self._previous_thumbnail: bytes | None = None
        self._recent_comment_texts: list[str] = []
        self._last_mood = "general"
        self._last_excitement = 5

    def start(self) -> None:
        if self._capture_thread and self._capture_thread.is_alive():
            return

        self._stop_event.clear()
        self._capture_thread = threading.Thread(target=self._run_capture_loop, daemon=True)
        self._release_thread = threading.Thread(target=self._run_release_loop, daemon=True)
        self._capture_thread.start()
        self._release_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._capture_thread:
            self._capture_thread.join(timeout=5)
        if self._release_thread:
            self._release_thread.join(timeout=5)
        with self._release_lock:
            self._scheduled.clear()

    def _run_capture_loop(self) -> None:
        while not self._stop_event.is_set():
            started_at = self.clock()
            try:
                self.capture_once()
            except Exception as exc:
                logger.exception("Capture loop error")
                self.on_status(f"Generation error: {exc}")

            elapsed = self.clock() - started_at
            sleep_for = max(0.1, self.config.capture.interval_sec - elapsed)
            self._stop_event.wait(sleep_for)

    def capture_once(self) -> None:
        frame = self.frame_source.grab_primary_display()
        change_level = self._compute_change_level(frame.thumbnail_rgb)

        personas = self._enabled_personas_with_weights()
        if not personas:
            personas = [(Persona.STANDARD, 1.0)]

        count = max(1, int(self.config.comments.base_count * (self._last_excitement / 5.0)))
        prompt = build_smart_prompt(
            enabled_personas=personas,
            count=count,
            context=PromptContext(recent_comments=self._recent_comment_texts),
        )
        batch = self.inference_client.generate_comments(prompt=prompt, image_base64=frame.jpeg_base64)
        self._last_mood = batch.mood
        self._last_excitement = batch.excitement
        self._previous_thumbnail = frame.thumbnail_rgb
        self._recent_comment_texts.extend(batch.comments)
        if len(self._recent_comment_texts) > self.config.comments.recent_history:
            overflow = len(self._recent_comment_texts) - self.config.comments.recent_history
            del self._recent_comment_texts[:overflow]

        self._schedule_comments(batch.comments, change_level)
        self.on_status(
            f"Running | mood: {batch.mood} | excitement: {batch.excitement} | comments: {len(batch.comments)}"
        )

    def _schedule_comments(self, comments: list[str], change_level: float) -> None:
        if not comments:
            return

        interval = self.config.capture.interval_sec / (len(comments) + 1)
        now = self.clock()
        with self._release_lock:
            for index, text in enumerate(comments):
                persona = self._select_persona()
                style = self._assign_style(persona)
                color = self._assign_color(persona, self._last_mood, style)
                speed = self.random_source.uniform(0.6, 1.5)
                if change_level > 0.12:
                    speed *= 0.9
                jitter = self.random_source.uniform(-0.3, 0.3) * interval
                delay = interval * (index + 1) + jitter
                heapq.heappush(
                    self._scheduled,
                    ScheduledComment(
                        release_at=now + max(0.0, delay),
                        pending=PendingComment(
                            text=text,
                            style=style,
                            color=color,
                            speed_multiplier=speed,
                        ),
                    ),
                )

    def release_due_comments(self, now: float | None = None) -> list[PendingComment]:
        release_now = self.clock() if now is None else now
        due_comments: list[PendingComment] = []
        with self._release_lock:
            while self._scheduled and self._scheduled[0].release_at <= release_now:
                due_comments.append(heapq.heappop(self._scheduled).pending)
        return due_comments

    def _run_release_loop(self) -> None:
        while not self._stop_event.is_set():
            for pending in self.release_due_comments():
                self.on_comment(pending)
            self._stop_event.wait(0.05)

    def _enabled_personas_with_weights(self) -> list[tuple[Persona, float]]:
        enabled = [(persona, cfg.weight) for persona, cfg in self.config.personas.items() if cfg.enabled]
        total = sum(weight for _, weight in enabled)
        if total <= 0:
            return []
        return [(persona, weight / total) for persona, weight in enabled]

    def _select_persona(self) -> Persona:
        enabled = self._enabled_personas_with_weights()
        if not enabled:
            return Persona.STANDARD
        roll = self.random_source.random()
        cumulative = 0.0
        for persona, weight in enabled:
            cumulative += weight
            if roll <= cumulative:
                return persona
        return enabled[-1][0]

    def _assign_style(self, persona: Persona) -> CommentStyle:
        if persona is Persona.BARRAGE:
            roll = self.random_source.random()
            if roll < 0.7:
                return CommentStyle.SCROLL
            if roll < 0.9:
                return CommentStyle.TOP
            return CommentStyle.BOTTOM

        if self._last_excitement >= 7 and self.random_source.random() < 0.12:
            return CommentStyle.TOP if self.random_source.random() < 0.5 else CommentStyle.BOTTOM
        return CommentStyle.SCROLL

    def _assign_color(self, persona: Persona, mood: str, style: CommentStyle) -> CommentColor:
        if style is CommentStyle.SCROLL:
            return CommentColor.WHITE
        if persona is Persona.BARRAGE:
            return self.random_source.choice(list(CommentColor))

        mood_palette = {
            "excitement": [CommentColor.RED, CommentColor.ORANGE, CommentColor.YELLOW],
            "funny": [CommentColor.GREEN, CommentColor.CYAN, CommentColor.YELLOW],
            "beautiful": [CommentColor.CYAN, CommentColor.BLUE, CommentColor.PURPLE],
            "cute": [CommentColor.PINK, CommentColor.PURPLE],
        }
        return self.random_source.choice(mood_palette.get(mood, [CommentColor.WHITE]))

    def _compute_change_level(self, current_thumbnail: bytes) -> float:
        if not self._previous_thumbnail or len(self._previous_thumbnail) != len(current_thumbnail):
            return 0.05
        total_diff = 0
        for current, previous in zip(current_thumbnail, self._previous_thumbnail, strict=True):
            total_diff += abs(current - previous)
        return total_diff / (len(current_thumbnail) * 255)
