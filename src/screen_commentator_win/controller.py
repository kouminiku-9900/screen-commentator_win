from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import QObject, Signal

from .contracts import EngineFactory, RuntimeLifecycle
from .config import ConfigManager
from .models import AppConfig, PendingComment
from .paths import AppPaths
from .runtime import RuntimeErrorWithDetails


logger = logging.getLogger(__name__)


class AppSignals(QObject):
    status_changed = Signal(str)
    log_message = Signal(str)
    busy_changed = Signal(bool)
    running_changed = Signal(bool)
    progress_label_changed = Signal(str)
    progress_value_changed = Signal(int)
    progress_indeterminate_changed = Signal(bool)
    progress_visibility_changed = Signal(bool)
    comment_ready = Signal(object)
    overlay_visibility_changed = Signal(bool)
    clear_overlay = Signal()


class AppController(QObject):
    def __init__(
        self,
        paths: AppPaths,
        config_manager: ConfigManager,
        config: AppConfig,
        runtime: RuntimeLifecycle,
        engine_factory: EngineFactory,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.config_manager = config_manager
        self.config = config
        self.signals = AppSignals()
        self.runtime = runtime
        self.engine_factory = engine_factory
        self.engine = None
        self._busy_lock = threading.Lock()
        self._is_running = False
        self._progress_started_at: float | None = None
        self._progress_label_base = ""
        self._progress_fraction: float | None = None

    def install(self) -> None:
        self._run_background(self._install_impl)

    def start(self) -> None:
        self._run_background(self._start_impl)

    def stop(self) -> None:
        self._run_background(self._stop_impl)

    def shutdown(self) -> None:
        if self._is_running:
            try:
                self._stop_impl()
            except Exception:  # pragma: no cover
                logger.exception("Failed to stop cleanly during shutdown")

    def _run_background(self, worker) -> None:
        if not self._busy_lock.acquire(blocking=False):
            self._emit_status("Another operation is already running.")
            return

        def runner() -> None:
            self.signals.busy_changed.emit(True)
            self._reset_progress()
            try:
                worker()
            except Exception as exc:
                logger.exception("Background operation failed")
                self._emit_status(str(exc))
            finally:
                self.signals.progress_visibility_changed.emit(False)
                self.signals.busy_changed.emit(False)
                self._busy_lock.release()

        threading.Thread(target=runner, daemon=True).start()

    def _install_impl(self) -> None:
        self._emit_status("Installing runtime and model...")
        try:
            if self.runtime.is_installed():
                self._emit_log("llmster is already installed. Skipping installation.")
            else:
                self._set_progress("Downloading llmster installer...", None)
                self.runtime.install_llmster(self._emit_log, self._set_progress)
            self._set_progress("Starting llmster daemon...", None)
            self.runtime.start_daemon(self._emit_log)
            self._set_progress("Starting llmster server...", None)
            self.runtime.start_server(self._emit_log)
            self._set_progress("Downloading model...", None)
            self.runtime.download_model(self._emit_log, self._set_progress)
            self._set_progress("Verifying model files...", None)
            files = self.runtime.verify_model_files()
            self._emit_log(f"Verified model file: {files.main_file}")
            self._emit_log(f"Verified mmproj file: {files.mmproj_file}")
        finally:
            self.runtime.stop_server(self._emit_log, ignore_errors=True)
            self.runtime.stop_daemon(self._emit_log, ignore_errors=True)
        self._emit_status("Install completed.")

    def _start_impl(self) -> None:
        if not self.runtime.is_installed():
            raise RuntimeErrorWithDetails("llmster is not installed yet. Press Install first.")

        self._emit_status("Starting comment system...")
        try:
            self._set_progress("Starting llmster daemon...", None)
            self.runtime.start_daemon(self._emit_log)
            self._set_progress("Starting llmster server...", None)
            self.runtime.start_server(self._emit_log)

            try:
                self.runtime.verify_model_files()
            except RuntimeErrorWithDetails:
                self._set_progress("Downloading model...", None)
                self.runtime.download_model(self._emit_log, self._set_progress)

            self._set_progress("Loading multimodal model...", None)
            files = self.runtime.load_model(self._emit_log, self._set_progress)
            self._emit_log(f"Loaded model from {files.main_file}")

            if self.engine:
                self.engine.stop()
            self.engine = self.engine_factory(self._emit_status, self._emit_comment)
            self.signals.clear_overlay.emit()
            self.signals.overlay_visibility_changed.emit(True)
            self.engine.start()
            self._is_running = True
            self.signals.running_changed.emit(True)
            self._emit_status("Comment engine running.")
        except Exception:
            self.signals.clear_overlay.emit()
            self.signals.overlay_visibility_changed.emit(False)
            if self.engine:
                self.engine.stop()
                self.engine = None
            self.runtime.unload_model(self._emit_log, ignore_errors=True)
            self.runtime.stop_server(self._emit_log, ignore_errors=True)
            self.runtime.stop_daemon(self._emit_log, ignore_errors=True)
            self._is_running = False
            self.signals.running_changed.emit(False)
            raise

    def _stop_impl(self) -> None:
        self._emit_status("Stopping comment system...")
        if self.engine:
            self.engine.stop()
            self.engine = None
        self.signals.clear_overlay.emit()
        self.signals.overlay_visibility_changed.emit(False)
        self.runtime.unload_model(self._emit_log, ignore_errors=True)
        self.runtime.stop_server(self._emit_log, ignore_errors=True)
        self.runtime.stop_daemon(self._emit_log, ignore_errors=True)
        self._is_running = False
        self.signals.running_changed.emit(False)
        self._emit_status("Stopped.")

    def _emit_status(self, message: str) -> None:
        self.signals.status_changed.emit(message)
        self.signals.log_message.emit(message)

    def _emit_log(self, message: str) -> None:
        self.signals.log_message.emit(message)

    def _emit_comment(self, pending: PendingComment) -> None:
        self.signals.comment_ready.emit(pending)

    def _set_progress(self, label: str, fraction: float | None) -> None:
        now = time.monotonic()
        if (
            self._progress_started_at is None
            or label != self._progress_label_base
            or (
                fraction is not None
                and self._progress_fraction is not None
                and fraction < self._progress_fraction
            )
        ):
            self._progress_started_at = now
            self._progress_label_base = label

        self._progress_fraction = fraction
        self.signals.progress_visibility_changed.emit(True)
        self.signals.progress_label_changed.emit(
            self._format_progress_label(label, fraction, now)
        )
        if fraction is None:
            self.signals.progress_indeterminate_changed.emit(True)
            self.signals.progress_value_changed.emit(0)
            return

        clamped = max(0, min(100, int(round(fraction * 100))))
        self.signals.progress_indeterminate_changed.emit(False)
        self.signals.progress_value_changed.emit(clamped)

    def _reset_progress(self) -> None:
        self._progress_started_at = None
        self._progress_label_base = ""
        self._progress_fraction = None
        self.signals.progress_label_changed.emit("")
        self.signals.progress_indeterminate_changed.emit(True)
        self.signals.progress_value_changed.emit(0)

    def _format_progress_label(
        self,
        label: str,
        fraction: float | None,
        now: float,
    ) -> str:
        if self._progress_started_at is None:
            return label

        elapsed_sec = max(0.0, now - self._progress_started_at)
        elapsed_text = self._format_duration(elapsed_sec)
        if fraction is None or fraction <= 0:
            return f"{label} | {elapsed_text} elapsed"

        percent = max(0.0, min(100.0, fraction * 100.0))
        if fraction >= 1.0:
            return f"{label} {percent:.1f}% | {elapsed_text} elapsed | 00:00 left"

        remaining_sec = elapsed_sec * (1.0 - fraction) / fraction
        return (
            f"{label} {percent:.1f}% | {elapsed_text} elapsed | "
            f"{self._format_duration(remaining_sec)} left"
        )

    @staticmethod
    def _format_duration(total_sec: float) -> str:
        seconds = max(0, int(round(total_sec)))
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"
