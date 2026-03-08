from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Callable

import httpx

from .contracts import ProgressStateCallback
from .inference import OpenAICompatibleInferenceClient
from .models import AppConfig
from .models import ModelFiles
from .paths import AppPaths
from .paths import ResolvedLmStudioPaths


logger = logging.getLogger(__name__)


MODEL_PREFIX = "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive"
MODEL_KEY = MODEL_PREFIX.lower()
MMPROJ_FILENAME = f"mmproj-{MODEL_PREFIX}-BF16.gguf"
INSTALL_SCRIPT_URL = "https://lmstudio.ai/install.ps1"
LOAD_TIMEOUT_SEC = 600.0
LOAD_ESTIMATE_SEC_PER_GIB = 2.5
MIN_LOAD_ESTIMATE_SEC = 8.0
MAX_LOAD_ESTIMATE_SEC = 180.0
ProgressCallback = Callable[[str], None]


class RuntimeErrorWithDetails(RuntimeError):
    pass


class RuntimeManager:
    def __init__(
        self,
        paths: AppPaths,
        config: AppConfig,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.paths = paths
        self.config = config
        self.http_client = http_client or httpx.Client()
        self._server_process: subprocess.Popen[str] | None = None
        self._selected_installation: ResolvedLmStudioPaths | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.config.runtime.port}"

    def is_installed(self) -> bool:
        return self._current_installation() is not None

    @property
    def lms_executable_path(self) -> str:
        return str(self._require_installation().lms_executable)

    def create_inference_client(self) -> OpenAICompatibleInferenceClient:
        return OpenAICompatibleInferenceClient(
            base_url=self.base_url,
            instance_id=self.config.runtime.instance_id,
            timeout_sec=self.config.runtime.request_timeout_sec,
        )

    def install_llmster(
        self,
        progress: ProgressCallback,
        progress_state: ProgressStateCallback | None = None,
    ) -> None:
        self.paths.ensure_directories()
        self._report_progress(progress_state, "Downloading llmster installer...", None)
        progress("Downloading llmster installer...")
        response = self.http_client.get(INSTALL_SCRIPT_URL, timeout=30.0)
        response.raise_for_status()
        self.paths.install_script_cache.write_text(response.text, encoding="utf-8")

        self._report_progress(progress_state, "Installing llmster...", None)
        progress("Installing llmster into isolated app directory...")
        self._run_command(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.paths.install_script_cache),
            ],
            progress=progress,
            check=True,
            home_root=self.paths.llmster_home,
        )
        installation = self.paths.resolve_installation()
        if installation is None:
            raise RuntimeErrorWithDetails(
                "llmster installation finished but lms.exe could not be discovered in either the app-local "
                f"home ({self.paths.llmster_home}) or the current user home."
            )
        self._selected_installation = installation
        if installation.home_root == self.paths.llmster_home:
            progress(f"Detected app-local lms.exe at {installation.lms_executable}")
        else:
            progress(f"Detected lms.exe at {installation.lms_executable}")
            progress("llmster ignored the requested app-local home and installed into the current user profile.")
        self._report_progress(progress_state, "llmster installed.", 1.0)

    def start_daemon(self, progress: ProgressCallback) -> None:
        self._sync_app_local_cli_key(progress)
        installations = self._candidate_installations()
        if not installations:
            raise RuntimeErrorWithDetails("llmster is not installed yet. Press Install first.")

        failures: list[str] = []
        for index, installation in enumerate(installations):
            progress(f"Starting llmster daemon via {installation.lms_executable}...")
            completed = self._run_command(
                [str(installation.lms_executable), "daemon", "up"],
                progress=progress,
                check=False,
                home_root=installation.home_root,
            )
            if completed.returncode == 0:
                self._selected_installation = installation
                return

            failures.append(
                f"{installation.lms_executable} -> exit {completed.returncode}"
            )
            if index < len(installations) - 1:
                progress(
                    f"Daemon startup failed via {installation.lms_executable}; trying another installation."
                )

        raise RuntimeErrorWithDetails("Could not start llmster daemon. " + "; ".join(failures))

    def stop_daemon(self, progress: ProgressCallback, ignore_errors: bool = False) -> None:
        installation = self._current_installation()
        if installation is None:
            return
        progress("Stopping llmster daemon...")
        self._run_command(
            [str(installation.lms_executable), "daemon", "down"],
            progress=progress,
            check=not ignore_errors,
        )

    def start_server(self, progress: ProgressCallback) -> None:
        installations = self._candidate_installations()
        if not installations:
            raise RuntimeErrorWithDetails("llmster is not installed yet. Press Install first.")

        failures: list[str] = []
        for index, installation in enumerate(installations):
            status = self._server_status_for_installation(installation)
            if status.get("running") and int(status.get("port", 0)) == self.config.runtime.port:
                self._selected_installation = installation
                progress("llmster server is already running.")
                return
            if status.get("running"):
                self._run_command(
                    [str(installation.lms_executable), "server", "stop"],
                    progress=progress,
                    check=False,
                    home_root=installation.home_root,
                )

            progress(
                f"Starting llmster server on port {self.config.runtime.port} via {installation.lms_executable}..."
            )
            process = subprocess.Popen(
                [str(installation.lms_executable), "server", "start", "--port", str(self.config.runtime.port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._runtime_environment(home_root=installation.home_root),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if process.stdout is not None:
                threading.Thread(
                    target=self._stream_process_output,
                    args=(process.stdout, progress),
                    daemon=True,
                ).start()
            try:
                self._wait_for_server(progress, installation=installation, process=process)
                self._selected_installation = installation
                self._server_process = process
                return
            except RuntimeErrorWithDetails as exc:
                failures.append(f"{installation.lms_executable} -> {exc}")
                self._terminate_process(process)
                if index < len(installations) - 1:
                    progress(
                        f"Server startup failed via {installation.lms_executable}; trying another installation."
                    )

        raise RuntimeErrorWithDetails("Could not start llmster server. " + "; ".join(failures))

    def stop_server(self, progress: ProgressCallback, ignore_errors: bool = False) -> None:
        installation = self._current_installation()
        if installation is None:
            return

        progress("Stopping llmster server...")
        self._run_command(
            [str(installation.lms_executable), "server", "stop"],
            progress=progress,
            check=not ignore_errors,
        )
        if self._server_process:
            try:
                self._server_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._server_process.terminate()
            self._server_process = None

    def server_status(self) -> dict:
        installation = self._current_installation()
        if installation is None:
            return {"running": False}
        return self._server_status_for_installation(installation)

    def _server_status_for_installation(self, installation: ResolvedLmStudioPaths) -> dict:
        try:
            completed = subprocess.run(
                [str(installation.lms_executable), "server", "status", "--json", "--quiet"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._runtime_environment(home_root=installation.home_root),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {"running": False}

        stdout = completed.stdout.strip()
        if not stdout:
            return {"running": False}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"running": False}

    def download_model(
        self,
        progress: ProgressCallback,
        progress_state: ProgressStateCallback | None = None,
    ) -> None:
        self._report_progress(progress_state, "Queueing model download...", None)
        progress("Queueing model download via llmster local API...")
        payload = {
            "model": self.config.runtime.model_repo_url,
            "quantization": self.config.runtime.quantization,
        }
        response = self.http_client.post(
            f"{self.base_url}/api/v1/models/download",
            json=payload,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        status = data.get("status")
        if status in {"already_downloaded", "completed"}:
            progress("Model is already available locally.")
            self._report_progress(progress_state, "Model is already available locally.", 1.0)
            return

        job_id = data.get("job_id")
        if not job_id:
            raise RuntimeErrorWithDetails(f"Unexpected download response: {data}")

        while True:
            time.sleep(2.0)
            poll = self.http_client.get(
                f"{self.base_url}/api/v1/models/download/status/{job_id}",
                timeout=30.0,
            )
            poll.raise_for_status()
            status_payload = poll.json()
            current_status = status_payload.get("status")
            downloaded_bytes = int(status_payload.get("downloaded_bytes", 0))
            total_size = int(status_payload.get("total_size_bytes", 0))
            if total_size > 0:
                percent = downloaded_bytes / total_size * 100
                progress(f"Downloading model... {percent:.1f}%")
                self._report_progress(progress_state, "Downloading model...", percent / 100.0)
            else:
                progress(f"Downloading model... {current_status}")
                self._report_progress(progress_state, f"Downloading model... {current_status}", None)

            if current_status == "completed":
                progress("Model download completed.")
                self._report_progress(progress_state, "Model download completed.", 1.0)
                return
            if current_status == "failed":
                raise RuntimeErrorWithDetails(f"Model download failed: {status_payload}")

    def verify_model_files(self) -> ModelFiles:
        quantized_name = f"{MODEL_PREFIX}-{self.config.runtime.quantization}.gguf"
        installation = self._require_installation()
        search_root = installation.lmstudio_home if installation.lmstudio_home.exists() else self.paths.llmster_home
        main_file = next(search_root.rglob(quantized_name), None)
        mmproj_file = next(search_root.rglob(MMPROJ_FILENAME), None)
        if not main_file or not mmproj_file:
            raise RuntimeErrorWithDetails(
                "Expected model files were not found after download. "
                f"main={quantized_name} mmproj={MMPROJ_FILENAME}"
            )
        return ModelFiles(main_file=main_file, mmproj_file=mmproj_file)

    def load_model(
        self,
        progress: ProgressCallback,
        progress_state: ProgressStateCallback | None = None,
    ) -> ModelFiles:
        files = self.verify_model_files()
        self.unload_model(progress, ignore_errors=True)
        model_key = self._resolve_model_key(files)
        progress("Loading multimodal model into memory...")
        estimated_duration_sec = self._estimated_load_duration_sec(files)
        installation = self._require_installation()
        command = [
            str(installation.lms_executable),
            "load",
            model_key,
            "--context-length",
            str(self.config.runtime.context_length),
            "--gpu",
            self.config.runtime.gpu,
            "--identifier",
            self.config.runtime.instance_id,
            "--yes",
        ]
        progress(f"$ {' '.join(command)}")
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._runtime_environment(home_root=installation.home_root),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if process.stdout is not None:
            threading.Thread(
                target=self._stream_process_output,
                args=(process.stdout, progress),
                daemon=True,
            ).start()
        self._wait_for_model_load(
            process=process,
            progress=progress,
            progress_state=progress_state,
            estimated_duration_sec=estimated_duration_sec,
        )
        self._report_progress(progress_state, "Loading multimodal model...", 1.0)
        return files

    def unload_model(self, progress: ProgressCallback, ignore_errors: bool = False) -> None:
        try:
            self.http_client.post(
                f"{self.base_url}/api/v1/models/unload",
                json={"instance_id": self.config.runtime.instance_id},
                timeout=20.0,
            ).raise_for_status()
            progress("Unloaded model from memory.")
        except httpx.HTTPError as exc:
            logger.info("Ignoring unload error: %s", exc)
            if not ignore_errors:
                raise RuntimeErrorWithDetails(f"Could not unload model: {exc}") from exc

    def _run_command(
        self,
        command: list[str],
        progress: ProgressCallback,
        check: bool,
        home_root=None,
    ) -> subprocess.CompletedProcess[str]:
        progress(f"$ {' '.join(command)}")
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._runtime_environment(home_root=home_root),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=600,
            check=False,
        )
        for line in (completed.stdout or "").splitlines():
            if line.strip():
                progress(line.strip())
        for line in (completed.stderr or "").splitlines():
            if line.strip():
                progress(line.strip())
        if check and completed.returncode != 0:
            raise RuntimeErrorWithDetails(
                f"Command failed ({completed.returncode}): {' '.join(command)}"
            )
        return completed

    def _wait_for_server(
        self,
        progress: ProgressCallback,
        installation: ResolvedLmStudioPaths | None = None,
        process: subprocess.Popen[str] | None = None,
    ) -> None:
        resolved_installation = installation or self._require_installation()
        resolved_process = process or self._server_process
        deadline = time.time() + 60.0
        while time.time() < deadline:
            if resolved_process:
                return_code = resolved_process.poll()
                if return_code not in (None, 0):
                    raise RuntimeErrorWithDetails("llmster server process exited before becoming ready.")

            status = self._server_status_for_installation(resolved_installation)
            if status.get("running") and int(status.get("port", 0)) == self.config.runtime.port:
                progress("llmster server is ready.")
                return
            time.sleep(1.0)
        raise RuntimeErrorWithDetails("Timed out waiting for llmster server to start.")

    def _wait_for_model_load(
        self,
        process: subprocess.Popen[str],
        progress: ProgressCallback,
        progress_state: ProgressStateCallback | None,
        estimated_duration_sec: float,
    ) -> None:
        start = time.monotonic()
        deadline = start + LOAD_TIMEOUT_SEC
        while time.monotonic() < deadline:
            loaded_model = self._loaded_model_entry(self.config.runtime.instance_id)
            if loaded_model is not None:
                status = str(loaded_model.get("status", "")).strip().lower()
                if status not in {"loading", "queued", "unloading"}:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    progress("Multimodal model is loaded.")
                    return

            return_code = process.poll()
            if return_code not in (None, 0):
                raise RuntimeErrorWithDetails(
                    f"llmster load failed with exit code {return_code}."
                )

            elapsed_sec = max(0.0, time.monotonic() - start)
            estimated_fraction = min(0.97, elapsed_sec / estimated_duration_sec)
            self._report_progress(
                progress_state,
                "Loading multimodal model... (estimated)",
                estimated_fraction,
            )
            time.sleep(1.0)

        self._terminate_process(process)
        raise RuntimeErrorWithDetails("Timed out waiting for the multimodal model to load.")

    def _runtime_environment(self, home_root=None) -> dict[str, str]:
        env = os.environ.copy()
        env["LMS_NO_MODIFY_PATH"] = "1"

        resolved_home_root = home_root
        if resolved_home_root is None:
            installation = self._current_installation()
            if installation is not None:
                resolved_home_root = installation.home_root

        if resolved_home_root is not None:
            home_root_str = str(resolved_home_root)
            env["HOME"] = home_root_str
            env["USERPROFILE"] = home_root_str
            drive, tail = os.path.splitdrive(home_root_str)
            if drive:
                env["HOMEDRIVE"] = drive
            if tail:
                env["HOMEPATH"] = tail
        return env

    def _resolve_model_key(self, files: ModelFiles) -> str:
        installation = self._require_installation()
        models_root = installation.lmstudio_home / "models"
        try:
            relative_path = files.main_file.relative_to(models_root).as_posix()
        except ValueError:
            relative_path = None

        for item in self._list_available_models():
            if relative_path is not None and item.get("path") == relative_path:
                model_key = str(item.get("modelKey", "")).strip()
                if model_key:
                    return model_key

        return MODEL_KEY

    def _list_available_models(self) -> list[dict]:
        payload = self._run_json_command(
            [self.lms_executable_path, "ls", "--json"],
            timeout=20,
        )
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _list_loaded_models(self) -> list[dict]:
        payload = self._run_json_command(
            [self.lms_executable_path, "ps", "--json"],
            timeout=20,
        )
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _loaded_model_entry(self, identifier: str) -> dict | None:
        for item in self._list_loaded_models():
            if str(item.get("identifier", "")).strip() == identifier:
                return item
        return None

    def _sync_app_local_cli_key(self, progress: ProgressCallback) -> None:
        app_key = self.paths.llmstudio_home / ".internal" / "lms-key-2"
        if app_key.exists():
            return

        for candidate in self.paths.candidate_installations():
            if candidate.home_root == self.paths.llmster_home:
                continue
            source_key = candidate.lmstudio_home / ".internal" / "lms-key-2"
            if not source_key.exists():
                continue

            app_key.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_key, app_key)
            progress(f"Copied LM Studio CLI key into app-local runtime from {source_key}.")
            return

    def _require_installation(self) -> ResolvedLmStudioPaths:
        installation = self._current_installation()
        if installation is None:
            raise RuntimeErrorWithDetails("llmster is not installed yet. Press Install first.")
        return installation

    def _current_installation(self) -> ResolvedLmStudioPaths | None:
        if self._selected_installation and self._selected_installation.lms_executable.exists():
            return self._selected_installation
        installation = self.paths.resolve_installation()
        if installation is not None:
            self._selected_installation = installation
        return installation

    def _candidate_installations(self) -> list[ResolvedLmStudioPaths]:
        candidates = [candidate for candidate in self.paths.candidate_installations() if candidate.lms_executable.exists()]
        if self._selected_installation and self._selected_installation.lms_executable.exists():
            selected = self._selected_installation
            return [selected] + [candidate for candidate in candidates if candidate.lms_executable != selected.lms_executable]
        return candidates

    def _run_json_command(
        self,
        command: list[str],
        *,
        timeout: float,
    ) -> object | None:
        installation = self._current_installation()
        home_root = installation.home_root if installation is not None else None
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._runtime_environment(home_root=home_root),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

        stdout = completed.stdout.strip()
        if completed.returncode != 0 or not stdout:
            return None

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _report_progress(
        progress_state: ProgressStateCallback | None,
        label: str,
        fraction: float | None,
    ) -> None:
        if progress_state is not None:
            progress_state(label, fraction)

    @staticmethod
    def _stream_process_output(handle, progress: ProgressCallback) -> None:
        for line in handle:
            stripped = line.strip()
            if stripped:
                progress(stripped)

    @staticmethod
    def _estimated_load_duration_sec(files: ModelFiles) -> float:
        size_gib = files.main_file.stat().st_size / (1024**3)
        estimate = size_gib * LOAD_ESTIMATE_SEC_PER_GIB
        return max(MIN_LOAD_ESTIMATE_SEC, min(MAX_LOAD_ESTIMATE_SEC, estimate))

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
