from __future__ import annotations

import tomllib
from dataclasses import asdict
from pathlib import Path

import tomli_w

from .models import (
    AppConfig,
    CaptureConfig,
    CommentConfig,
    OverlayConfig,
    Persona,
    PersonaConfig,
    RuntimeConfig,
)
from .paths import AppPaths


class ConfigManager:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    def load(self) -> AppConfig:
        self.paths.ensure_directories()
        if not self.paths.config_file.exists():
            config = AppConfig()
            self.save(config)
            return config

        with self.paths.config_file.open("rb") as handle:
            raw = tomllib.load(handle)
        return self._from_dict(raw)

    def save(self, config: AppConfig) -> None:
        self.paths.ensure_directories()
        raw = {
            "runtime": asdict(config.runtime),
            "capture": asdict(config.capture),
            "comments": asdict(config.comments),
            "overlay": asdict(config.overlay),
            "personas": {
                persona.value: asdict(persona_config)
                for persona, persona_config in config.personas.items()
            },
        }
        with self.paths.config_file.open("wb") as handle:
            tomli_w.dump(raw, handle)

    def _from_dict(self, raw: dict) -> AppConfig:
        runtime = RuntimeConfig(**raw.get("runtime", {}))
        capture = CaptureConfig(**raw.get("capture", {}))
        comments = CommentConfig(**raw.get("comments", {}))
        overlay = OverlayConfig(**raw.get("overlay", {}))

        persona_section = raw.get("personas", {})
        personas: dict[Persona, PersonaConfig] = {}
        for persona in Persona:
            raw_persona = persona_section.get(persona.value, {})
            if raw_persona:
                personas[persona] = PersonaConfig(**raw_persona)
            else:
                personas[persona] = AppConfig().personas[persona]

        return AppConfig(
            runtime=runtime,
            capture=capture,
            comments=comments,
            overlay=overlay,
            personas=personas,
        )


def config_path_for_user(paths: AppPaths) -> Path:
    paths.ensure_directories()
    return paths.config_file

