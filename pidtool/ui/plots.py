from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

COLORS = {
    "setpoint": "#9aa0a6",
    "feedback": "#4da3ff",
    "error": "#ff5c5c",
    "u": "#37c26f",
    "up": "#f2a03d",
    "ui": "#b07cff",
    "ud": "#39c6c0",
}

MAX_PLOT_POINTS = 4000


class PlotPanel(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.glw = pg.GraphicsLayoutWidget()
        self.plot_track = self.glw.addPlot(row=0, col=0, title="目标 / 反馈")
        self.plot_error = self.glw.addPlot(row=1, col=0, title="误差")
        self.plot_terms = self.glw.addPlot(row=2, col=0, title="输出 / PID 分量")
        self.plot_error.setXLink(self.plot_track)
        self.plot_terms.setXLink(self.plot_track)
        for plot in (self.plot_track, self.plot_error, self.plot_terms):
            plot.showGrid(x=True, y=True, alpha=0.25)
            plot.setDownsampling(auto=True, mode="peak")
            plot.setClipToView(True)
        self.plot_terms.setLabel("bottom", "t (s, 相对当前)")
        self.plot_track.addLegend(offset=(10, 10))
        self.plot_terms.addLegend(offset=(10, 10))

        self.curves = {
            "setpoint": self.plot_track.plot(
                pen=pg.mkPen(COLORS["setpoint"], width=1.4, style=QtCore.Qt.PenStyle.DashLine),
                name="目标",
            ),
            "feedback": self.plot_track.plot(pen=pg.mkPen(COLORS["feedback"], width=1.8), name="反馈"),
            "error": self.plot_error.plot(pen=pg.mkPen(COLORS["error"], width=1.5)),
            "u": self.plot_terms.plot(pen=pg.mkPen(COLORS["u"], width=1.5), name="u"),
            "up": self.plot_terms.plot(pen=pg.mkPen(COLORS["up"], width=1.1), name="up"),
            "ui": self.plot_terms.plot(pen=pg.mkPen(COLORS["ui"], width=1.1), name="ui"),
            "ud": self.plot_terms.plot(pen=pg.mkPen(COLORS["ud"], width=1.1), name="ud"),
        }

        self.window_spin = QtWidgets.QDoubleSpinBox()
        self.window_spin.setRange(1.0, 300.0)
        self.window_spin.setValue(10.0)
        self.window_spin.setDecimals(1)
        self.window_spin.setSuffix(" s")
        self.freeze_check = QtWidgets.QCheckBox("冻结显示")
        self.clear_btn = QtWidgets.QPushButton("清除")

        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("时间窗口"))
        bar.addWidget(self.window_spin)
        bar.addWidget(self.freeze_check)
        bar.addWidget(self.clear_btn)
        bar.addStretch(1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.glw, 1)
        layout.addLayout(bar)

        self._frozen_t: float | None = None
        self.markers: list[tuple[float, list[tuple[pg.PlotItem, pg.InfiniteLine]]]] = []

    def clear(self) -> None:
        for curve in self.curves.values():
            curve.setData([], [])
        self.clear_markers()
        self._frozen_t = None

    def add_marker(self, t: float, label: str) -> None:
        pen = pg.mkPen("#ffd166", width=1.2, style=QtCore.Qt.PenStyle.DashDotLine)
        items: list[tuple[pg.PlotItem, pg.InfiniteLine]] = []
        for plot in (self.plot_track, self.plot_error, self.plot_terms):
            line = pg.InfiniteLine(
                angle=90,
                movable=False,
                pen=pen,
                label=label if plot is self.plot_track else None,
                labelOpts={
                    "position": 0.08,
                    "color": "#ffd166",
                    "fill": "#3b4a6b",
                    "movable": False,
                },
            )
            plot.addItem(line, ignoreBounds=True)
            items.append((plot, line))
        self.markers.append((float(t), items))

    def clear_markers(self) -> None:
        for _, items in self.markers:
            for plot, line in items:
                plot.removeItem(line)
        self.markers.clear()

    def update_data(self, data: dict[str, np.ndarray], t_now: float) -> None:
        t = data["t"]
        if t.size == 0:
            return
        if self.freeze_check.isChecked():
            if self._frozen_t is None:
                self._frozen_t = float(t[-1])
            t_ref = self._frozen_t
        else:
            self._frozen_t = None
            t_ref = float(t_now)
        for mark_t, items in self.markers:
            for _, line in items:
                line.setValue(mark_t - t_ref)
        window = float(self.window_spin.value())
        idx = int(np.searchsorted(t, t_ref - window, side="left"))
        t = t[idx:]
        x = t - t_ref
        step = max(1, t.size // MAX_PLOT_POINTS)
        xs = x[::step]
        self.curves["setpoint"].setData(xs, data["setpoint"][idx::step])
        self.curves["feedback"].setData(xs, data["feedback"][idx::step])
        self.curves["error"].setData(xs, (data["setpoint"][idx:] - data["feedback"][idx:])[::step])
        self.curves["u"].setData(xs, data["u"][idx::step])
        self.curves["up"].setData(xs, data["up"][idx::step])
        self.curves["ui"].setData(xs, data["ui"][idx::step])
        self.curves["ud"].setData(xs, data["ud"][idx::step])
