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
    assert auto["plan"] == ["step"] * 8
    assert auto["loops"] == 1
    assert auto["disturb_s"] == 5.0
    assert auto["recover_s"] == 4.0

    path = tmp_path / "custom.toml"
    path.write_text('[autotune]\nloops = 4\nplan = ["step", "disturbance"]\n', encoding="utf-8")
    config2 = AppConfig(path=path)
    assert config2.data["autotune"]["loops"] == 4
    assert config2.data["autotune"]["plan"] == ["step", "disturbance"]
    assert config2.data["autotune"]["disturb_s"] == 5.0


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
