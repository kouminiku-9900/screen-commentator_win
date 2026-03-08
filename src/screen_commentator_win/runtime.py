from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from typing import Callable
from urllib.parse import urlparse

import httpx

from .contracts import ProgressStateCallback
from .inference import OpenAICompatibleInferenceClient
from .models import AppConfig
from .models import ModelFiles
from .paths import AppPaths
from .paths import ResolvedLmStudioPaths


logger = logging.getLogger(__name__)

INSTALL_SCRIPT_URL = "https://lmstudio.ai/install.ps1"
DAEMON_START_TIMEOUT_SEC = 60.0
DAEMON_STABILIZATION_SEC = 3.0
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
        self._daemon_process: subprocess.Popen[str] | None = None
        self._server_process: subprocess.Popen[str] | None = None
        self._daemon_recent_output: list[str] = []
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
        if installation is None or installation.home_root != self.paths.llmster_home:
            raise RuntimeErrorWithDetails(
                "llmster installation did not finish inside the app-local runtime directory. "
                f"Expected app-local install under {self.paths.llmster_home}."
            )
        self._selected_installation = installation
        progress(f"Detected app-local lms.exe at {installation.lms_executable}")
        self._report_progress(progress_state, "llmster installed.", 1.0)

    def start_daemon(self, progress: ProgressCallback) -> None:
        installation = self._current_installation()
        if installation is None:
            raise RuntimeErrorWithDetails("llmster is not installed yet. Press Install first.")
        self._selected_installation = installation

        if self._daemon_process and self._daemon_process.poll() is None:
            progress("Isolated llmster daemon is already running.")
            return

        self._kill_stale_daemons(progress)

        last_error: RuntimeErrorWithDetails | None = None
        for attempt in range(3):
            try:
                self._attempt_daemon_start(progress, installation)
                return
            except RuntimeErrorWithDetails as exc:
                last_error = exc
                if attempt < 2 and "already running" in str(exc).lower():
                    progress("Detected another llmster instance; retrying after cleanup...")
                    self._kill_stale_daemons(progress)
                    time.sleep(3.0)
                    continue
                raise

        assert last_error is not None
        raise last_error

    def _attempt_daemon_start(
        self, progress: ProgressCallback, installation: ResolvedLmStudioPaths
    ) -> None:
        key_file = installation.lmstudio_home / ".internal" / "lms-key-2"
        if key_file.exists():
            progress(f"Starting llmster daemon via {installation.lms_executable}...")
            completed = self._run_command(
                [str(installation.lms_executable), "daemon", "up"],
                progress=progress,
                check=False,
                home_root=installation.home_root,
            )
            if completed.returncode == 0:
                return
            progress("App-local CLI daemon command failed; restarting the isolated llmster daemon directly.")
            key_file.unlink(missing_ok=True)

        daemon_executable = self._app_local_daemon_executable()
        progress(f"Starting isolated llmster daemon via {daemon_executable}...")
        self._daemon_recent_output = []
        self._daemon_process = subprocess.Popen(
            [str(daemon_executable)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._runtime_environment(home_root=installation.home_root),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert self._daemon_process.stdout is not None
        threading.Thread(
            target=self._stream_process_output,
            args=(self._daemon_process.stdout, progress, self._daemon_recent_output),
            daemon=True,
        ).start()
        self._wait_for_app_local_cli_key(progress, key_file=key_file)

    def stop_daemon(self, progress: ProgressCallback, ignore_errors: bool = False) -> None:
        installation = self._current_installation()
        if installation is None:
            return
        progress("Stopping llmster daemon...")
        key_file = installation.lmstudio_home / ".internal" / "lms-key-2"
        if key_file.exists():
            self._run_command(
                [str(installation.lms_executable), "daemon", "down"],
                progress=progress,
                check=not ignore_errors,
                home_root=installation.home_root,
            )
        if self._daemon_process:
            self._terminate_process(self._daemon_process)
            self._daemon_process = None

    def start_server(self, progress: ProgressCallback) -> None:
        installation = self._current_installation()
        if installation is None:
            raise RuntimeErrorWithDetails("llmster is not installed yet. Press Install first.")
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
        self._server_process = subprocess.Popen(
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
        assert self._server_process.stdout is not None
        threading.Thread(
            target=self._stream_process_output,
            args=(self._server_process.stdout, progress),
            daemon=True,
        ).start()
        self._wait_for_server(progress, installation=installation, process=self._server_process)

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
        try:
            self._download_model_rest(progress, progress_state)
        except (httpx.HTTPStatusError, httpx.ConnectError) as exc:
            logger.info("REST model download not available (%s), falling back to CLI.", exc)
            progress("REST download API not available; using CLI fallback...")
            self._download_model_cli(progress, progress_state)

    def _download_model_rest(
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

    def _download_model_cli(
        self,
        progress: ProgressCallback,
        progress_state: ProgressStateCallback | None = None,
    ) -> None:
        self._report_progress(progress_state, "Queueing model download...", None)
        progress("Queueing model download via llmster CLI...")
        installation = self._require_installation()
        owner, repo = self._configured_repo_parts()
        quantization = self.config.runtime.quantization.strip()
        target = f"{owner}/{repo}@{quantization.lower()}"
        command = [
            str(installation.lms_executable),
            "get",
            target,
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
        try:
            self._stream_download_output(process, progress, progress_state)
        finally:
            if process.poll() is None:
                self._terminate_process(process)
        progress("Model download completed.")
        self._report_progress(progress_state, "Model download completed.", 1.0)

    def _stream_download_output(
        self,
        process: subprocess.Popen[str],
        progress: ProgressCallback,
        progress_state: ProgressStateCallback | None,
    ) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            stripped = line.strip()
            if stripped:
                progress(stripped)
                fraction = self._download_fraction_from_line(stripped)
                if fraction is not None:
                    self._report_progress(progress_state, "Downloading model...", fraction)

        return_code = process.wait()
        if return_code != 0:
            raise RuntimeErrorWithDetails(
                f"CLI model download failed with exit code {return_code}."
            )

    @staticmethod
    def _download_fraction_from_line(line: str) -> float | None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
        if match:
            return min(1.0, float(match.group(1)) / 100.0)
        return None

    def verify_model_files(self) -> ModelFiles:
        installation = self._require_installation()
        models_root = installation.lmstudio_home / "models"
        search_root = models_root if models_root.exists() else self.paths.llmster_home
        repo_root = self._downloaded_model_dir(search_root)
        main_file = self._find_main_model_file(repo_root)
        mmproj_file = self._find_mmproj_file(repo_root)
        if not main_file or not mmproj_file:
            raise RuntimeErrorWithDetails(
                "Expected model files were not found after download. "
                f"repo={self.config.runtime.model_repo_url} quantization={self.config.runtime.quantization}"
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

        return files.main_file.stem.lower()

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

    def _require_installation(self) -> ResolvedLmStudioPaths:
        installation = self._current_installation()
        if installation is None:
            raise RuntimeErrorWithDetails("llmster is not installed yet. Press Install first.")
        return installation

    def _current_installation(self) -> ResolvedLmStudioPaths | None:
        if self._selected_installation and self._selected_installation.lms_executable.exists():
            return self._selected_installation
        installation = self.paths.app_local_installation()
        if not installation.lms_executable.exists():
            return None
        if installation is not None:
            self._selected_installation = installation
        return installation

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
    def _stream_process_output(
        handle,
        progress: ProgressCallback,
        recent_lines: list[str] | None = None,
    ) -> None:
        for line in handle:
            stripped = line.strip()
            if stripped:
                if recent_lines is not None:
                    recent_lines.append(stripped)
                    if len(recent_lines) > 40:
                        del recent_lines[:-40]
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

    def _app_local_daemon_executable(self) -> str:
        if self.paths.llmster_install_location_file.exists():
            try:
                payload = json.loads(
                    self.paths.llmster_install_location_file.read_text(encoding="utf-8")
                )
            except json.JSONDecodeError as exc:
                raise RuntimeErrorWithDetails(
                    f"Could not parse app-local llmster install metadata: {exc}"
                ) from exc
            path_value = str(payload.get("path", "")).strip()
            if path_value:
                executable = os.path.normpath(path_value)
                if os.path.exists(executable):
                    return executable

        candidates = sorted(
            self.paths.llmstudio_home.glob("llmster/*/llmster.exe"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return str(candidates[0])
        raise RuntimeErrorWithDetails(
            "llmster is not installed correctly in the app-local runtime directory."
        )

    def _downloaded_model_dir(self, search_root):
        owner, repo = self._configured_repo_parts()
        direct_path = search_root / owner / repo
        if direct_path.exists():
            return direct_path

        nested_candidates = list(search_root.rglob(repo))
        directory_candidates = [path for path in nested_candidates if path.is_dir() and path.name == repo]
        if directory_candidates:
            directory_candidates.sort(key=lambda path: len(path.parts))
            return directory_candidates[0]

        raise RuntimeErrorWithDetails(
            "The configured model repository was not found under the app-local runtime. "
            f"Expected to find {owner}/{repo} inside {search_root}."
        )

    def _configured_repo_parts(self) -> tuple[str, str]:
        raw = self.config.runtime.model_repo_url.strip().rstrip("/")
        if not raw:
            raise RuntimeErrorWithDetails("runtime.model_repo_url must not be empty.")

        if "://" in raw:
            parsed = urlparse(raw)
            path = parsed.path.strip("/")
        else:
            path = raw.strip("/")

        parts = [segment for segment in path.split("/") if segment]
        if len(parts) < 2:
            raise RuntimeErrorWithDetails(
                "runtime.model_repo_url must point to a Hugging Face repository like owner/repo."
            )
        return parts[0], parts[1]

    def _find_main_model_file(self, repo_root):
        quantization_suffix = f"-{self.config.runtime.quantization}.gguf".lower()
        candidates = [
            path
            for path in repo_root.rglob("*.gguf")
            if not path.name.lower().startswith("mmproj-")
        ]
        exact_matches = [path for path in candidates if path.name.lower().endswith(quantization_suffix)]
        if exact_matches:
            exact_matches.sort(key=lambda path: (len(path.parts), len(path.name)))
            return exact_matches[0]
        return None

    @staticmethod
    def _find_mmproj_file(repo_root):
        preferred_names = [
            "mmproj-bf16.gguf",
            "mmproj-f16.gguf",
            "mmproj-f32.gguf",
        ]
        candidates = [
            path
            for path in repo_root.rglob("*.gguf")
            if path.name.lower().startswith("mmproj")
        ]
        if not candidates:
            return None

        for preferred_name in preferred_names:
            for candidate in candidates:
                if candidate.name.lower() == preferred_name:
                    return candidate

        candidates.sort(key=lambda path: (len(path.parts), len(path.name)))
        return candidates[0]

    def _kill_stale_daemons(self, progress: ProgressCallback) -> None:
        if self._daemon_process and self._daemon_process.poll() is None:
            return

        installation = self._current_installation()

        # 1. Graceful shutdown via CLI — this cleanly stops child processes too.
        if installation is not None:
            key_file = installation.lmstudio_home / ".internal" / "lms-key-2"
            if key_file.exists():
                progress("Stopping existing llmster daemon via CLI...")
                try:
                    subprocess.run(
                        [str(installation.lms_executable), "daemon", "down"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=15,
                        check=False,
                        env=self._runtime_environment(home_root=installation.home_root),
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
                time.sleep(1.0)

        # 2. Force-kill remaining app-local llmster processes (with /T for tree kill).
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "Get-Process -Name llmster -ErrorAction SilentlyContinue "
                        "| Select-Object -Property Id, Path "
                        "| ConvertTo-Json -Compress"
                    ),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None

        if result is not None:
            stdout = result.stdout.strip()
            if stdout:
                try:
                    payload = json.loads(stdout)
                except json.JSONDecodeError:
                    payload = []

                if isinstance(payload, dict):
                    payload = [payload]

                app_local_prefix = str(self.paths.llmster_home).lower()
                for entry in payload:
                    exe_path = str(entry.get("Path", "")).lower()
                    pid = entry.get("Id")
                    if not pid:
                        continue
                    if exe_path.startswith(app_local_prefix):
                        progress(f"Terminating stale llmster process tree (PID {pid})...")
                        try:
                            subprocess.run(
                                ["taskkill", "/F", "/T", "/PID", str(pid)],
                                capture_output=True,
                                timeout=10,
                                check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            )
                        except (OSError, subprocess.TimeoutExpired):
                            pass

        # 3. Clean up key file.
        if installation is not None:
            key_file = installation.lmstudio_home / ".internal" / "lms-key-2"
            key_file.unlink(missing_ok=True)

    def _wait_for_app_local_cli_key(self, progress: ProgressCallback, key_file) -> None:
        deadline = time.time() + DAEMON_START_TIMEOUT_SEC
        while time.time() < deadline:
            if key_file.exists():
                self._verify_daemon_stable(progress, key_file)
                progress("Isolated llmster daemon is ready.")
                return

            if self._daemon_process:
                return_code = self._daemon_process.poll()
                if return_code is not None:
                    message = self._daemon_start_failure_message(return_code)
                    self._daemon_process = None
                    raise RuntimeErrorWithDetails(message)

            time.sleep(0.5)
        raise RuntimeErrorWithDetails("Timed out waiting for the isolated llmster daemon to initialize.")

    def _verify_daemon_stable(self, progress: ProgressCallback, key_file) -> None:
        if not self._daemon_process:
            return

        deadline = time.time() + DAEMON_STABILIZATION_SEC
        while time.time() < deadline:
            return_code = self._daemon_process.poll()
            if return_code is not None:
                key_file.unlink(missing_ok=True)
                message = self._daemon_start_failure_message(return_code)
                self._daemon_process = None
                raise RuntimeErrorWithDetails(message)
            time.sleep(0.5)

    def _daemon_start_failure_message(self, return_code: int) -> str:
        recent_output = "\n".join(self._daemon_recent_output[-8:])
        if "LM Studio is already running with built-in llmster" in recent_output:
            return (
                "Could not start the isolated app-local llmster daemon because LM Studio is currently running. "
                "Close LM Studio completely and try again."
            )
        if "already running" in recent_output.lower():
            return (
                f"Could not start the isolated app-local llmster daemon (exit {return_code}). "
                f"Another llmster instance is already running. {recent_output}"
            )
        if recent_output:
            return f"Could not start the isolated app-local llmster daemon (exit {return_code}). {recent_output}"
        return f"Could not start the isolated app-local llmster daemon (exit {return_code})."
