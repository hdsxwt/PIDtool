from __future__ import annotations

from pidtool.app_config import DEFAULT_LLM_MODEL, AppConfig


def test_default_model(tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    assert DEFAULT_LLM_MODEL == "deepseek-flash"
    assert config.data["llm"]["model"] == DEFAULT_LLM_MODEL


def test_legacy_model_migrated(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text('[llm]\nmodel = "deepseek-chat"\napi_key = "k"\n', encoding="utf-8")
    config = AppConfig(path=path)
    assert config.data["llm"]["model"] == "deepseek-flash"
    assert config.data["llm"]["api_key"] == "k"


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
