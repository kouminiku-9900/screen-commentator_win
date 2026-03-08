from __future__ import annotations

import tomllib

from screen_commentator_win.config import ConfigManager
from screen_commentator_win.models import RuntimeConfig
from screen_commentator_win.paths import AppPaths


def test_load_migrates_legacy_default_model_repo(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    manager = ConfigManager(paths)
    paths.ensure_directories()
    paths.config_file.write_text(
        """
[runtime]
model_repo_url = "https://huggingface.co/HauhauCS/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive"
quantization = "Q4_K_M"
""".strip(),
        encoding="utf-8",
    )

    config = manager.load()

    assert config.runtime.model_repo_url == RuntimeConfig().model_repo_url
    with paths.config_file.open("rb") as handle:
        stored = tomllib.load(handle)
    assert stored["runtime"]["model_repo_url"] == RuntimeConfig().model_repo_url


def test_load_keeps_custom_model_repo(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    manager = ConfigManager(paths)
    paths.ensure_directories()
    custom_url = "https://huggingface.co/example-org/custom-vlm-gguf"
    paths.config_file.write_text(
        f"""
[runtime]
model_repo_url = "{custom_url}"
quantization = "Q4_K_M"
""".strip(),
        encoding="utf-8",
    )

    config = manager.load()

    assert config.runtime.model_repo_url == custom_url
