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


def test_bottom_panel_sizes_follow_tabs(qapp, tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    window = MainWindow(app_config=config)
    window.show()
    qapp.processEvents()
    qapp.processEvents()
    assert window.bottom_tabs.currentIndex() == 0
    assert window.main_splitter.sizes()[1] == 220

    window.bottom_tabs.setCurrentIndex(1)
    assert window.main_splitter.sizes()[1] == 220

    window.bottom_tabs.setCurrentIndex(2)
    assert window.main_splitter.sizes()[1] == 330

    window.bottom_tabs.setCurrentIndex(3)
    assert window.main_splitter.sizes()[1] == 330

    window.bottom_tabs.setCurrentIndex(0)
    assert window.main_splitter.sizes()[1] == 220
    window.close()


def test_autotune_plan_dialog_roundtrip(qapp):
    dialog = AutotunePlanDialog(
        {
            "cycles": 3,
            "rounds": [
                {"name": "轮次 1", "actions": [{"kind": "step"}]},
                {"name": "轮次 2", "actions": [{"kind": "step"}, {"kind": "disturbance"}]},
            ],
        }
    )
    values = dialog.values()
    assert values["cycles"] == 3
    assert [r["name"] for r in values["rounds"]] == ["轮次 1", "轮次 2"]
    assert [a["kind"] for a in values["rounds"][1]["actions"]] == ["step", "disturbance"]
    assert "共 6 轮" in dialog.summary.text()

    dialog.round_list.setCurrentRow(1)
    dialog._add_action("step")
    assert [a["kind"] for a in dialog.values()["rounds"][1]["actions"]] == [
        "step",
        "disturbance",
        "step",
    ]
    dialog.action_list.setCurrentRow(2)
    dialog._move_action(-1)
    assert [a["kind"] for a in dialog.values()["rounds"][1]["actions"]] == [
        "step",
        "step",
        "disturbance",
    ]
    dialog._remove_action()
    assert [a["kind"] for a in dialog.values()["rounds"][1]["actions"]] == [
        "step",
        "disturbance",
    ]

    dialog._add_round()
    assert len(dialog.values()["rounds"]) == 3
    dialog.round_list.setCurrentRow(2)
    dialog._move_round(-1)
    assert [r["name"] for r in dialog.values()["rounds"]][1:] == ["轮次 3", "轮次 2"]
    dialog._remove_round()
    assert len(dialog.values()["rounds"]) == 2
    dialog.close()


def test_autotune_plan_dialog_param_editing(qapp):
    dialog = AutotunePlanDialog(
        {"cycles": 1, "rounds": [{"name": "轮次 1", "actions": [{"kind": "step"}]}]}
    )
    dialog.action_list.setCurrentRow(0)
    spins = [
        dialog.params_form.itemAt(row, QtWidgets.QFormLayout.ItemRole.FieldRole).widget()
        for row in range(dialog.params_form.rowCount())
    ]
    assert len(spins) == 5
    spins[1].setValue(300.0)
    action = dialog.values()["rounds"][0]["actions"][0]
    assert action["target_sp"] == 300.0

    dialog._add_action("disturbance")
    dialog.action_list.setCurrentRow(1)
    assert dialog.params_form.rowCount() == 5
    assert "5" in dialog.action_list.item(1).text()
    dialog.close()


def test_apply_plan_settings_persists(qapp, tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    window = MainWindow(app_config=config)
    window._apply_plan_settings(
        {
            "cycles": 2,
            "rounds": [
                {"name": "轮次 1", "actions": [{"kind": "step"}]},
                {"name": "轮次 2", "actions": [{"kind": "disturbance"}]},
            ],
        }
    )
    auto = config.data["autotune"]
    assert auto["cycles"] == 2
    assert [r["name"] for r in auto["rounds"]] == ["轮次 1", "轮次 2"]
    assert "4 轮" in window.ai_plan_summary.text()
    assert "扰动×1" in window.ai_plan_summary.text()
    window.close()


def test_autotune_plan_dialog_empty_rounds_get_default(qapp):
    dialog = AutotunePlanDialog({"rounds": []})
    values = dialog.values()
    assert len(values["rounds"]) == 1
    assert values["rounds"][0]["actions"][0]["kind"] == "step"
    assert "共 1 轮" in dialog.summary.text()
    dialog.close()


def test_autotune_plan_dialog_keeps_min_round_and_action(qapp):
    dialog = AutotunePlanDialog(
        {"cycles": 1, "rounds": [{"name": "轮次 1", "actions": [{"kind": "step"}]}]}
    )
    dialog._remove_round()
    assert len(dialog.values()["rounds"]) == 1
    dialog._remove_action()
    assert len(dialog.values()["rounds"][0]["actions"]) == 1
    dialog.close()


def test_autotune_plan_dialog_move_without_selection_is_noop(qapp):
    dialog = AutotunePlanDialog(
        {
            "cycles": 1,
            "rounds": [
                {"name": "A", "actions": [{"kind": "step"}]},
                {"name": "B", "actions": [{"kind": "disturbance"}]},
            ],
        }
    )
    dialog.round_list.setCurrentRow(-1)
    dialog._move_round(1)
    assert [r["name"] for r in dialog.values()["rounds"]] == ["A", "B"]
    dialog.round_list.setCurrentRow(1)
    dialog._move_round(1)
    assert [r["name"] for r in dialog.values()["rounds"]] == ["A", "B"]
    dialog.action_list.setCurrentRow(-1)
    dialog._move_action(1)
    assert len(dialog.values()["rounds"][1]["actions"]) == 1
    dialog.close()
