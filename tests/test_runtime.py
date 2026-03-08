from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import httpx
import pytest

from screen_commentator_win.models import AppConfig
from screen_commentator_win.models import ModelFiles
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
    monkeypatch.setattr(runtime, "_kill_stale_processes", lambda progress: None)

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
    monkeypatch.setattr(runtime, "_kill_stale_processes", lambda progress: None)
    monkeypatch.setattr(runtime, "_daemon_status_command_succeeds", lambda installation: False)
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


def test_start_server_prefers_bundled_lms_when_available(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path / "app-root"))
    paths = AppPaths.discover()
    config = AppConfig()
    progress: list[str] = []
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"local")
    daemon_executable = paths.llmstudio_home / "llmster" / "0.0.6-1" / "llmster.exe"
    bundled_lms = daemon_executable.parent / ".bundle" / "lms.exe"
    bundled_lms.parent.mkdir(parents=True, exist_ok=True)
    daemon_executable.write_bytes(b"daemon")
    bundled_lms.write_bytes(b"bundle-lms")
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
        return FakeProcess()

    def fake_wait_for_server(progress_callback, installation=None, process=None):
        progress_callback("llmster server is ready.")

    monkeypatch.setattr("screen_commentator_win.runtime.subprocess.Popen", fake_popen)
    monkeypatch.setattr(runtime, "_server_status_for_installation", lambda installation: {"running": False})
    monkeypatch.setattr(runtime, "_wait_for_server", fake_wait_for_server)

    runtime.start_server(progress.append)

    assert captured["command"][0] == str(bundled_lms)


def test_start_server_falls_back_to_installation_lms_when_bundled_fails(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path / "app-root"))
    paths = AppPaths.discover()
    config = AppConfig()
    progress: list[str] = []
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"local")
    daemon_executable = paths.llmstudio_home / "llmster" / "0.0.6-1" / "llmster.exe"
    bundled_lms = daemon_executable.parent / ".bundle" / "lms.exe"
    bundled_lms.parent.mkdir(parents=True, exist_ok=True)
    daemon_executable.write_bytes(b"daemon")
    bundled_lms.write_bytes(b"bundle-lms")
    paths.llmster_install_location_file.parent.mkdir(parents=True, exist_ok=True)
    paths.llmster_install_location_file.write_text(
        json.dumps({"path": str(daemon_executable)}),
        encoding="utf-8",
    )

    runtime = RuntimeManager(paths=paths, config=config)
    commands: list[list[str]] = []

    class FakeProcess:
        def __init__(self, command) -> None:
            self.command = command
            self.stdout = []

        def poll(self):
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return 0

    def fake_popen(command, **kwargs):
        commands.append(command)
        return FakeProcess(command)

    wait_calls = 0

    def fake_wait_for_server(progress_callback, installation=None, process=None):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            raise RuntimeErrorWithDetails("bundled cli failed")
        progress_callback("llmster server is ready.")

    monkeypatch.setattr("screen_commentator_win.runtime.subprocess.Popen", fake_popen)
    monkeypatch.setattr(runtime, "_server_status_for_installation", lambda installation: {"running": False})
    monkeypatch.setattr(runtime, "_wait_for_server", fake_wait_for_server)
    monkeypatch.setattr(runtime, "_terminate_process_tree", lambda process: None)

    runtime.start_server(progress.append)

    assert commands == [
        [str(bundled_lms), "server", "start", "--port", str(config.runtime.port)],
        [str(paths.lms_executable), "server", "start", "--port", str(config.runtime.port)],
    ]
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


