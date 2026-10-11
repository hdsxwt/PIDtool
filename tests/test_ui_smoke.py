from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets

from pidtool.app_config import AppConfig
from pidtool.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    yield app


def _wait_for(qapp, condition, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_mock_link_smoke(qapp, tmp_path):
    config = AppConfig(path=tmp_path / "app.toml")
    window = MainWindow(app_config=config)
    window._activate_workspace(tmp_path)
    assert window.workspace is not None
    window.link_combo.setCurrentIndex(window.link_combo.findData("mock"))
    window._connect()
    assert window.link is not None
    assert window.link.is_open

    assert _wait_for(qapp, lambda: window.link.buffer.total >= 150, timeout=5.0)

    window.sp_spin.setValue(2.0)
    window._apply_setpoint()
    assert window._capturing

    baseline = window.link.buffer.total
    assert _wait_for(
        qapp,
        lambda: window.link is not None and window.link.buffer.total - baseline >= 80,
        timeout=5.0,
    )

    window._compute_metrics()
    assert window.metrics_table.item(0, 1).text() != "—"
    trials = window.workspace.read_trials()
    assert len(trials) >= 1
    assert trials[-1]["kind"] == "manual_step"

    window._tick()
    xs, _ = window.plots.curves["feedback"].getData()
    assert xs is not None and xs.size > 0

    window._disconnect()
    assert window.link is None
    window.close()
