from __future__ import annotations

from typing import Callable, Protocol

from .models import CapturedFrame, CommentBatch, ModelFiles, PendingComment


ProgressCallback = Callable[[str], None]
ProgressStateCallback = Callable[[str, float | None], None]


class FrameSource(Protocol):
    def grab_primary_display(self) -> CapturedFrame: ...


class InferenceClient(Protocol):
    def generate_comments(self, prompt: str, image_base64: str) -> CommentBatch: ...


class Engine(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


class RuntimeLifecycle(Protocol):
    def is_installed(self) -> bool: ...

    def install_llmster(
        self,
        progress: ProgressCallback,
        progress_state: ProgressStateCallback | None = None,
    ) -> None: ...

    def start_daemon(self, progress: ProgressCallback) -> None: ...

    def stop_daemon(self, progress: ProgressCallback, ignore_errors: bool = False) -> None: ...

    def start_server(self, progress: ProgressCallback) -> None: ...

    def stop_server(self, progress: ProgressCallback, ignore_errors: bool = False) -> None: ...

    def download_model(
        self,
        progress: ProgressCallback,
        progress_state: ProgressStateCallback | None = None,
    ) -> None: ...

    def verify_model_files(self) -> ModelFiles: ...

    def load_model(
        self,
        progress: ProgressCallback,
        progress_state: ProgressStateCallback | None = None,
    ) -> ModelFiles: ...

    def unload_model(self, progress: ProgressCallback, ignore_errors: bool = False) -> None: ...


EngineFactory = Callable[[ProgressCallback, Callable[[PendingComment], None]], Engine]
