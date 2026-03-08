from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


APP_DIR_NAME = "ScreenCommentatorWin"
APP_ROOT_ENV = "SCW_APP_ROOT"


@dataclass(frozen=True)
class ResolvedLmStudioPaths:
    home_root: Path | None
    lmstudio_home: Path
    lms_executable: Path


@dataclass(frozen=True)
class AppPaths:
    root: Path
    config_file: Path
    logs_dir: Path
    state_dir: Path
    llmster_home: Path
    llmstudio_home: Path
    llmstudio_bin_dir: Path
    lms_executable: Path
    llmster_install_location_file: Path
    install_script_cache: Path
    app_log_file: Path

    @classmethod
    def discover(cls) -> "AppPaths":
        override_root = os.environ.get(APP_ROOT_ENV)
        if override_root:
            root = Path(override_root).expanduser()
            logs_dir = root / "logs"
            state_dir = root / "state"
            llmster_home = root / "llmster-home"
            llmstudio_home = llmster_home / ".lmstudio"
            llmstudio_bin_dir = llmstudio_home / "bin"
            return cls(
                root=root,
                config_file=root / "config.toml",
                logs_dir=logs_dir,
                state_dir=state_dir,
                llmster_home=llmster_home,
                llmstudio_home=llmstudio_home,
                llmstudio_bin_dir=llmstudio_bin_dir,
                lms_executable=llmstudio_bin_dir / "lms.exe",
                llmster_install_location_file=llmstudio_home / ".internal" / "llmster-install-location.json",
                install_script_cache=state_dir / "install-llmster.ps1",
                app_log_file=logs_dir / "screen-commentator.log",
            )

        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            base_root = Path(local_appdata)
        else:
            base_root = Path.home() / "AppData" / "Local"

        root = base_root / APP_DIR_NAME
        logs_dir = root / "logs"
        state_dir = root / "state"
        llmster_home = root / "llmster-home"
        llmstudio_home = llmster_home / ".lmstudio"
        llmstudio_bin_dir = llmstudio_home / "bin"

        return cls(
            root=root,
            config_file=root / "config.toml",
            logs_dir=logs_dir,
            state_dir=state_dir,
            llmster_home=llmster_home,
            llmstudio_home=llmstudio_home,
            llmstudio_bin_dir=llmstudio_bin_dir,
            lms_executable=llmstudio_bin_dir / "lms.exe",
            llmster_install_location_file=llmstudio_home / ".internal" / "llmster-install-location.json",
            install_script_cache=state_dir / "install-llmster.ps1",
            app_log_file=logs_dir / "screen-commentator.log",
        )

    def ensure_directories(self) -> None:
        for path in (self.root, self.logs_dir, self.state_dir, self.llmster_home):
            path.mkdir(parents=True, exist_ok=True)

    def resolve_installation(self) -> ResolvedLmStudioPaths | None:
        candidate = self.app_local_installation()
        if candidate.lms_executable.exists():
            return candidate
        return None

    def candidate_installations(self) -> list[ResolvedLmStudioPaths]:
        return [self.app_local_installation()]

    def app_local_installation(self) -> ResolvedLmStudioPaths:
        return ResolvedLmStudioPaths(
            home_root=self.llmster_home,
            lmstudio_home=self._lmstudio_home_for_home_root(self.llmster_home),
            lms_executable=self.lms_executable,
        )

    @staticmethod
    def _lmstudio_home_for_home_root(home_root: Path) -> Path:
        pointer_file = home_root / ".lmstudio-home-pointer"
        if pointer_file.exists():
            target = pointer_file.read_text(encoding="utf-8").strip()
            if target:
                return Path(target)
        return home_root / ".lmstudio"
