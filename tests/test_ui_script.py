from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets

from pidtool.app_config import AppConfig
from pidtool.core.experiments import disturbance_script, step_response_script
from pidtool.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    yield app


def _wait_until(qapp, condition, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture()
def window(qapp, tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    win = MainWindow(app_config=config)
    win._activate_workspace(tmp_path)
    win.link_combo.setCurrentIndex(win.link_combo.findData("mock"))
    win._connect()
    assert win.link is not None
    assert _wait_until(qapp, lambda: win.link.buffer.total >= 60, timeout=5.0)
    yield win
    win._disconnect()
    win.close()


def test_step_script_flow(qapp, window):
    window.sp_spin.setValue(2.0)
    window._run_script(step_response_script(2.0, settle_s=0.2, countdown_s=0.3, measure_s=0.8))
    assert window.runner.active
    assert _wait_until(qapp, lambda: not window.runner.active, timeout=8.0)
    assert window.banner.isHidden()
    assert window.capture_btn.isEnabled()
    assert len(window.plots.markers) >= 1
    trials = window.workspace.read_trials()
    assert trials[-1]["kind"] == "script_step"
    assert trials[-1]["script"] == "阶跃响应"
    assert window.metrics_table.item(0, 1).text() != "—"
    assert window.metrics_table.item(0, 0).text() == "上升时间 (10-90%)"


def test_disturbance_script_manual_prompt(qapp, window):
    window._run_script(
        disturbance_script(disturb_s=0.4, settle_s=0.2, countdown_s=0.3, recover_s=0.5)
    )
    deadline = time.time() + 10.0
    while window.runner.active and time.time() < deadline:
        qapp.processEvents()
        if not window.banner.isHidden() and not window.banner_confirm.isHidden():
            window.runner.confirm()
        time.sleep(0.01)
    assert not window.runner.active
    assert window.banner.isHidden()
    trials = window.workspace.read_trials()
    assert trials[-1]["kind"] == "script_disturbance"
    labels = [
        window.metrics_table.item(i, 0).text() for i in range(window.metrics_table.rowCount())
    ]
    assert "最大偏差" in labels
    assert "恢复时间" in labels
    assert len(window.plots.markers) == 2
