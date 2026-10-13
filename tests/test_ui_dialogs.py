from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets

from pidtool.app_config import DEFAULT_LLM_MODEL, AppConfig
from pidtool.ui.dialogs import LLMSettingsDialog
from pidtool.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    yield app


def test_llm_settings_dialog_roundtrip(qapp):
    dialog = LLMSettingsDialog(
        {"base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key": "k1"}
    )
    assert dialog.base_edit.text() == "https://api.deepseek.com"
    assert dialog.model_edit.text() == "deepseek-flash"
    assert dialog.key_edit.text() == "k1"
    assert dialog.key_edit.echoMode() == QtWidgets.QLineEdit.EchoMode.Password
    dialog.model_edit.setText("deepseek-v4-pro")
    dialog.key_edit.setText("k2")
    values = dialog.values()
    assert values == {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "api_key": "k2",
    }
    dialog.close()


def test_apply_llm_settings_persists_and_updates_summary(qapp, tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    window = MainWindow(app_config=config)
    assert "未配置 Key" in window.llm_summary.text()
    window._apply_llm_settings(
        {"base_url": "", "model": "", "api_key": "secret"}
    )
    assert config.data["llm"]["model"] == DEFAULT_LLM_MODEL
    assert config.data["llm"]["base_url"] == "https://api.deepseek.com"
    assert config.data["llm"]["api_key"] == "secret"
    assert "已配置 Key" in window.llm_summary.text()
    assert not hasattr(window, "llm_base_edit")
    window.close()


def test_layout_compaction(qapp, tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    window = MainWindow(app_config=config)
    assert window.metrics_table.maximumHeight() <= 200
    assert window.llm_settings_btn.text() == "DeepSeek 设置…"
    window.close()
