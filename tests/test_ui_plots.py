from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6 import QtWidgets

from pidtool.ui.plots import CURVE_LABELS, PlotPanel


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    yield app


def _data(points=500):
    t = np.arange(points, dtype=np.float64) * 0.001 + 100.0
    y = (1.0 + 0.01 * np.sin(t * 10.0)).astype(np.float32)
    return {
        "t": t,
        "setpoint": y + 1.0,
        "feedback": y,
        "u": y * 2.0,
        "up": y,
        "ui": y * 0.5,
        "ud": y * 0.1,
    }


def test_two_plots_and_no_error_curve(qapp):
    panel = PlotPanel()
    assert "error" not in panel.curves
    assert set(panel.curves) == {key for key, _ in CURVE_LABELS}
    assert len(panel.glw.ci.items) == 2


def test_update_data_sets_all_curves(qapp):
    panel = PlotPanel()
    data = _data()
    panel.update_data(data, float(data["t"][-1]))
    for key in panel.curves:
        xs, ys = panel.curves[key].getData()
        assert xs is not None and xs.size > 0
        assert ys is not None and ys.size == xs.size


def test_curve_checkboxes_toggle_visibility(qapp):
    panel = PlotPanel()
    panel.curve_checks["ui"].setChecked(False)
    assert not panel.curves["ui"].isVisible()
    panel.curve_checks["ui"].setChecked(True)
    assert panel.curves["ui"].isVisible()
    panel.curve_checks["feedback"].setChecked(False)
    assert not panel.curves["feedback"].isVisible()


def test_markers_attach_to_two_plots_and_clear(qapp):
    panel = PlotPanel()
    panel.add_marker(100.5, "阶跃起点")
    assert len(panel.markers) == 1
    _, items = panel.markers[0]
    assert len(items) == 2
    panel.clear()
    assert panel.markers == []
