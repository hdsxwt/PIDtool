from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets

from pidtool.ai.llm import Suggestion
from pidtool.app_config import AppConfig
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


def test_autotune_ui_flow(qapp, tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    window = MainWindow(app_config=config)
    window._activate_workspace(tmp_path)
    window.link_combo.setCurrentIndex(window.link_combo.findData("mock"))
    window._connect()
    assert _wait_until(qapp, lambda: window.link.buffer.total >= 60, timeout=10.0)

    window._ai_settle_s = 0.2
    window._ai_countdown_s = 0.2
    window.ai_measure_spin.setValue(0.6)
    window.ai_iters_spin.setValue(2)
    window.ai_target_spin.setValue(2.0)
    window.ai_wave_check.setChecked(True)
    window.app_config.data["llm"]["api_key"] = "test-key"

    calls: list[int] = []
    captured: list[dict] = []

    def fake_suggest(history, limits, setpoint):
        calls.append(len(history))
        captured.append(dict(history[-1]))
        assert setpoint == 2.0
        return Suggestion(5.0, 40.0, 0.1, "fake suggestion")

    window._make_suggest = lambda: fake_suggest
    window._start_autotune()
    assert window.tuner.active
    assert not window.ai_start_btn.isEnabled()
    assert window.ai_abort_btn.isEnabled()

    assert _wait_until(qapp, lambda: not window.tuner.active, timeout=60.0)
    assert window.ai_log.rowCount() == 2
    assert calls == [1]
    assert captured[0].get("waveform")
    waveform = captured[0]["waveform"]
    assert 10 <= len(waveform["t"]) <= 90
    assert len(waveform["feedback"]) == len(waveform["t"])
    assert window.ai_start_btn.isEnabled()
    assert not window.ai_abort_btn.isEnabled()
    assert "2 轮" in window.ai_status.text()

    trials = [t for t in window.workspace.read_trials() if t.get("kind") == "script_step"]
    assert len(trials) == 2
    assert trials[0]["gains"]["kp"] == 4.0
    assert trials[1]["gains"]["kp"] == 5.0
    assert window.trials_table.rowCount() == 2

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
