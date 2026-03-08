from __future__ import annotations

import json
import subprocess

import httpx
import pytest

from screen_commentator_win.models import AppConfig
from screen_commentator_win.models import ModelFiles
from screen_commentator_win.paths import AppPaths
from screen_commentator_win.runtime import RuntimeErrorWithDetails
from screen_commentator_win.runtime import RuntimeManager


def test_download_model_uses_cli_and_reports_progress(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    progress: list[str] = []
    progress_state: list[tuple[str, float | None]] = []
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")
    runtime = RuntimeManager(paths=paths, config=config)
    commands: list[list[str]] = []

    class FakeProcess:
        def __init__(self, command, **kwargs) -> None:
            commands.append(command)
            self.stdout = iter(
                [
                    "Resolving model...\n",
                    "Downloading model... 25.0%\n",
                    "Downloading model... 100.0%\n",
                ]
            )

        def poll(self):
            return 0

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return 0

    monkeypatch.setattr("screen_commentator_win.runtime.subprocess.Popen", FakeProcess)
    monkeypatch.setattr(
        runtime,
        "verify_model_files",
        lambda: ModelFiles(main_file=tmp_path / "main.gguf", mmproj_file=tmp_path / "mmproj.gguf"),
    )

    runtime.download_model(progress.append, lambda label, fraction: progress_state.append((label, fraction)))

    assert commands == [
        [
            str(paths.lms_executable),
            "get",
            "unsloth/Qwen3.5-4B-GGUF@q4_k_m",
            "--gguf",
            "--yes",
        ]
    ]
    assert progress[-1] == "Model download completed."
    assert progress_state == [
        ("Queueing model download...", None),
        ("Downloading model...", 0.25),
        ("Downloading model...", 1.0),
        ("Model download completed.", 1.0),
    ]


def test_install_llmster_requires_app_local_install(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path / "app-root"))
    paths = AppPaths.discover()
    config = AppConfig()
    progress: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="installer")

    runtime = RuntimeManager(
        paths=paths,
        config=config,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    def fake_run_command(command, progress, check, home_root=None):
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runtime, "_run_command", fake_run_command)

    with pytest.raises(RuntimeErrorWithDetails, match="app-local runtime directory"):
        runtime.install_llmster(progress.append)


def test_runtime_environment_uses_app_local_home_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path / "app-root"))
    paths = AppPaths.discover()
    config = AppConfig()
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")

    runtime = RuntimeManager(paths=paths, config=config)
    env = runtime._runtime_environment()

    assert env["HOME"] == str(paths.llmster_home)
    assert env["USERPROFILE"] == str(paths.llmster_home)
    assert env["LMS_NO_MODIFY_PATH"] == "1"


def test_start_daemon_launches_app_local_llmster_when_key_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path / "app-root"))
    paths = AppPaths.discover()
    config = AppConfig()
    progress: list[str] = []
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"local")
    daemon_executable = paths.llmstudio_home / "llmster" / "0.0.6-1" / "llmster.exe"
    daemon_executable.parent.mkdir(parents=True, exist_ok=True)
    daemon_executable.write_bytes(b"daemon")
    paths.llmster_install_location_file.parent.mkdir(parents=True, exist_ok=True)
    paths.llmster_install_location_file.write_text(
        json.dumps({"path": str(daemon_executable)}),
        encoding="utf-8",
    )

    runtime = RuntimeManager(paths=paths, config=config)
    captured: dict[str, object] = {}

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = []

        def poll(self):
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return 0

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return FakeProcess()

    def fake_wait_for_key(progress_callback, key_file):
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text("ready", encoding="utf-8")
        progress_callback("Isolated llmster daemon is ready.")

    monkeypatch.setattr("screen_commentator_win.runtime.subprocess.Popen", fake_popen)
    monkeypatch.setattr(runtime, "_wait_for_app_local_cli_key", fake_wait_for_key)

    runtime.start_daemon(progress.append)

    assert captured["command"] == [str(daemon_executable)]
    assert captured["env"]["HOME"] == str(paths.llmster_home)
    assert captured["env"]["USERPROFILE"] == str(paths.llmster_home)
    assert progress[-1] == "Isolated llmster daemon is ready."


def test_start_daemon_reports_running_lm_studio_conflict(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path / "app-root"))
    paths = AppPaths.discover()
    config = AppConfig()
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"local")
    daemon_executable = paths.llmstudio_home / "llmster" / "0.0.6-1" / "llmster.exe"
    daemon_executable.parent.mkdir(parents=True, exist_ok=True)
    daemon_executable.write_bytes(b"daemon")

    runtime = RuntimeManager(paths=paths, config=config)
    paths.llmster_install_location_file.parent.mkdir(parents=True, exist_ok=True)
    paths.llmster_install_location_file.write_text(
        json.dumps({"path": str(daemon_executable)}),
        encoding="utf-8",
    )

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = []

        def poll(self):
            return 1

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return 1

    def fake_popen(command, **kwargs):
        runtime._daemon_recent_output = [
            "Cannot start: LM Studio is already running with built-in llmster. "
            "Close LM Studio first, or use LM Studio instead of standalone llmster."
        ]
        return FakeProcess()

    monkeypatch.setattr("screen_commentator_win.runtime.subprocess.Popen", fake_popen)
    progress: list[str] = []

    with pytest.raises(RuntimeErrorWithDetails, match="Close LM Studio completely and try again"):
        runtime.start_daemon(progress.append)


