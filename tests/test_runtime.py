from __future__ import annotations

import json
import subprocess

import httpx

from screen_commentator_win.models import AppConfig
from screen_commentator_win.paths import AppPaths
from screen_commentator_win.runtime import RuntimeErrorWithDetails
from screen_commentator_win.runtime import RuntimeManager


def test_download_model_polls_until_completion(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    progress: list[str] = []
    seen_requests: list[tuple[str, dict[str, object]]] = []
    polls = iter(
        [
            {"status": "downloading", "downloaded_bytes": 50, "total_size_bytes": 100},
            {"status": "completed", "downloaded_bytes": 100, "total_size_bytes": 100},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen_requests.append((str(request.url), json.loads(request.content.decode("utf-8"))))
            return httpx.Response(200, json={"status": "queued", "job_id": "job-1"})
        return httpx.Response(200, json=next(polls))

    monkeypatch.setattr("screen_commentator_win.runtime.time.sleep", lambda *_: None)
    runtime = RuntimeManager(
        paths=paths,
        config=config,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    runtime.download_model(progress.append)

    assert seen_requests == [
        (
            "http://127.0.0.1:12346/api/v1/models/download",
            {
                "model": config.runtime.model_repo_url,
                "quantization": config.runtime.quantization,
            },
        )
    ]
    assert progress[-1] == "Model download completed."


def test_install_llmster_detects_global_install_when_bootstrap_ignores_app_home(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setattr("screen_commentator_win.paths.actual_user_home", lambda: tmp_path / "user-home")
    paths = AppPaths.discover()
    config = AppConfig()
    progress: list[str] = []
    progress_state: list[tuple[str, float | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="installer")

    runtime = RuntimeManager(
        paths=paths,
        config=config,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    def fake_run_command(command, progress, check, home_root=None):
        global_lms = tmp_path / "user-home" / ".lmstudio" / "bin"
        global_lms.mkdir(parents=True, exist_ok=True)
        (global_lms / "lms.exe").write_bytes(b"binary")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runtime, "_run_command", fake_run_command)

    runtime.install_llmster(progress.append, lambda label, fraction: progress_state.append((label, fraction)))

    installation = paths.resolve_installation()
    assert installation is not None
    assert installation.lms_executable == tmp_path / "user-home" / ".lmstudio" / "bin" / "lms.exe"
    assert ("llmster installed.", 1.0) in progress_state
    assert any("ignored the requested app-local home" in line for line in progress)


def test_runtime_environment_uses_resolved_home_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setattr("screen_commentator_win.paths.actual_user_home", lambda: tmp_path / "user-home")
    paths = AppPaths.discover()
    config = AppConfig()
    global_lms = tmp_path / "user-home" / ".lmstudio" / "bin"
    global_lms.mkdir(parents=True, exist_ok=True)
    (global_lms / "lms.exe").write_bytes(b"binary")

    runtime = RuntimeManager(paths=paths, config=config)
    env = runtime._runtime_environment()

    assert env["HOME"] == str(tmp_path / "user-home")
    assert env["USERPROFILE"] == str(tmp_path / "user-home")
    assert env["LMS_NO_MODIFY_PATH"] == "1"


def test_start_daemon_falls_back_to_user_home_install(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setattr("screen_commentator_win.paths.actual_user_home", lambda: tmp_path / "user-home")
    paths = AppPaths.discover()
    config = AppConfig()
    local_lms = paths.llmstudio_bin_dir
    local_lms.mkdir(parents=True, exist_ok=True)
    (local_lms / "lms.exe").write_bytes(b"local")
    user_lms = tmp_path / "user-home" / ".lmstudio" / "bin"
    user_lms.mkdir(parents=True, exist_ok=True)
    (user_lms / "lms.exe").write_bytes(b"user")
    progress: list[str] = []

    runtime = RuntimeManager(paths=paths, config=config)

    def fake_run_command(command, progress, check, home_root=None):
        executable = command[0]
        if executable == str(local_lms / "lms.exe"):
            return subprocess.CompletedProcess(command, 1, "", "invalid passkey")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runtime, "_run_command", fake_run_command)

    runtime.start_daemon(progress.append)

    assert runtime.lms_executable_path == str(user_lms / "lms.exe")
    assert any("trying another installation" in line for line in progress)


def test_start_daemon_copies_global_cli_key_for_app_local_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setattr("screen_commentator_win.paths.actual_user_home", lambda: tmp_path / "user-home")
    paths = AppPaths.discover()
    config = AppConfig()
    progress: list[str] = []
    local_lms = paths.llmstudio_bin_dir
    local_lms.mkdir(parents=True, exist_ok=True)
    (local_lms / "lms.exe").write_bytes(b"local")
    source_key = tmp_path / "user-home" / ".lmstudio" / ".internal" / "lms-key-2"
    source_key.parent.mkdir(parents=True, exist_ok=True)
    source_key.write_text("shared-key", encoding="utf-8")

    runtime = RuntimeManager(paths=paths, config=config)

    def fake_run_command(command, progress, check, home_root=None):
        copied_key = paths.llmstudio_home / ".internal" / "lms-key-2"
        assert copied_key.read_text(encoding="utf-8") == "shared-key"
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runtime, "_run_command", fake_run_command)

    runtime.start_daemon(progress.append)

    assert (paths.llmstudio_home / ".internal" / "lms-key-2").exists()
    assert any("Copied LM Studio CLI key" in line for line in progress)


def test_wait_for_server_accepts_successful_launcher_exit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    runtime = RuntimeManager(paths=paths, config=config)
    progress: list[str] = []
    lms_dir = paths.llmstudio_bin_dir
    lms_dir.mkdir(parents=True, exist_ok=True)
    (lms_dir / "lms.exe").write_bytes(b"binary")
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


def test_verify_model_files_requires_main_and_mmproj(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    lms_dir = paths.llmstudio_bin_dir
    lms_dir.mkdir(parents=True, exist_ok=True)
    (lms_dir / "lms.exe").write_bytes(b"binary")
    model_dir = paths.llmster_home / ".lmstudio" / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    main_file = model_dir / "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"
    mmproj_file = model_dir / "mmproj-Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-BF16.gguf"
    main_file.write_bytes(b"main")
    mmproj_file.write_bytes(b"mmproj")

    runtime = RuntimeManager(paths=paths, config=config)
    files = runtime.verify_model_files()

    assert files.main_file == main_file
    assert files.mmproj_file == mmproj_file


def test_start_server_falls_back_to_user_home_install(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setattr("screen_commentator_win.paths.actual_user_home", lambda: tmp_path / "user-home")
    paths = AppPaths.discover()
    config = AppConfig()
    progress: list[str] = []
    local_lms = paths.llmstudio_bin_dir
    local_lms.mkdir(parents=True, exist_ok=True)
    (local_lms / "lms.exe").write_bytes(b"local")
    user_lms = tmp_path / "user-home" / ".lmstudio" / "bin"
    user_lms.mkdir(parents=True, exist_ok=True)
    (user_lms / "lms.exe").write_bytes(b"user")

    runtime = RuntimeManager(paths=paths, config=config)

    class FakeProcess:
        def __init__(self, command, **kwargs) -> None:
            self.command = command
            self.stdout = []

        def poll(self):
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return 0

    monkeypatch.setattr("screen_commentator_win.runtime.subprocess.Popen", FakeProcess)
    monkeypatch.setattr(runtime, "_server_status_for_installation", lambda installation: {"running": False})

    def fake_wait_for_server(progress, installation=None, process=None):
        assert installation is not None
        if installation.lms_executable == local_lms / "lms.exe":
            raise RuntimeErrorWithDetails("not ready")

    monkeypatch.setattr(runtime, "_wait_for_server", fake_wait_for_server)

    runtime.start_server(progress.append)

    assert runtime.lms_executable_path == str(user_lms / "lms.exe")
    assert any("trying another installation" in line for line in progress)


def test_load_model_uses_model_key_and_yes_flag(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    progress: list[str] = []
    progress_state: list[tuple[str, float | None]] = []
    lms_dir = paths.llmstudio_bin_dir
    lms_dir.mkdir(parents=True, exist_ok=True)
    (lms_dir / "lms.exe").write_bytes(b"binary")
    model_dir = paths.llmster_home / ".lmstudio" / "models" / "HauhauCS" / "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive"
    model_dir.mkdir(parents=True, exist_ok=True)
    main_file = model_dir / "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"
    mmproj_file = model_dir / "mmproj-Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-BF16.gguf"
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
                "path": "HauhauCS/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive/"
                "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf",
                "modelKey": "qwen3.5-4b-uncensored-hauhaucs-aggressive",
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

    assert files.main_file == main_file
    assert commands == [
        [
            str(lms_dir / "lms.exe"),
            "load",
            "qwen3.5-4b-uncensored-hauhaucs-aggressive",
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
