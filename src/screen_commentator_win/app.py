from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .contracts import EngineFactory, RuntimeLifecycle
from .config import ConfigManager
from .controller import AppController
from .engine import CommentEngine
from .gui import LauncherWindow
from .logging_utils import configure_logging
from .models import AppConfig, CommentColor, CommentStyle, PendingComment
from .overlay import OverlayWindow
from .paths import AppPaths
from .runtime import RuntimeManager


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ApplicationBundle:
    app: QApplication
    controller: AppController
    overlay: OverlayWindow
    window: LauncherWindow
    paths: AppPaths


def build_application(
    qt_argv: list[str] | None = None,
    *,
    paths: AppPaths | None = None,
    config: AppConfig | None = None,
    runtime: RuntimeLifecycle | None = None,
    engine_factory: EngineFactory | None = None,
) -> ApplicationBundle:
    resolved_paths = paths or AppPaths.discover()
    resolved_paths.ensure_directories()
    configure_logging(resolved_paths)

    app = QApplication.instance() or QApplication(qt_argv or [sys.argv[0]])
    config_manager = ConfigManager(resolved_paths)
    resolved_config = config or config_manager.load()
    resolved_runtime = runtime or RuntimeManager(paths=resolved_paths, config=resolved_config)
    resolved_engine_factory = engine_factory or create_default_engine_factory(
        config=resolved_config,
        runtime=resolved_runtime,
    )

    controller = AppController(
        paths=resolved_paths,
        config_manager=config_manager,
        config=resolved_config,
        runtime=resolved_runtime,
        engine_factory=resolved_engine_factory,
    )

    overlay = OverlayWindow(
        overlay_config=resolved_config.overlay,
        fixed_duration_sec=resolved_config.comments.fixed_duration_sec,
        max_active=resolved_config.comments.max_active,
    )
    controller.signals.comment_ready.connect(overlay.add_pending_comment)
    controller.signals.overlay_visibility_changed.connect(overlay.set_overlay_visible)
    controller.signals.clear_overlay.connect(overlay.clear_comments)

    window = LauncherWindow(controller=controller, paths=resolved_paths)
    app.aboutToQuit.connect(controller.shutdown)
    return ApplicationBundle(
        app=app,
        controller=controller,
        overlay=overlay,
        window=window,
        paths=resolved_paths,
    )


def create_default_engine_factory(
    *,
    config: AppConfig,
    runtime: RuntimeLifecycle,
) -> EngineFactory:
    if not isinstance(runtime, RuntimeManager):
        raise TypeError("Default engine factory requires RuntimeManager.")

    def factory(on_status, on_comment):
        inference_client = runtime.create_inference_client()
        return CommentEngine(
            config=config,
            inference_client=inference_client,
            on_status=on_status,
            on_comment=on_comment,
        )

    return factory


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", choices=["smoke", "demo-overlay"])
    parser.add_argument("--self-test-output")
    return parser.parse_args(argv)


def _run_self_test(bundle: ApplicationBundle, mode: str, output_path: str | None) -> int:
    def fail(exc: Exception) -> None:
        logger.exception("Self-test failed", exc_info=exc)
        bundle.app.exit(1)

    def capture_overlay() -> None:
        try:
            target = Path(output_path) if output_path else bundle.paths.state_dir / "demo-overlay.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            bundle.app.processEvents()
            if not bundle.overlay.save_snapshot(target):
                raise RuntimeError(f"Could not save overlay snapshot to {target}")
            bundle.app.exit(0)
        except Exception as exc:  # pragma: no cover
            fail(exc)

    def bootstrap() -> None:
        try:
            bundle.window.show()
            bundle.overlay.set_overlay_visible(True)
            if mode == "demo-overlay":
                bundle.overlay.add_pending_comment(
                    PendingComment(
                        text="8888",
                        style=CommentStyle.TOP,
                        color=CommentColor.WHITE,
                        speed_multiplier=1.0,
                    )
                )
                QTimer.singleShot(300, capture_overlay)
                return
            QTimer.singleShot(200, lambda: bundle.app.exit(0))
        except Exception as exc:  # pragma: no cover
            fail(exc)

    QTimer.singleShot(0, bootstrap)
    return bundle.app.exec()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    bundle = build_application(qt_argv=[sys.argv[0]])
    if args.self_test:
        return _run_self_test(bundle, mode=args.self_test, output_path=args.self_test_output)
    bundle.window.show()
    return bundle.app.exec()
