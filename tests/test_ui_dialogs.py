from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets

from pidtool.app_config import DEFAULT_LLM_MODEL, AppConfig
from pidtool.ui.dialogs import AutotunePlanDialog, LLMSettingsDialog
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
    assert config.get_api_key() == "secret"
    assert "已配置 Key" in window.llm_summary.text()
    assert not hasattr(window, "llm_base_edit")
    window.close()


def test_layout_compaction(qapp, tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    window = MainWindow(app_config=config)
    assert window.metrics_table.maximumHeight() <= 200
    assert window.llm_settings_btn.text() == "DeepSeek 设置…"
    window.close()


def test_autotune_plan_dialog_roundtrip(qapp):
    dialog = AutotunePlanDialog(
        {"plan": ["step", "disturbance"], "loops": 3, "disturb_s": 2.0, "recover_s": 1.5}
    )
    assert dialog._plan() == ["step", "disturbance"]
    values = dialog.values()
    assert values["loops"] == 3
    assert values["disturb_s"] == 2.0
    assert values["recover_s"] == 1.5
    assert "共 6 轮" in dialog.summary.text()

    dialog._add_row("step")
    assert dialog._plan() == ["step", "disturbance", "step"]
    dialog.list.setCurrentRow(2)
    dialog._move_selected(-1)
    assert dialog._plan() == ["step", "step", "disturbance"]
    dialog._remove_selected()
    assert dialog._plan() == ["step", "disturbance"]
    assert "共 6 轮" in dialog.summary.text()
    dialog.close()


def test_apply_plan_settings_persists(qapp, tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    window = MainWindow(app_config=config)
    window._apply_plan_settings(
        {"plan": ["step", "disturbance"], "loops": 2, "disturb_s": 3.0, "recover_s": 2.0}
    )
    auto = config.data["autotune"]
    assert auto["plan"] == ["step", "disturbance"]
    assert auto["loops"] == 2
    assert auto["disturb_s"] == 3.0
    assert auto["recover_s"] == 2.0
    assert "→ 4 轮" in window.ai_plan_summary.text()
    window.close()


def test_autotune_plan_dialog_empty_plan_gets_default_row(qapp):
    dialog = AutotunePlanDialog({"plan": []})
    assert dialog._plan() == ["step"]
    assert "共 1 轮" in dialog.summary.text()
    dialog.close()


def test_autotune_plan_dialog_remove_last_row_adds_default(qapp):
    dialog = AutotunePlanDialog({"plan": ["step"]})
    dialog.list.setCurrentRow(0)
    dialog._remove_selected()
    assert dialog._plan() == ["step"]
    dialog.close()


def test_autotune_plan_dialog_move_without_selection_is_noop(qapp):
    dialog = AutotunePlanDialog({"plan": ["step", "disturbance"]})
    dialog.list.setCurrentRow(-1)
    dialog._move_selected(1)
    assert dialog._plan() == ["step", "disturbance"]
    dialog.list.setCurrentRow(0)
    dialog._move_selected(-1)
    assert dialog._plan() == ["step", "disturbance"]
    dialog.list.setCurrentRow(1)
    dialog._move_selected(1)
    assert dialog._plan() == ["step", "disturbance"]
    dialog.close()
