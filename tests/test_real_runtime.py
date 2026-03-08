from __future__ import annotations

import base64
import io
import os

import pytest
from PIL import Image

from screen_commentator_win.app import build_application
from screen_commentator_win.engine import CommentEngine
from screen_commentator_win.models import AppConfig, CapturedFrame
from screen_commentator_win.paths import AppPaths
from screen_commentator_win.runtime import RuntimeManager


class StaticFrameSource:
    def __init__(self, frame: CapturedFrame) -> None:
        self.frame = frame

    def grab_primary_display(self) -> CapturedFrame:
        return self.frame


def _fixture_frame() -> CapturedFrame:
    image = Image.new("RGB", (64, 64), "#2c6e49")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    thumbnail = image.resize((32, 32)).convert("RGB")
    return CapturedFrame(
        jpeg_base64=base64.b64encode(buffer.getvalue()).decode("ascii"),
        thumbnail_rgb=thumbnail.tobytes(),
        width=64,
        height=64,
    )


@pytest.mark.real_runtime
def test_real_runtime_generates_overlay_comment(qtbot, tmp_path, monkeypatch) -> None:
    if os.environ.get("SCW_RUN_REAL_E2E") != "1":
        pytest.skip("Set SCW_RUN_REAL_E2E=1 to run the llmster/model end-to-end test.")

    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path / "app-root"))
    paths = AppPaths.discover()
    config = AppConfig()
    config.capture.interval_sec = 1.0
    runtime = RuntimeManager(paths=paths, config=config)
    frame = _fixture_frame()

    def engine_factory(on_status, on_comment):
        return CommentEngine(
            config=config,
            inference_client=runtime.create_inference_client(),
            frame_source=StaticFrameSource(frame),
            on_status=on_status,
            on_comment=on_comment,
        )

    bundle = build_application(
        paths=paths,
        config=config,
        runtime=runtime,
        engine_factory=engine_factory,
    )
    qtbot.addWidget(bundle.window)
    qtbot.addWidget(bundle.overlay)
    bundle.window.show()

    try:
        bundle.controller._install_impl()
        bundle.controller._start_impl()
        qtbot.waitUntil(lambda: len(bundle.overlay.active_comments) > 0, timeout=300000)

        output_path = tmp_path / "real-runtime-overlay.png"
        assert bundle.overlay.save_snapshot(output_path)
        assert output_path.exists()

        bundle.controller._stop_impl()
        assert not bundle.overlay.active_comments
        assert runtime.server_status().get("running") is False
    finally:
        bundle.controller.shutdown()
