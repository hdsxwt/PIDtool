from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets

from pidtool.ai.llm import Suggestion
from pidtool.app_config import AppConfig
from pidtool.link.protocol import MODE_IDLE
from pidtool.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    yield app


def _wait_until(qapp, condition, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def _fast_step_round(name: str, target: float = 2.0) -> dict:
    return {
        "name": name,
        "actions": [
            {
                "kind": "step",
                "baseline_sp": 0.0,
                "target_sp": target,
                "settle_s": 0.2,
                "countdown_s": 0.2,
                "measure_s": 0.6,
            }
        ],
    }


def test_autotune_ui_flow(qapp, tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    window = MainWindow(app_config=config)
    window._activate_workspace(tmp_path)
    window.link_combo.setCurrentIndex(window.link_combo.findData("mock"))
    window._connect()
    assert _wait_until(qapp, lambda: window.link.buffer.total >= 60, timeout=10.0)

    window.app_config.data["autotune"] = {
        "cycles": 1,
        "rounds": [_fast_step_round("轮次 1"), _fast_step_round("轮次 2")],
    }
    window.ai_wave_check.setChecked(True)
    window.app_config.data["llm"]["api_key"] = "test-key"

    calls: list[int] = []
    captured: list[dict] = []

    def fake_suggest(history, limits):
        calls.append(len(history))
        captured.append(dict(history[-1]))
        return Suggestion(5.0, 40.0, 0.1, "fake suggestion")

    observed_modes: list[int] = []
    observed_labels: list[str] = []
    original_handler = window._on_tuner_event

    def spy(kind, data):
        original_handler(kind, data)
        if kind == "pause_motor":
            if window.link is not None:
                observed_modes.append(window.link.mode)
            observed_labels.append(window.ai_current_label.text())

    window.tuner._on_event = spy
    window._make_suggest = lambda: fake_suggest
    window._start_autotune()
    assert window.tuner.active
    assert not window.ai_start_btn.isEnabled()
    assert window.ai_abort_btn.isEnabled()
    assert "第 1/2 轮" in window.ai_current_label.text()
    assert "阶跃" in window.ai_current_label.text()
    assert "Kp=4" in window.ai_current_label.text()

    assert _wait_until(qapp, lambda: not window.tuner.active, timeout=60.0)
    assert window.ai_log.rowCount() == 2
    assert calls == [1]
    waveform = captured[0]["actions"][0].get("waveform")
    assert waveform
    assert 10 <= len(waveform["t"]) <= 90
    assert len(waveform["feedback"]) == len(waveform["t"])
    assert window.ai_start_btn.isEnabled()
    assert not window.ai_abort_btn.isEnabled()
    assert "共 2 轮" in window.ai_status.text()
    assert len(observed_modes) >= 2
    assert all(mode == MODE_IDLE for mode in observed_modes)
    assert any("等待 AI 建议" in text for text in observed_labels)
    assert "最优参数" in window.ai_current_label.text()
    assert window.link.mode == MODE_IDLE

    trials = [t for t in window.workspace.read_trials() if t.get("kind") == "script_step"]
    assert len(trials) == 2
    assert trials[0]["gains"]["kp"] == 4.0
    assert trials[1]["gains"]["kp"] == 5.0
    assert trials[0]["autotune"]["action_kind"] == "step"
    assert window.trials_table.rowCount() == 2

    window._disconnect()
    window.close()


def test_autotune_mixed_round_ui(qapp, tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    window = MainWindow(app_config=config)
    window._activate_workspace(tmp_path)
    window.link_combo.setCurrentIndex(window.link_combo.findData("mock"))
    window._connect()
    assert _wait_until(qapp, lambda: window.link.buffer.total >= 60, timeout=10.0)

    step_action = {
        "kind": "step",
        "baseline_sp": 0.0,
        "target_sp": 2.0,
        "settle_s": 0.2,
        "countdown_s": 0.2,
        "measure_s": 0.5,
    }
    disturb_action = {
        "kind": "disturbance",
        "target_sp": 2.0,
        "settle_s": 0.2,
        "countdown_s": 0.2,
        "disturb_s": 0.4,
        "recover_s": 0.5,
    }
    window.app_config.data["autotune"] = {
        "cycles": 2,
        "rounds": [{"name": "混合轮", "actions": [step_action, disturb_action]}],
    }
    window.app_config.data["llm"]["api_key"] = "test-key"

    calls: list[list[dict]] = []

    def fake_suggest(history, limits):
        calls.append(history)
        return Suggestion(5.0, 40.0, 0.1, "fake")

    window._make_suggest = lambda: fake_suggest
    window._start_autotune()
    assert window.tuner.active
    assert "第 1/2 轮" in window.ai_current_label.text()
    assert "阶跃/扰动" in window.ai_current_label.text()

    deadline = time.time() + 60.0
    banner_texts: list[str] = []
    while window.tuner.active and time.time() < deadline:
        qapp.processEvents()
        if not window.banner_confirm.isHidden():
            if not banner_texts:
                banner_texts.append(window.banner_label.text())
            window.runner.confirm()
        time.sleep(0.01)
    assert not window.tuner.active

    assert len(calls) == 1
    assert [a["kind"] for a in calls[0][0]["actions"]] == ["step", "disturbance"]

    trials = window.workspace.read_trials()
    kinds = [t["kind"] for t in trials]
    assert kinds.count("script_step") == 2
    assert kinds.count("script_disturbance") == 2
    assert trials[0]["autotune"]["action_index"] == 1
    assert trials[1]["autotune"]["action_index"] == 2
    assert trials[2]["autotune"]["round"] == 1

    plan_column = [
        window.ai_log.item(row, 1).text() for row in range(window.ai_log.rowCount())
    ]
    assert plan_column == ["阶跃", "扰动", "阶跃", "扰动"]
    assert window.ai_status.text().startswith("完成全部轮次")
    assert banner_texts and "当前测试参数" in banner_texts[0]
    assert window.link.mode == MODE_IDLE

    window._disconnect()
    window.close()


def test_export_current_from_history_tab(qapp, tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    window = MainWindow(app_config=config)
    window._activate_workspace(tmp_path)
    window._set_gain_spins(3.0, 30.0, 0.03)
    window._export_current()
    import json

    data = json.loads((window.workspace.exports_dir / "config.json").read_text(encoding="utf-8"))
    assert data["gains"] == {"kp": 3.0, "ki": 30.0, "kd": 0.03}
    assert (window.workspace.exports_dir / "pid_config.h").is_file()
    window.close()