def test_load_model_uses_rest_load_and_tracks_active_instance(monkeypatch, tmp_path) -> None:
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

    seen_requests: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append((str(request.url), json.loads(request.content.decode("utf-8"))))
        return httpx.Response(200, json={"instance_id": "loaded-qwen-instance"})

    runtime = RuntimeManager(
        paths=paths,
        config=config,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

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

    files = runtime.load_model(
        progress.append,
        lambda label, fraction: progress_state.append((label, fraction)),
    )

    assert files == ModelFiles(main_file=main_file, mmproj_file=mmproj_file)
    assert seen_requests == [
        (
            "http://127.0.0.1:12346/api/v1/models/load",
            {
                "model": "unsloth/qwen3.5-4b-gguf/qwen3.5-4b-q4_k_m",
                "context_length": config.runtime.context_length,
            },
        )
    ]
    assert runtime.create_inference_client().instance_id == "loaded-qwen-instance"
    assert progress[-1] == "Multimodal model is loaded."
    assert progress_state == [
        ("Loading multimodal model...", 1.0),
    ]


def test_stop_server_terminates_tracked_process_without_cli(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    progress: list[str] = []
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")

    runtime = RuntimeManager(paths=paths, config=config)
    taskkill_commands: list[list[str]] = []

    class FakeProcess:
        pid = 222

        def poll(self):
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return 0

    def fake_subprocess_run(command, **kwargs):
        taskkill_commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    runtime._server_process = FakeProcess()
    monkeypatch.setattr("screen_commentator_win.runtime.subprocess.run", fake_subprocess_run)

    runtime.stop_server(progress.append)

    assert taskkill_commands == [["taskkill", "/F", "/T", "/PID", "222"]]
    assert runtime._server_process is None
    assert progress == ["Stopping llmster server..."]


def test_stop_daemon_terminates_tracked_and_stale_processes(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    progress: list[str] = []
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")
    key_dir = paths.llmstudio_home / ".internal"
    key_dir.mkdir(parents=True, exist_ok=True)
    key_file = key_dir / "lms-key-2"
    key_file.write_text("key", encoding="utf-8")

    runtime = RuntimeManager(paths=paths, config=config)
    runtime._active_instance_id = "loaded-qwen-instance"

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def poll(self):
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return 0

    runtime._server_process = FakeProcess(222)
    runtime._daemon_process = FakeProcess(111)

    app_local_daemon = paths.llmstudio_home / "llmster" / "0.0.6-1" / "llmster.exe"
    unrelated_daemon = tmp_path / "other-home" / "llmster.exe"
    query_payload = [
        {"Id": 333, "ProcessName": "llmster", "Path": str(app_local_daemon)},
        {"Id": 444, "ProcessName": "llmster", "Path": str(unrelated_daemon)},
    ]
    query_commands: list[list[str]] = []
    taskkill_commands: list[list[str]] = []

    def fake_subprocess_run(command, **kwargs):
        if command[0] == "powershell":
            query_commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, json.dumps(query_payload), "")
        if command[0] == "taskkill":
            taskkill_commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("screen_commentator_win.runtime.subprocess.run", fake_subprocess_run)

    runtime.stop_daemon(progress.append)

    assert len(query_commands) == 1
    assert taskkill_commands == [
        ["taskkill", "/F", "/T", "/PID", "222"],
        ["taskkill", "/F", "/T", "/PID", "111"],
        ["taskkill", "/F", "/T", "/PID", "333"],
    ]
    assert runtime._server_process is None
    assert runtime._daemon_process is None
    assert runtime._active_instance_id is None
    assert runtime._daemon_recent_output == []
    assert not key_file.exists()


def test_kill_stale_processes_runs_powershell_with_home_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")
    # Create key file to verify it gets cleaned up.
    key_dir = paths.llmstudio_home / ".internal"
    key_dir.mkdir(parents=True, exist_ok=True)
    key_file = key_dir / "lms-key-2"
    key_file.write_text("key", encoding="utf-8")
    runtime = RuntimeManager(paths=paths, config=config)
    progress: list[str] = []

    app_local_daemon = paths.llmstudio_home / "llmster" / "0.0.6-1" / "llmster.exe"
    legacy_temp_daemon = (
        Path(tempfile.gettempdir())
        / "scw-llmster-direct-legacy"
        / "llmster-home"
        / ".lmstudio"
        / "llmster"
        / "0.0.6-1"
        / "llmster.exe"
    )
    unrelated_daemon = tmp_path / "other-home" / "llmster.exe"

    query_payload = [
        {"Id": 101, "ProcessName": "llmster", "Path": str(app_local_daemon)},
        {"Id": 202, "ProcessName": "llmster", "Path": str(legacy_temp_daemon)},
        {"Id": 303, "ProcessName": "llmster", "Path": str(unrelated_daemon)},
    ]
    query_commands: list[list[str]] = []
    taskkill_commands: list[list[str]] = []

    def fake_subprocess_run(command, **kwargs):
        if command[0] == "powershell":
            query_commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, json.dumps(query_payload), "")
        if command[0] == "taskkill":
            taskkill_commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("screen_commentator_win.runtime.subprocess.run", fake_subprocess_run)

    runtime._kill_stale_processes(progress.append)

    assert len(query_commands) == 1
    assert query_commands[0][:3] == ["powershell", "-NoProfile", "-Command"]
    assert taskkill_commands == [
        ["taskkill", "/F", "/T", "/PID", "101"],
        ["taskkill", "/F", "/T", "/PID", "202"],
    ]
    assert not key_file.exists()


def test_wait_for_server_accepts_launcher_exit_after_http_ready(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    runtime = RuntimeManager(paths=paths, config=config)
    progress: list[str] = []
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")

    class FakeProcess:
        def poll(self):
            return 1

    status_values = iter(
        [
            {"running": False},
            {"running": True, "port": config.runtime.port},
        ]
    )
    time_values = iter([0.0, 0.0, 0.1])

    monkeypatch.setattr(
        runtime,
        "_server_status_for_installation",
        lambda installation: next(status_values),
    )
    monkeypatch.setattr("screen_commentator_win.runtime.time.sleep", lambda *_: None)
    monkeypatch.setattr("screen_commentator_win.runtime.time.time", lambda: next(time_values))

    runtime._wait_for_server(
        progress.append,
        installation=paths.app_local_installation(),
        process=FakeProcess(),
    )

    assert progress[-1] == "llmster server is ready."


def test_wait_for_app_local_cli_key_accepts_launcher_exit_after_daemon_ready(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    runtime = RuntimeManager(paths=paths, config=config)
    progress: list[str] = []
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")

    class FakeProcess:
        def poll(self):
            return 0

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return 0

    key_file = tmp_path / "lms-key-2"
    key_file.write_text("key", encoding="utf-8")
    runtime._daemon_process = FakeProcess()

    ready_states = iter([False, True])
    time_values = iter([0.0, 0.0, 0.1])

    monkeypatch.setattr(
        runtime,
        "_daemon_status_command_succeeds",
        lambda installation: next(ready_states),
    )
    monkeypatch.setattr("screen_commentator_win.runtime.time.sleep", lambda *_: None)
    monkeypatch.setattr("screen_commentator_win.runtime.time.time", lambda: next(time_values))

    runtime._wait_for_app_local_cli_key(progress.append, key_file)

    assert progress[-1] == "Isolated llmster daemon is ready."
    assert runtime._daemon_process is None


def test_wait_for_app_local_cli_key_raises_on_early_exit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    runtime = RuntimeManager(paths=paths, config=config)
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")

    class FakeProcess:
        def poll(self):
            return 1

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return 1

    runtime._daemon_process = FakeProcess()
    runtime._daemon_recent_output = ["Cannot start: llmster already running"]

    key_file = tmp_path / "lms-key-2"
    key_file.write_text("key", encoding="utf-8")

    monkeypatch.setattr(runtime, "_daemon_status_command_succeeds", lambda installation: False)
    monkeypatch.setattr("screen_commentator_win.runtime.time.sleep", lambda *_: None)
    monkeypatch.setattr("screen_commentator_win.runtime.time.time", lambda: 0.0)

    with pytest.raises(RuntimeErrorWithDetails, match="already running"):
        runtime._wait_for_app_local_cli_key(lambda msg: None, key_file)

    assert not key_file.exists()
    assert runtime._daemon_process is None
    assert runtime._daemon_recent_output == []


def test_wait_for_app_local_cli_key_times_out_when_daemon_never_becomes_ready(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    runtime = RuntimeManager(paths=paths, config=config)
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")

    class FakeProcess:
        def poll(self):
            return 0

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            return 0

    key_file = tmp_path / "lms-key-2"
    key_file.write_text("key", encoding="utf-8")
    runtime._daemon_process = FakeProcess()
    runtime._daemon_recent_output = ["App is quitting"]

    time_values = iter([0.0, 0.0, 61.0])

    monkeypatch.setattr(runtime, "_daemon_status_command_succeeds", lambda installation: False)
    monkeypatch.setattr("screen_commentator_win.runtime.time.sleep", lambda *_: None)
    monkeypatch.setattr("screen_commentator_win.runtime.time.time", lambda: next(time_values))

    with pytest.raises(RuntimeErrorWithDetails, match="Timed out waiting for the isolated llmster daemon"):
        runtime._wait_for_app_local_cli_key(lambda msg: None, key_file)

    assert not key_file.exists()
    assert runtime._daemon_process is None
    assert runtime._daemon_recent_output == []


def test_start_daemon_retries_on_singleton_conflict(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path / "app-root"))
    paths = AppPaths.discover()
    config = AppConfig()
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")
    runtime = RuntimeManager(paths=paths, config=config)

    attempt_count = 0
    kill_count = 0

    def fake_attempt(progress, installation):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            raise RuntimeErrorWithDetails("llmster already running")
        # Second attempt succeeds

    def fake_kill(progress):
        nonlocal kill_count
        kill_count += 1

    monkeypatch.setattr(runtime, "_attempt_daemon_start", fake_attempt)
    monkeypatch.setattr(runtime, "_kill_stale_processes", fake_kill)
    monkeypatch.setattr("screen_commentator_win.runtime.time.sleep", lambda *_: None)

    progress: list[str] = []
    runtime.start_daemon(progress.append)

    assert attempt_count == 2
    assert kill_count == 2  # Once at start, once before retry


def test_download_model_falls_back_to_cli_on_404(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()
    config = AppConfig()
    paths.llmstudio_bin_dir.mkdir(parents=True, exist_ok=True)
    paths.lms_executable.write_bytes(b"binary")
    progress: list[str] = []

    model_dir = paths.llmster_home / ".lmstudio" / "models" / "unsloth" / "Qwen3.5-4B-GGUF"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "Qwen3.5-4B-Q4_K_M.gguf").write_bytes(b"main")
    (model_dir / "mmproj-BF16.gguf").write_bytes(b"mmproj")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    runtime = RuntimeManager(
        paths=paths,
        config=config,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    commands: list[list[str]] = []

    class FakeProcess:
        def __init__(self, command, **kwargs) -> None:
            commands.append(command)
            self.stdout = iter(["Downloading... 50%\n", "Download complete 100%\n"])
            self._exited = False

        def poll(self):
            return 0 if self._exited else None

        def wait(self, timeout=None):
            self._exited = True
            return 0

        def terminate(self) -> None:
            self._exited = True

    monkeypatch.setattr("screen_commentator_win.runtime.subprocess.Popen", FakeProcess)

    runtime.download_model(progress.append)

    assert len(commands) == 1
    assert "get" in commands[0]
    assert "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF@q4_k_m" in commands[0]
    assert "--gguf" in commands[0]
    assert progress[-1] == "Model download completed."


def test_download_fraction_from_line() -> None:
    assert RuntimeManager._download_fraction_from_line("Downloading 50.5%") == pytest.approx(0.505)
    assert RuntimeManager._download_fraction_from_line("100 %") == 1.0
    assert RuntimeManager._download_fraction_from_line("no percentage here") is None
    assert RuntimeManager._download_fraction_from_line("") is None
