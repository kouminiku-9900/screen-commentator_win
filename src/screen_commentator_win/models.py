from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal


Mood = Literal["excitement", "funny", "surprise", "cute", "boring", "beautiful", "general"]
VALID_MOODS: set[str] = {
    "excitement",
    "funny",
    "surprise",
    "cute",
    "boring",
    "beautiful",
    "general",
}


class Persona(str, Enum):
    STANDARD = "standard"
    MEME = "meme"
    CRITIC = "critic"
    INSTRUCTOR = "instructor"
    BARRAGE = "barrage"

    @property
    def display_name(self) -> str:
        return {
            Persona.STANDARD: "Standard",
            Persona.MEME: "Meme",
            Persona.CRITIC: "Critic",
            Persona.INSTRUCTOR: "Instructor",
            Persona.BARRAGE: "Barrage",
        }[self]


class CommentStyle(str, Enum):
    SCROLL = "scroll"
    TOP = "top"
    BOTTOM = "bottom"


class CommentColor(str, Enum):
    WHITE = "#FFFFFF"
    RED = "#FF5252"
    PINK = "#FF6FB5"
    ORANGE = "#FF9800"
    YELLOW = "#FFE066"
    GREEN = "#7CFC8A"
    CYAN = "#7CEEFF"
    BLUE = "#77A8FF"
    PURPLE = "#D087FF"


@dataclass(slots=True)
class PersonaConfig:
    enabled: bool
    weight: float


@dataclass(slots=True)
class RuntimeConfig:
    port: int = 12346
    quantization: str = "Q4_K_M"
    context_length: int = 16384
    gpu: str = "max"
    model_repo_url: str = "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF"
    instance_id: str = "screen-commentator-vlm"
    api_key: str = "lm-studio"
    request_timeout_sec: float = 120.0


@dataclass(slots=True)
class CaptureConfig:
    interval_sec: float = 4.0
    jpeg_quality: int = 85
    thumbnail_size: int = 32


@dataclass(slots=True)
class CommentConfig:
    base_count: int = 5
    max_active: int = 30
    recent_history: int = 30
    fixed_duration_sec: float = 4.0


@dataclass(slots=True)
class OverlayConfig:
    font_size: int = 40
    opacity: float = 1.0
    scroll_duration_sec: float = 6.0
    bold: bool = True
    lane_padding: int = 6
    top_margin: int = 30


@dataclass(slots=True)
class AppConfig:
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    comments: CommentConfig = field(default_factory=CommentConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    personas: dict[Persona, PersonaConfig] = field(
        default_factory=lambda: {
            Persona.STANDARD: PersonaConfig(enabled=True, weight=0.6),
            Persona.MEME: PersonaConfig(enabled=True, weight=0.3),
            Persona.CRITIC: PersonaConfig(enabled=False, weight=0.1),
            Persona.INSTRUCTOR: PersonaConfig(enabled=False, weight=0.3),
            Persona.BARRAGE: PersonaConfig(enabled=False, weight=0.2),
        }
    )


@dataclass(slots=True)
class PromptContext:
    recent_comments: list[str]


@dataclass(slots=True)
class CommentBatch:
    comments: list[str]
    mood: Mood = "general"
    excitement: int = 5


@dataclass(slots=True)
class CapturedFrame:
    jpeg_base64: str
    thumbnail_rgb: bytes
    width: int
    height: int


@dataclass(slots=True)
class ModelFiles:
    main_file: Path
    mmproj_file: Path


@dataclass(slots=True)
class PendingComment:
    text: str
    style: CommentStyle
    color: CommentColor
    speed_multiplier: float


@dataclass(slots=True)
class ActiveComment:
    text: str
    style: CommentStyle
    color: CommentColor
    speed_multiplier: float
    lane: int
    created_monotonic: float
    total_duration_sec: float
