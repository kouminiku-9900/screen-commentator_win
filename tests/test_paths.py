from __future__ import annotations

from screen_commentator_win.paths import AppPaths


def test_app_paths_respect_scw_app_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCW_APP_ROOT", str(tmp_path))
    paths = AppPaths.discover()

    assert paths.root == tmp_path
    assert paths.config_file == tmp_path / "config.toml"
    assert paths.llmster_home == tmp_path / "llmster-home"