def test_wait_for_server_accepts_successful_launcher_exit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    runtime = RuntimeManager(paths=paths, config=config)
    progress: list[str] = []
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")
    statuses = iter(
        [
            {"running": False},
            {"running": True, "port": config.runtime.port},
        ]
    )

    class FakeProcess:
        def poll(self):
            return 0

    runtime._server_process = FakeProcess()
    monkeypatch.setattr(runtime, "_server_status_for_installation", lambda installation: next(statuses))
    monkeypatch.setattr("screen_commentator_win.runtime.time.sleep", lambda *_: None)

    runtime._wait_for_server(progress.append)

    assert progress[-1] == "llmster server is ready."


def test_start_server_uses_app_local_installation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path / "app-root"))
    paths = AppPaths.discover()
    config = AppConfig()
    progress: list[str] = []
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"local")

    runtime = RuntimeManager(paths=paths, config=config)
    captured: dict[str, object] = {}

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = []

        def poll(self):
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return 0

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return FakeProcess()

    def fake_wait_for_server(progress_callback, installation=None, process=None):
        captured["installation"] = installation
        captured["process"] = process
        progress_callback("llmster server is ready.")

    monkeypatch.setattr("screen_commentator_win.runtime.subprocess.Popen", fake_popen)
    monkeypatch.setattr(runtime, "_server_status_for_installation", lambda installation: {"running": False})
    monkeypatch.setattr(runtime, "_wait_for_server", fake_wait_for_server)

    runtime.start_server(progress.append)

    assert captured["command"] == [
        str(paths.lms_executable),
        "server",
        "start",
        "--port",
        str(config.runtime.port),
    ]
    assert captured["env"]["HOME"] == str(paths.llmster_home)
    assert captured["env"]["USERPROFILE"] == str(paths.llmster_home)
    assert captured["installation"].lms_executable == paths.lms_executable
    assert progress[-1] == "llmster server is ready."


def test_verify_model_files_requires_main_and_mmproj(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")
    model_dir = paths.llmster_home / ".lmstudio" / "models" / "unsloth" / "Qwen3.5-4B-GGUF"
    model_dir.mkdir(parents=True, exist_ok=True)
    main_file = model_dir / "Qwen3.5-4B-Q4_K_M.gguf"
    mmproj_file = model_dir / "mmproj-BF16.gguf"
    main_file.write_bytes(b"main")
    mmproj_file.write_bytes(b"mmproj")

    runtime = RuntimeManager(paths=paths, config=config)
    files = runtime.verify_model_files()

    assert files.main_file == main_file
    assert files.mmproj_file == mmproj_file


def test_verify_model_files_respects_configured_repo_url(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    config.runtime.model_repo_url = "https://huggingface.co/example-org/custom-vlm-gguf"
    config.runtime.quantization = "Q8_0"
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")
    model_dir = paths.llmster_home / ".lmstudio" / "models" / "example-org" / "custom-vlm-gguf"
    model_dir.mkdir(parents=True, exist_ok=True)
    main_file = model_dir / "custom-vlm-Q8_0.gguf"
    mmproj_file = model_dir / "mmproj-F16.gguf"
    main_file.write_bytes(b"main")
    mmproj_file.write_bytes(b"mmproj")

    runtime = RuntimeManager(paths=paths, config=config)
    files = runtime.verify_model_files()

    assert files == ModelFiles(main_file=main_file, mmproj_file=mmproj_file)


def test_load_model_uses_model_key_and_yes_flag(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    progress: list[str] = []
    progress_state: list[tuple[str, float | None]] = []
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")
    model_dir = paths.llmster_home / ".lmstudio" / "models" / "unsloth" / "Qwen3.5-4B-GGUF"
    model_dir.mkdir(parents=True, exist_ok=True)
    main_file = model_dir / "Qwen3.5-4B-Q4_K_M.gguf"
    mmproj_file = model_dir / "mmproj-BF16.gguf"
    main_file.write_bytes(b"main")
    mmproj_file.write_bytes(b"mmproj")

    runtime = RuntimeManager(paths=paths, config=config)
    commands: list[list[str]] = []

    class FakeProcess:
        def __init__(self, command, **kwargs) -> None:
            commands.append(command)
            self.stdout = []

        def poll(self):
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return 0

    loaded_states = iter(
        [
            None,
            {"identifier": config.runtime.instance_id, "status": "idle"},
        ]
    )
    monotonic_values = iter([100.0, 100.0, 104.0, 105.0])

    monkeypatch.setattr(runtime, "unload_model", lambda progress, ignore_errors=False: None)
    monkeypatch.setattr(
        runtime,
        "_list_available_models",
        lambda: [
            {
                "path": "unsloth/Qwen3.5-4B-GGUF/Qwen3.5-4B-Q4_K_M.gguf",
                "modelKey": "unsloth/qwen3.5-4b-gguf/qwen3.5-4b-q4_k_m",
            }
        ],
    )
    monkeypatch.setattr(runtime, "_loaded_model_entry", lambda identifier: next(loaded_states))
    monkeypatch.setattr("screen_commentator_win.runtime.subprocess.Popen", FakeProcess)
    monkeypatch.setattr("screen_commentator_win.runtime.time.sleep", lambda *_: None)
    monkeypatch.setattr("screen_commentator_win.runtime.time.monotonic", lambda: next(monotonic_values))

    files = runtime.load_model(
        progress.append,
        lambda label, fraction: progress_state.append((label, fraction)),
    )

    assert files == ModelFiles(main_file=main_file, mmproj_file=mmproj_file)
    assert commands == [
        [
            str(paths.lms_executable),
            "load",
            "unsloth/qwen3.5-4b-gguf/qwen3.5-4b-q4_k_m",
            "--context-length",
            "16384",
            "--gpu",
            "max",
            "--identifier",
            "screen-commentator-vlm",
            "--yes",
        ]
    ]
    assert progress[-1] == "Multimodal model is loaded."
    assert progress_state == [
        ("Loading multimodal model... (estimated)", 0.5),
        ("Loading multimodal model...", 1.0),
    ]
