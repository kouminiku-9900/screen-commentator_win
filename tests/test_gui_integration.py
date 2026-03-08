from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt

from screen_commentator_win.config import ConfigManager
from screen_commentator_win.controller import AppController
from screen_commentator_win.gui import LauncherWindow
from screen_commentator_win.models import (
    AppConfig,
    CommentColor,
    CommentStyle,
    ModelFiles,
    PendingComment,
)
from screen_commentator_win.overlay import OverlayWindow
from screen_commentator_win.paths import AppPaths


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._installed = True

    def is_installed(self) -> bool:
        return self._installed

    def install_llmster(self, progress, progress_state=None) -> None:
        self.calls.append("install_llmster")
        if progress_state is not None:
            progress_state("Installing llmster...", None)
        progress("installed")

    def start_daemon(self, progress) -> None:
        self.calls.append("start_daemon")
        progress("daemon up")

    def stop_daemon(self, progress, ignore_errors: bool = False) -> None:
        self.calls.append("stop_daemon")
        progress("daemon down")

    def start_server(self, progress) -> None:
        self.calls.append("start_server")
        progress("server up")

    def stop_server(self, progress, ignore_errors: bool = False) -> None:
        self.calls.append("stop_server")
        progress("server down")

    def download_model(self, progress, progress_state=None) -> None:
        self.calls.append("download_model")
        if progress_state is not None:
            progress_state("Downloading model...", 0.42)
            time.sleep(0.05)
            progress_state("Downloading model...", 1.0)
        progress("downloaded")

    def verify_model_files(self) -> ModelFiles:
        self.calls.append("verify_model_files")
        return ModelFiles(main_file=Path("main.gguf"), mmproj_file=Path("mmproj.gguf"))

    def load_model(self, progress, progress_state=None) -> ModelFiles:
        self.calls.append("load_model")
        if progress_state is not None:
            progress_state("Loading multimodal model... (estimated)", 0.5)
            time.sleep(0.05)
            progress_state("Loading multimodal model...", 1.0)
        progress("loaded")
        return ModelFiles(main_file=Path("main.gguf"), mmproj_file=Path("mmproj.gguf"))

    def unload_model(self, progress, ignore_errors: bool = False) -> None:
        self.calls.append("unload_model")
        progress("unloaded")


class FakeEngine:
    def __init__(self, on_status, on_comment) -> None:
        self.on_status = on_status
        self.on_comment = on_comment
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True
        self.on_comment(
            PendingComment(
                text="self-test",
                style=CommentStyle.SCROLL,
                color=CommentColor.WHITE,
                speed_multiplier=1.0,
            )
        )

    def stop(self) -> None:
        self.stopped = True


def _build_controller_bundle(tmp_path):
    paths = AppPaths(root=tmp_path, config_file=tmp_path / "config.toml", logs_dir=tmp_path / "logs", state_dir=tmp_path / "state", llmster_home=tmp_path / "llmster-home", llmstudio_home=tmp_path / "llmster-home" / ".lmstudio", llmstudio_bin_dir=tmp_path / "llmster-home" / ".lmstudio" / "bin", lms_executable=tmp_path / "llmster-home" / ".lmstudio" / "bin" / "lms.exe", install_script_cache=tmp_path / "state" / "install-llmster.ps1", app_log_file=tmp_path / "logs" / "screen-commentator.log")
    paths.ensure_directories()
    config_manager = ConfigManager(paths)
    config = AppConfig()
    runtime = FakeRuntime()
    controller = AppController(
        paths=paths,
        config_manager=config_manager,
        config=config,
        runtime=runtime,
        engine_factory=lambda on_status, on_comment: FakeEngine(on_status, on_comment),
    )
    overlay = OverlayWindow(
        overlay_config=config.overlay,
        fixed_duration_sec=config.comments.fixed_duration_sec,
        max_active=config.comments.max_active,
    )
    controller.signals.comment_ready.connect(overlay.add_pending_comment)
    controller.signals.overlay_visibility_changed.connect(overlay.set_overlay_visible)
    controller.signals.clear_overlay.connect(overlay.clear_comments)
    window = LauncherWindow(controller=controller, paths=paths)
    return controller, overlay, window, runtime


def test_install_button_runs_runtime_sequence(qtbot, tmp_path) -> None:
    controller, overlay, window, runtime = _build_controller_bundle(tmp_path)
    qtbot.addWidget(window)
    qtbot.addWidget(overlay)
    window.show()

    qtbot.mouseClick(window.install_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.status_label.text() == "Install completed.")

    assert runtime.calls == [
        "install_llmster",
        "start_daemon",
        "start_server",
        "download_model",
        "verify_model_files",
        "stop_server",
        "stop_daemon",
    ]

    assert not window.progress_bar.isVisible()


def test_install_button_updates_progress_bar(qtbot, tmp_path) -> None:
    controller, overlay, window, _ = _build_controller_bundle(tmp_path)
    qtbot.addWidget(window)
    qtbot.addWidget(overlay)
    window.show()

    qtbot.mouseClick(window.install_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: window.progress_bar.isVisible()
        and window.progress_bar.maximum() == 100
        and window.progress_bar.value() == 42
    )
    qtbot.waitUntil(lambda: window.status_label.text() == "Install completed.")

    assert not window.progress_bar.isVisible()


def test_progress_label_includes_percent_and_eta(qtbot, tmp_path, monkeypatch) -> None:
    controller, overlay, window, _ = _build_controller_bundle(tmp_path)
    qtbot.addWidget(window)
    qtbot.addWidget(overlay)
    window.show()

    timestamps = iter([10.0, 12.0])
    monkeypatch.setattr("screen_commentator_win.controller.time.monotonic", lambda: next(timestamps))

    controller._set_progress("Downloading model...", 0.25)
    controller._set_progress("Downloading model...", 0.5)

    assert window.progress_label.text() == "Downloading model... 50.0% | 00:02 elapsed | 00:02 left"


def test_start_and_stop_flow_updates_overlay_and_buttons(qtbot, tmp_path) -> None:
    controller, overlay, window, runtime = _build_controller_bundle(tmp_path)
    qtbot.addWidget(window)
    qtbot.addWidget(overlay)
    window.show()

    qtbot.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.stop_button.isEnabled())
    qtbot.waitUntil(lambda: len(overlay.active_comments) == 1)

    assert runtime.calls[:3] == ["start_daemon", "start_server", "load_model"]
    assert overlay.isVisible()

    qtbot.mouseClick(window.stop_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.start_button.isEnabled())

    assert runtime.calls[-3:] == ["unload_model", "stop_server", "stop_daemon"]
    assert overlay.active_comments == []
    assert not overlay.isVisible()
