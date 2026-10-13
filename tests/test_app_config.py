from __future__ import annotations

import sys

import pytest

from pidtool.app_config import DEFAULT_LLM_MODEL, AppConfig

WINDOWS = sys.platform.startswith("win")


def test_default_model(tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    assert DEFAULT_LLM_MODEL == "deepseek-flash"
    assert config.data["llm"]["model"] == DEFAULT_LLM_MODEL


def test_legacy_model_migrated(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text('[llm]\nmodel = "deepseek-chat"\napi_key = "k"\n', encoding="utf-8")
    config = AppConfig(path=path)
    assert config.data["llm"]["model"] == "deepseek-flash"
    assert config.get_api_key() == "k"
    text = path.read_text(encoding="utf-8")
    assert 'api_key = "k"' not in text
    if WINDOWS:
        assert "dpapi:" in text


def test_empty_model_replaced(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text('[llm]\nmodel = ""\n', encoding="utf-8")
    config = AppConfig(path=path)
    assert config.data["llm"]["model"] == DEFAULT_LLM_MODEL


def test_custom_model_preserved(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text('[llm]\nmodel = "deepseek-v4-pro"\n', encoding="utf-8")
    config = AppConfig(path=path)
    assert config.data["llm"]["model"] == "deepseek-v4-pro"


def test_autotune_defaults_and_merge(tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    auto = config.data["autotune"]
    assert auto["schema"] == 2
    assert auto["cycles"] == 2
    assert auto["rounds"][0]["actions"][0]["target_sp"] == 300.0
    assert auto["rounds"][0]["actions"][1]["target_sp"] == 600.0

    path = tmp_path / "custom.toml"
    path.write_text(
        '[autotune]\ncycles = 4\n'
        '[[autotune.rounds]]\nname = "A"\n'
        '[[autotune.rounds.actions]]\nkind = "disturbance"\ntarget_sp = 5.0\n',
        encoding="utf-8",
    )
    config2 = AppConfig(path=path)
    assert config2.data["autotune"]["cycles"] == 4
    assert config2.data["autotune"]["rounds"][0]["name"] == "A"
    assert config2.data["autotune"]["rounds"][0]["actions"][0]["kind"] == "disturbance"
    assert config2.data["autotune"]["rounds"][0]["actions"][0]["target_sp"] == 5.0


def test_legacy_autotune_migrated(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text(
        '[autotune]\nplan = ["step", "disturbance"]\nloops = 3\n'
        "disturb_s = 2.0\nrecover_s = 1.5\n",
        encoding="utf-8",
    )
    config = AppConfig(path=path)
    auto = config.data["autotune"]
    assert auto["schema"] == 2
    assert auto["cycles"] == 3
    assert len(auto["rounds"]) == 2
    assert auto["rounds"][0]["actions"][0]["kind"] == "step"
    disturbance = auto["rounds"][1]["actions"][0]
    assert disturbance["kind"] == "disturbance"
    assert disturbance["disturb_s"] == 2.0
    assert disturbance["recover_s"] == 1.5
    assert "plan" not in auto
    assert "loops" not in auto

    import tomllib

    reloaded = tomllib.loads(path.read_text(encoding="utf-8"))
    assert len(reloaded["autotune"]["rounds"]) == 2


def test_corrupt_config_falls_back_to_defaults(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text("llm = [broken", encoding="utf-8")
    config = AppConfig(path=path)
    assert config.data["llm"]["model"] == DEFAULT_LLM_MODEL
    assert config.data["llm"]["base_url"] == "https://api.deepseek.com"


def test_save_and_reload_roundtrip(tmp_path):
    path = tmp_path / "app.toml"
    config = AppConfig(path=path)
    config.data["llm"]["api_key"] = "secret-key"
    config.data["ui"]["window_s"] = 20.0
    config.save()
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "secret-key" not in text
    reloaded = AppConfig(path=path)
    assert reloaded.get_api_key() == "secret-key"
    assert reloaded.data["ui"]["window_s"] == 20.0


def test_set_api_key_encrypts_at_rest(tmp_path):
    path = tmp_path / "app.toml"
    config = AppConfig(path=path)
    config.set_api_key("s3cr3t-value")
    config.save()
    text = path.read_text(encoding="utf-8")
    assert "s3cr3t-value" not in text
    if WINDOWS:
        assert "dpapi:" in text
    assert AppConfig(path=path).get_api_key() == "s3cr3t-value"


def test_set_empty_api_key_clears(tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    config.set_api_key("something")
    config.set_api_key("")
    assert config.get_api_key() == ""
    assert config.data["llm"]["api_key"] == ""


def test_add_recent_deduplicates_and_caps(tmp_path):
    path = tmp_path / "app.toml"
    config = AppConfig(path=path)
    config.add_recent(str(tmp_path / "a"))
    config.add_recent(str(tmp_path / "b"))
    config.add_recent(str(tmp_path / "a"))
    assert config.data["recent_workspaces"][0] == str(tmp_path / "a")
    assert config.data["recent_workspaces"].count(str(tmp_path / "a")) == 1
    assert config.data["last_workspace"] == str(tmp_path / "a")

    for index in range(15):
        config.add_recent(str(tmp_path / f"ws{index}"))
    assert len(config.data["recent_workspaces"]) == 10
    assert config.data["recent_workspaces"][0] == str(tmp_path / "ws14")
