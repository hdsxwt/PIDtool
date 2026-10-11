from __future__ import annotations

from pidtool.workspace import DEFAULT_SETTINGS, Workspace, ensure_gitignore


def test_create_and_settings_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    assert not Workspace.is_workspace(tmp_path)
    created = ws.ensure()
    assert created
    assert Workspace.is_workspace(tmp_path)
    settings = ws.load_settings()
    assert settings["serial"]["baud"] == DEFAULT_SETTINGS["serial"]["baud"]
    settings["gains"]["kp"] = 12.5
    ws.save_settings(settings)
    assert ws.load_settings()["gains"]["kp"] == 12.5
    assert (tmp_path / ".pidtool" / "settings.toml").is_file()
    assert ws.exports_dir.is_dir()
    assert ws.logs_dir.is_dir()
    assert not ws.ensure()


def test_trials_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    ws.append_trial({"kind": "manual_step", "gains": {"kp": 1.0}})
    ws.append_trial({"kind": "manual_step", "gains": {"kp": 2.0}})
    trials = ws.read_trials()
    assert len(trials) == 2
    assert trials[0]["kind"] == "manual_step"
    assert "ts" in trials[0]
    assert trials[1]["gains"]["kp"] == 2.0


def test_settings_deep_merge_keeps_defaults(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    ws.save_settings({"serial": {"port": "COM9"}})
    settings = ws.load_settings()
    assert settings["serial"]["port"] == "COM9"
    assert settings["serial"]["baud"] == DEFAULT_SETTINGS["serial"]["baud"]
    assert "safety" in settings


def test_gitignore_appended_and_idempotent(tmp_path):
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    assert ensure_gitignore(tmp_path)
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".pidtool/" in text
    assert ensure_gitignore(tmp_path)
    text_again = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert text_again.count(".pidtool") == 1


def test_gitignore_created_when_missing(tmp_path):
    assert ensure_gitignore(tmp_path)
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").strip() == ".pidtool/"
