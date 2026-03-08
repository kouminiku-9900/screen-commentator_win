from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
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
        self._active_instance_id: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.config.runtime.port}"

    def is_installed(self) -> bool:
        return self._current_installation() is not None

    @property
    def lms_executable_path(self) -> str:
        installation = self._require_installation()
        return self._cli_candidates_for_installation(installation)[0]

    def create_inference_client(self) -> OpenAICompatibleInferenceClient:
        return OpenAICompatibleInferenceClient(
            base_url=self.base_url,
            instance_id=self._active_instance_id or self.config.runtime.instance_id,
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

        self._active_instance_id = None
        self._server_process = None
        self._kill_stale_processes(progress)

        try:
            self._attempt_daemon_start(progress, installation)
        except RuntimeErrorWithDetails as exc:
            if "already running" not in str(exc).lower():
                raise
            progress("Retrying after killing remaining processes...")
            self._kill_stale_processes(progress)
            time.sleep(3.0)
            self._attempt_daemon_start(progress, installation)

    def _attempt_daemon_start(
        self, progress: ProgressCallback, installation: ResolvedLmStudioPaths
    ) -> None:
        key_file = installation.lmstudio_home / ".internal" / "lms-key-2"
        cli_executable = self._cli_executable_for_installation(installation)
        if key_file.exists():
            progress(f"Starting llmster daemon via {cli_executable}...")
            completed = self._run_command(
                [cli_executable, "daemon", "up"],
                progress=progress,
                check=False,
                home_root=installation.home_root,
            )
            if completed.returncode == 0:
                self._wait_for_app_local_cli_key(progress, key_file=key_file)
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
            self._active_instance_id = None
            return
        progress("Stopping llmster daemon...")
        if self._server_process is not None:
            self._terminate_process_tree(self._server_process)
            self._server_process = None
        if self._daemon_process:
            self._terminate_process_tree(self._daemon_process)
        self._daemon_process = None
        self._kill_matching_llmster_processes(
            progress,
            announce=None,
            skip_if_tracked_running=False,
        )
        key_file = installation.lmstudio_home / ".internal" / "lms-key-2"
        key_file.unlink(missing_ok=True)
        self._daemon_recent_output = []
        self._active_instance_id = None

    def start_server(self, progress: ProgressCallback) -> None:
        installation = self._current_installation()
        if installation is None:
            raise RuntimeErrorWithDetails("llmster is not installed yet. Press Install first.")
        status = self._server_status_for_installation(installation)
        if status.get("running") and int(status.get("port", 0)) == self.config.runtime.port:
            self._selected_installation = installation
            progress("llmster server is already running.")
            return
        start_errors: list[RuntimeErrorWithDetails] = []
        for cli_executable in self._cli_candidates_for_installation(installation):
            progress(
                f"Starting llmster server on port {self.config.runtime.port} via {cli_executable}..."
            )
            self._server_process = subprocess.Popen(
                [cli_executable, "server", "start", "--port", str(self.config.runtime.port)],
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
            try:
                self._wait_for_server(progress, installation=installation, process=self._server_process)
                return
            except RuntimeErrorWithDetails as exc:
                start_errors.append(exc)
                if self._server_process is not None:
                    self._terminate_process_tree(self._server_process)
                self._server_process = None

        if start_errors:
            raise start_errors[-1]
        raise RuntimeErrorWithDetails("Could not start the llmster server.")

    def stop_server(self, progress: ProgressCallback, ignore_errors: bool = False) -> None:
        installation = self._current_installation()
        if installation is None:
            return

        progress("Stopping llmster server...")
        if self._server_process:
            self._terminate_process_tree(self._server_process)
        self._server_process = None

    def server_status(self) -> dict:
        installation = self._current_installation()
        if installation is None:
            return {"running": False}
        return self._server_status_for_installation(installation)

    def _server_status_for_installation(self, installation: ResolvedLmStudioPaths) -> dict:
        if self._server_http_ready():
            return {"running": True, "port": self.config.runtime.port}

        for cli_executable in self._cli_candidates_for_installation(installation):
            try:
                completed = subprocess.run(
                    [cli_executable, "server", "status", "--json", "--quiet"],
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
                continue

            stdout = completed.stdout.strip()
            if not stdout:
                continue
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                continue
        return {"running": False}

    def _server_http_ready(self) -> bool:
        try:
            response = self.http_client.get(
                f"{self.base_url}/lmstudio-greeting",
                timeout=5.0,
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def _daemon_status_command_succeeds(self, installation: ResolvedLmStudioPaths) -> bool:
        for cli_executable in self._cli_candidates_for_installation(installation):
            try:
                completed = subprocess.run(
                    [cli_executable, "daemon", "status", "--json"],
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
                continue
            if completed.returncode == 0:
                return True
        return False

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
        cli_executable = self._cli_executable_for_installation(installation)
        target = self._download_request_target()
        command = [
            cli_executable,
            "get",
            target,
            "--gguf",
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
        self._active_instance_id = None
        try:
            response = self.http_client.post(
                f"{self.base_url}/api/v1/models/load",
                json={
                    "model": model_key,
                    "context_length": self.config.runtime.context_length,
                },
                timeout=LOAD_TIMEOUT_SEC,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeErrorWithDetails(f"Could not load model: {exc}") from exc

        self._active_instance_id = self._loaded_instance_id_from_response(
            response.json(),
            fallback=model_key,
        )
        progress("Multimodal model is loaded.")
        self._report_progress(progress_state, "Loading multimodal model...", 1.0)
        return files

    def unload_model(self, progress: ProgressCallback, ignore_errors: bool = False) -> None:
        instance_ids: list[str] = []
        for candidate in (self._active_instance_id, self.config.runtime.instance_id):
            instance_id = str(candidate or "").strip()
            if instance_id and instance_id not in instance_ids:
                instance_ids.append(instance_id)

        errors: list[httpx.HTTPError] = []
        for instance_id in instance_ids:
            try:
                self.http_client.post(
                    f"{self.base_url}/api/v1/models/unload",
                    json={"instance_id": instance_id},
                    timeout=20.0,
                ).raise_for_status()
                self._active_instance_id = None
                progress("Unloaded model from memory.")
                return
            except httpx.HTTPError as exc:
                logger.info("Ignoring unload error for %s: %s", instance_id, exc)
                errors.append(exc)

        self._active_instance_id = None
        if not ignore_errors and errors:
            raise RuntimeErrorWithDetails(f"Could not unload model: {errors[-1]}") from errors[-1]

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
        last_return_code: int | None = None
        while time.time() < deadline:
            status = self._server_status_for_installation(resolved_installation)
            if status.get("running") and int(status.get("port", 0)) == self.config.runtime.port:
                progress("llmster server is ready.")
                return

            if resolved_process:
                return_code = resolved_process.poll()
                if return_code not in (None, 0):
                    last_return_code = return_code
            time.sleep(1.0)
        if last_return_code not in (None, 0):
            raise RuntimeErrorWithDetails("llmster server process exited before becoming ready.")
        raise RuntimeErrorWithDetails("Timed out waiting for llmster server to start.")

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
        installation = self._require_installation()
        for cli_executable in self._cli_candidates_for_installation(installation):
            payload = self._run_json_command(
                [cli_executable, "ls", "--json"],
                timeout=20,
            )
            if isinstance(payload, list):
                return [item for item in payload if isinstance(item, dict)]
        return []

    def _list_loaded_models(self) -> list[dict]:
        installation = self._require_installation()
        for cli_executable in self._cli_candidates_for_installation(installation):
            payload = self._run_json_command(
                [cli_executable, "ps", "--json"],
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
    def _loaded_instance_id_from_response(payload: object, *, fallback: str) -> str:
        if isinstance(payload, dict):
            for key in ("instance_id", "instanceId", "identifier", "id"):
                value = str(payload.get(key, "")).strip()
                if value:
                    return value
        return fallback

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

    def _terminate_process_tree(self, process: subprocess.Popen[str]) -> None:
        pid = getattr(process, "pid", None)
        if pid and process.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return
            except (OSError, subprocess.TimeoutExpired):
                pass
        self._terminate_process(process)

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

    def _cli_executable_for_installation(self, installation: ResolvedLmStudioPaths) -> str:
        return self._cli_candidates_for_installation(installation)[0]

    def _cli_candidates_for_installation(self, installation: ResolvedLmStudioPaths) -> list[str]:
        try:
            daemon_executable = self._app_local_daemon_executable()
        except RuntimeErrorWithDetails:
            daemon_executable = ""

        candidates: list[str] = []
        if daemon_executable:
            bundled_lms = os.path.join(os.path.dirname(daemon_executable), ".bundle", "lms.exe")
            if os.path.exists(bundled_lms):
                candidates.append(bundled_lms)

        installation_lms = str(installation.lms_executable)
        if installation_lms not in candidates:
            candidates.append(installation_lms)
        return candidates

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

    def _download_request_target(self) -> str:
        raw = self.config.runtime.model_repo_url.strip().rstrip("/")
        if not raw:
            raise RuntimeErrorWithDetails("runtime.model_repo_url must not be empty.")

        quantization = self.config.runtime.quantization.strip()
        if not quantization:
            raise RuntimeErrorWithDetails("runtime.quantization must not be empty.")
        return f"{raw}@{quantization.lower()}"

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

    def _kill_stale_processes(self, progress: ProgressCallback) -> None:
        self._kill_matching_llmster_processes(
            progress,
            announce="Cleaning up stale llmster processes...",
            skip_if_tracked_running=True,
        )

        installation = self._current_installation()
        if installation is not None:
            key_file = installation.lmstudio_home / ".internal" / "lms-key-2"
            key_file.unlink(missing_ok=True)

    def _kill_matching_llmster_processes(
        self,
        progress: ProgressCallback,
        *,
        announce: str | None,
        skip_if_tracked_running: bool,
    ) -> None:
        if skip_if_tracked_running and self._daemon_process and self._daemon_process.poll() is None:
            return

        if announce:
            progress(announce)

        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "Get-Process -Name llmster -ErrorAction SilentlyContinue "
                        "| Select-Object -Property Id, ProcessName, Path "
                        "| ConvertTo-Json -Compress"
                    ),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return

        stdout = result.stdout.strip()
        if not stdout:
            return

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = []

        if isinstance(payload, dict):
            payload = [payload]

        for entry in payload:
            pid = entry.get("Id")
            executable_path = str(entry.get("Path", "")).strip()
            if not pid or not self._is_stale_llmster_process_path(executable_path):
                continue
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

    def _wait_for_app_local_cli_key(self, progress: ProgressCallback, key_file) -> None:
        installation = self._require_installation()
        deadline = time.time() + DAEMON_START_TIMEOUT_SEC
        try:
            while time.time() < deadline:
                if self._daemon_status_command_succeeds(installation):
                    if self._daemon_process and self._daemon_process.poll() is not None:
                        self._daemon_process = None
                    progress("Isolated llmster daemon is ready.")
                    return

                if self._daemon_process:
                    return_code = self._daemon_process.poll()
                    if return_code not in (None, 0):
                        raise RuntimeErrorWithDetails(
                            self._daemon_start_failure_message(return_code)
                        )

                time.sleep(0.5)
        except Exception:
            self._cleanup_failed_daemon_start(key_file)
            raise
        self._cleanup_failed_daemon_start(key_file)
        raise RuntimeErrorWithDetails("Timed out waiting for the isolated llmster daemon to initialize.")

    def _cleanup_failed_daemon_start(self, key_file) -> None:
        key_file.unlink(missing_ok=True)
        if self._daemon_process is not None:
            self._terminate_process(self._daemon_process)
        self._daemon_process = None
        self._daemon_recent_output = []
        self._active_instance_id = None

    def _is_stale_llmster_process_path(self, executable_path: str) -> bool:
        if not executable_path:
            return False

        normalized_path = os.path.normcase(os.path.normpath(executable_path))
        if os.path.basename(normalized_path) != "llmster.exe":
            return False

        app_local_root = os.path.normcase(os.path.normpath(str(self.paths.llmster_home)))
        if normalized_path.startswith(app_local_root + os.sep):
            return True

        legacy_temp_prefix = os.path.normcase(
            os.path.normpath(os.path.join(tempfile.gettempdir(), "scw-llmster-direct-"))
        )
        return normalized_path.startswith(legacy_temp_prefix) and (
            f"{os.sep}llmster-home{os.sep}" in normalized_path
        )

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
