from __future__ import annotations

import copy
import sys
from pathlib import Path

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets
from serial.tools import list_ports

from pidtool.app_config import AppConfig
from pidtool.core.experiments import (
    ExperimentRunner,
    ExperimentScript,
    disturbance_script,
    step_response_script,
)
from pidtool.core.experiments.specs import MARK_DISTURB_END, MARK_DISTURB_START
from pidtool.core.metrics import StepMetrics, analyze_disturbance, analyze_step
from pidtool.link.mock_link import MockLink
from pidtool.link.protocol import MODE_IDLE, MODE_RUN, load_adapter
from pidtool.link.serial_link import LinkBase, SerialLink
from pidtool.ui.plots import PlotPanel
from pidtool.workspace import DEFAULT_SETTINGS, Workspace, ensure_gitignore

BAUD_RATES = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
LINK_TYPES = [("mock", "模拟器"), ("serial", "串口")]
METRIC_ROWS = [
    "上升时间 (10-90%)",
    "峰值时间",
    "超调量",
    "调节时间 (±2%)",
    "稳态误差",
    "尾波波动幅值",
    "持续振荡",
    "样本数",
]


def _fmt_sec(value: float) -> str:
    if value != value:
        return "—"
    return f"{value:.4f} s"


def _json_number(value: float) -> float | None:
    if value != value:
        return None
    return float(value)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, app_config: AppConfig | None = None) -> None:
        super().__init__()
        self.setWindowTitle("PIDtool - STM32 PID 调参工作台")
        self.resize(1280, 800)
        self.app_config = app_config or AppConfig()
        self.workspace: Workspace | None = None
        self.link: LinkBase | None = None
        self.safety = copy.deepcopy(DEFAULT_SETTINGS["safety"])
        self._capture_t0: float | None = None
        self._capture_sp = 1.0
        self._capturing = False
        self.runner = ExperimentRunner(self._on_experiment_event)
        self._script_marks: list[tuple[float, str]] = []
        self._script_kind = ""
        self._prompt_text = ""
        self._prompt_manual = False

        self._build_toolbar()
        self._build_body()
        self._build_statusbar()

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._load_recent_workspaces()
        last = self.app_config.data.get("last_workspace", "")
        if last and Path(last).is_dir():
            self._activate_workspace(Path(last))
        else:
            self._status("请选择固件工作目录（可先用“模拟器”链路体验）")
        self._refresh_ports()

    def _build_toolbar(self) -> None:
        tb = QtWidgets.QToolBar("主工具栏")
        tb.setMovable(False)
        self.addToolBar(tb)

        tb.addWidget(QtWidgets.QLabel(" 工作目录 "))
        self.workspace_combo = QtWidgets.QComboBox()
        self.workspace_combo.setMinimumWidth(280)
        self.workspace_combo.activated.connect(self._on_workspace_selected)
        tb.addWidget(self.workspace_combo)

        browse = QtGui.QAction("浏览…", self)
        browse.triggered.connect(self._browse_workspace)
        tb.addAction(browse)

        tb.addSeparator()
        tb.addWidget(QtWidgets.QLabel(" 链路 "))
        self.link_combo = QtWidgets.QComboBox()
        for key, label in LINK_TYPES:
            self.link_combo.addItem(label, key)
        tb.addWidget(self.link_combo)

        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setMinimumWidth(140)
        tb.addWidget(self.port_combo)

        refresh = QtGui.QAction("刷新", self)
        refresh.triggered.connect(self._refresh_ports)
        tb.addAction(refresh)

        self.baud_combo = QtWidgets.QComboBox()
        for baud in BAUD_RATES:
            self.baud_combo.addItem(str(baud), baud)
        self.baud_combo.setCurrentIndex(BAUD_RATES.index(115200))
        tb.addWidget(self.baud_combo)

        self.connect_btn = QtWidgets.QPushButton("连接")
        self.connect_btn.clicked.connect(self._toggle_connection)
        tb.addWidget(self.connect_btn)

    def _build_body(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        self.banner = QtWidgets.QFrame()
        self.banner.setObjectName("scriptBanner")
        self.banner.setStyleSheet(
            "QFrame#scriptBanner { background: #3b4a6b; border-radius: 6px; }"
            "QLabel#scriptBannerLabel { color: #ffffff; font-size: 15px; padding: 6px; }"
        )
        banner_row = QtWidgets.QHBoxLayout(self.banner)
        self.banner_label = QtWidgets.QLabel()
        self.banner_label.setObjectName("scriptBannerLabel")
        self.banner_confirm = QtWidgets.QPushButton("确认")
        self.banner_confirm.clicked.connect(self._on_banner_confirm)
        banner_row.addWidget(self.banner_label, 1)
        banner_row.addWidget(self.banner_confirm)
        self.banner.hide()
        self.banner_confirm.hide()
        root.addWidget(self.banner)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        root.addWidget(splitter, 1)

        self.plots = PlotPanel()
        self.plots.clear_btn.clicked.connect(self.plots.clear)
        splitter.addWidget(self.plots)

        controls = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(controls)
        row.addWidget(self._build_gains_group())
        row.addWidget(self._build_experiment_group())
        row.addWidget(self._build_script_group())
        row.addWidget(self._build_metrics_group())
        splitter.addWidget(controls)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([600, 220])

    def _build_gains_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("PID 参数（在线下发）")
        form = QtWidgets.QFormLayout(group)
        self.kp_spin = self._gain_spin()
        self.ki_spin = self._gain_spin()
        self.kd_spin = self._gain_spin()
        form.addRow("Kp", self.kp_spin)
        form.addRow("Ki", self.ki_spin)
        form.addRow("Kd", self.kd_spin)
        row = QtWidgets.QHBoxLayout()
        apply_btn = QtWidgets.QPushButton("应用")
        apply_btn.clicked.connect(self._apply_gains)
        read_btn = QtWidgets.QPushButton("读取")
        read_btn.clicked.connect(self._read_gains)
        row.addWidget(apply_btn)
        row.addWidget(read_btn)
        form.addRow(row)
        return group

    def _gain_spin(self) -> QtWidgets.QDoubleSpinBox:
        spin = QtWidgets.QDoubleSpinBox()
        spin.setDecimals(4)
        spin.setRange(0.0, 100000.0)
        spin.setSingleStep(0.1)
        return spin

    def _build_experiment_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("实验控制")
        form = QtWidgets.QFormLayout(group)
        self.sp_spin = QtWidgets.QDoubleSpinBox()
        self.sp_spin.setDecimals(4)
        self.sp_spin.setRange(-100000.0, 100000.0)
        self.sp_spin.setSingleStep(0.1)
        self.sp_spin.setValue(1.0)
        form.addRow("设定值", self.sp_spin)

        sp_row = QtWidgets.QHBoxLayout()
        sp_btn = QtWidgets.QPushButton("下发目标值")
        sp_btn.clicked.connect(self._apply_setpoint)
        sp_row.addWidget(sp_btn)
        form.addRow(sp_row)

        mode_row = QtWidgets.QHBoxLayout()
        enable_btn = QtWidgets.QPushButton("使能")
        enable_btn.clicked.connect(lambda: self._set_mode(MODE_RUN))
        disable_btn = QtWidgets.QPushButton("停止")
        disable_btn.clicked.connect(lambda: self._set_mode(MODE_IDLE))
        mode_row.addWidget(enable_btn)
        mode_row.addWidget(disable_btn)
        form.addRow(mode_row)

        telemetry_row = QtWidgets.QHBoxLayout()
        telemetry_btn = QtWidgets.QPushButton("开始遥测")
        telemetry_btn.clicked.connect(self._start_telemetry)
        telemetry_stop_btn = QtWidgets.QPushButton("停止遥测")
        telemetry_stop_btn.clicked.connect(self._stop_telemetry)
        telemetry_row.addWidget(telemetry_btn)
        telemetry_row.addWidget(telemetry_stop_btn)
        form.addRow(telemetry_row)
        return group

    def _build_metrics_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("实验指标")
        layout = QtWidgets.QVBoxLayout(group)
        row = QtWidgets.QHBoxLayout()
        self.capture_btn = QtWidgets.QPushButton("开始采集")
        self.capture_btn.clicked.connect(self._on_capture_clicked)
        calc_btn = QtWidgets.QPushButton("计算指标")
        calc_btn.clicked.connect(self._compute_metrics)
        row.addWidget(self.capture_btn)
        row.addWidget(calc_btn)
        layout.addLayout(row)

        self.metrics_table = QtWidgets.QTableWidget(0, 2)
        self.metrics_table.setHorizontalHeaderLabels(["指标", "值"])
        self.metrics_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.metrics_table.verticalHeader().setVisible(False)
        self.metrics_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.metrics_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self._set_metrics_rows([(name, "—") for name in METRIC_ROWS])
        self.metrics_table.setMinimumWidth(360)
        layout.addWidget(self.metrics_table)
        return group

    def _build_script_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("自动化实验")
        layout = QtWidgets.QVBoxLayout(group)
        row = QtWidgets.QHBoxLayout()
        self.step_exp_btn = QtWidgets.QPushButton("阶跃实验")
        self.step_exp_btn.clicked.connect(lambda: self._start_experiment("step"))
        self.disturb_exp_btn = QtWidgets.QPushButton("扰动实验")
        self.disturb_exp_btn.clicked.connect(lambda: self._start_experiment("disturbance"))
        self.abort_exp_btn = QtWidgets.QPushButton("中止")
        self.abort_exp_btn.setEnabled(False)
        self.abort_exp_btn.clicked.connect(self._abort_experiment)
        row.addWidget(self.step_exp_btn)
        row.addWidget(self.disturb_exp_btn)
        row.addWidget(self.abort_exp_btn)
        layout.addLayout(row)

        form = QtWidgets.QFormLayout()
        self.measure_spin = QtWidgets.QDoubleSpinBox()
        self.measure_spin.setRange(0.5, 120.0)
        self.measure_spin.setValue(4.0)
        self.measure_spin.setDecimals(1)
        self.measure_spin.setSuffix(" s")
        self.disturb_spin = QtWidgets.QDoubleSpinBox()
        self.disturb_spin.setRange(0.5, 120.0)
        self.disturb_spin.setValue(5.0)
        self.disturb_spin.setDecimals(1)
        self.disturb_spin.setSuffix(" s")
        form.addRow("观测时长", self.measure_spin)
        form.addRow("扰动时长", self.disturb_spin)
        layout.addLayout(form)
        layout.addStretch(1)
        return group

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.lbl_state = QtWidgets.QLabel("未连接")
        self.lbl_rate = QtWidgets.QLabel("采样率: -")
        self.lbl_rx = QtWidgets.QLabel("RX 0 B / 帧 0 / CRC错 0")
        sb.addPermanentWidget(self.lbl_state)
        sb.addPermanentWidget(self.lbl_rate)
        sb.addPermanentWidget(self.lbl_rx)

    def _status(self, message: str, timeout: int = 5000) -> None:
        self.statusBar().showMessage(message, timeout)

    def _refresh_ports(self) -> None:
        current = self.port_combo.currentData()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        for port in list_ports.comports():
            label = port.device
            if port.description and port.description not in ("n/a", ""):
                label = f"{port.device} ({port.description})"
            self.port_combo.addItem(label, port.device)
        if current:
            idx = self.port_combo.findData(current)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
        self.port_combo.blockSignals(False)

    def _load_recent_workspaces(self) -> None:
        self.workspace_combo.blockSignals(True)
        self.workspace_combo.clear()
        for path in self.app_config.data.get("recent_workspaces", []):
            self.workspace_combo.addItem(path, path)
        self.workspace_combo.blockSignals(False)

    def _set_workspace_combo(self, path: str) -> None:
        self.workspace_combo.blockSignals(True)
        idx = self.workspace_combo.findData(path)
        if idx < 0:
            self.workspace_combo.insertItem(0, path, path)
            idx = 0
        self.workspace_combo.setCurrentIndex(idx)
        self.workspace_combo.blockSignals(False)

    def _browse_workspace(self) -> None:
        start = str(self.workspace.root) if self.workspace else ""
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择固件工作目录", start)
        if path:
            self._activate_workspace(Path(path), ask_gitignore=True)

    def _on_workspace_selected(self, index: int) -> None:
        path = self.workspace_combo.itemData(index)
        if not path:
            return
        if not Path(path).is_dir():
            QtWidgets.QMessageBox.warning(self, "工作目录不存在", f"目录不存在或不可访问:\n{path}")
            return
        self._activate_workspace(Path(path))

    def _activate_workspace(self, root: Path, ask_gitignore: bool = False) -> None:
        self._save_settings_from_ui()
        self._disconnect()
        workspace = Workspace(root)
        created = workspace.ensure()
        if created and ask_gitignore:
            answer = QtWidgets.QMessageBox.question(
                self,
                "创建工作目录配置",
                f"已在 {root} 创建 .pidtool/ 配置目录。\n是否将 .pidtool/ 追加到 .gitignore？",
            )
            if answer == QtWidgets.QMessageBox.StandardButton.Yes:
                ensure_gitignore(root)
        self.workspace = workspace
        self.app_config.add_recent(str(root))
        self._load_recent_workspaces()
        self._set_workspace_combo(str(root))
        settings = workspace.load_settings()
        self._apply_settings_to_ui(settings)
        self._status(f"已加载工作目录 {root}")

    def _apply_settings_to_ui(self, settings: dict) -> None:
        self.safety = copy.deepcopy(settings.get("safety", DEFAULT_SETTINGS["safety"]))
        gains = settings.get("gains", {})
        self._set_gain_spins(
            float(gains.get("kp", 0.0)),
            float(gains.get("ki", 0.0)),
            float(gains.get("kd", 0.0)),
        )
        self.sp_spin.setValue(float(settings.get("setpoint", {}).get("value", 1.0)))
        link_type = settings.get("link", {}).get("type", "mock")
        idx = self.link_combo.findData(link_type)
        if idx >= 0:
            self.link_combo.setCurrentIndex(idx)
        serial_cfg = settings.get("serial", {})
        port = serial_cfg.get("port", "")
        if port:
            idx = self.port_combo.findData(port)
            if idx < 0:
                self.port_combo.addItem(port, port)
                idx = self.port_combo.findData(port)
            self.port_combo.setCurrentIndex(idx)
        baud = int(serial_cfg.get("baud", 115200))
        idx = self.baud_combo.findData(baud)
        if idx < 0:
            self.baud_combo.addItem(str(baud), baud)
            idx = self.baud_combo.findData(baud)
        self.baud_combo.setCurrentIndex(idx)

    def _save_settings_from_ui(self) -> None:
        if self.workspace is None:
            return
        settings = self.workspace.load_settings()
        settings["link"]["type"] = self.link_combo.currentData()
        settings["serial"]["port"] = self.port_combo.currentData() or ""
        settings["serial"]["baud"] = int(self.baud_combo.currentData() or 115200)
        settings["gains"] = {
            "kp": self.kp_spin.value(),
            "ki": self.ki_spin.value(),
            "kd": self.kd_spin.value(),
        }
        settings["setpoint"] = {"value": self.sp_spin.value()}
        self.workspace.save_settings(settings)

    def _set_gain_spins(self, kp: float, ki: float, kd: float) -> None:
        for spin, value in ((self.kp_spin, kp), (self.ki_spin, ki), (self.kd_spin, kd)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _clamp_gains(self, kp: float, ki: float, kd: float) -> tuple[float, float, float]:
        limits = {
            "kp": self.safety.get("kp", {}),
            "ki": self.safety.get("ki", {}),
            "kd": self.safety.get("kd", {}),
        }
        out = []
        for value, key in ((kp, "kp"), (ki, "ki"), (kd, "kd")):
            low = float(limits[key].get("min", float("-inf")))
            high = float(limits[key].get("max", float("inf")))
            out.append(max(low, min(high, value)))
        return out[0], out[1], out[2]

    def _ensure_link(self, silent: bool = False) -> bool:
        if self.link is not None and self.link.is_open:
            return True
        if not silent:
            QtWidgets.QMessageBox.information(self, "未连接", "请先连接链路（可用“模拟器”）。")
        return False

    def _toggle_connection(self) -> None:
        if self.link is not None and self.link.is_open:
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        adapter_spec = "default"
        if self.workspace is not None:
            adapter_spec = self.workspace.load_settings()["serial"].get("adapter", "default")
        try:
            adapter = load_adapter(adapter_spec)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "适配器加载失败", str(exc))
            return
        link_type = self.link_combo.currentData()
        if link_type == "serial":
            port = self.port_combo.currentData() or self.port_combo.currentText()
            if not port:
                QtWidgets.QMessageBox.information(self, "无串口", "未选择串口，请刷新端口列表后选择。")
                return
            try:
                link: LinkBase = SerialLink(port, int(self.baud_combo.currentData()), adapter)
                link.open()
            except Exception as exc:
                QtWidgets.QMessageBox.critical(self, "连接失败", str(exc))
                return
        else:
            link = MockLink(adapter)
            link.open()
            if self.workspace is not None:
                period = int(self.workspace.load_settings()["telemetry"].get("period_ms", 5))
                link.telemetry_period_ms = period
        self.link = link
        self.connect_btn.setText("断开")
        self._set_connected_ui(True)
        self.lbl_state.setText(f"已连接 ({self.link_combo.currentText()})")
        if link_type == "mock":
            self._apply_gains(silent=True)
            self._apply_setpoint(silent=True)
        self._status(f"已连接 {self.link_combo.currentText()}")

    def _disconnect(self) -> None:
        if self.runner.active:
            self.runner.abort()
        self.banner.hide()
        self.banner_confirm.hide()
        if self.link is not None:
            try:
                self.link.close()
            except Exception:
                pass
            self.link = None
        self.connect_btn.setText("连接")
        self._set_connected_ui(False)
        self.lbl_state.setText("未连接")
        self.lbl_rate.setText("采样率: -")
        self._capture_t0 = None
        self._capturing = False
        self.capture_btn.setText("开始采集")

    def _set_connected_ui(self, connected: bool) -> None:
        for widget in (self.workspace_combo, self.link_combo, self.port_combo, self.baud_combo):
            widget.setEnabled(not connected)

    def _apply_gains(self, checked: bool = False, silent: bool = False) -> None:
        if not self._ensure_link(silent=silent):
            return
        assert self.link is not None
        kp, ki, kd = self.kp_spin.value(), self.ki_spin.value(), self.kd_spin.value()
        kp2, ki2, kd2 = self._clamp_gains(kp, ki, kd)
        if (kp2, ki2, kd2) != (kp, ki, kd):
            self._set_gain_spins(kp2, ki2, kd2)
            if not silent:
                self._status("参数超出安全限幅，已钳制后下发")
        try:
            self.link.send(self.link.adapter.encode_set_gains(kp2, ki2, kd2))
        except Exception as exc:
            self._status(f"发送失败: {exc}")
            return
        self._save_settings_from_ui()
        if not silent:
            self._status(f"已下发 Kp={kp2:g} Ki={ki2:g} Kd={kd2:g}")

    def _read_gains(self, checked: bool = False) -> None:
        if not self._ensure_link():
            return
        assert self.link is not None
        try:
            self.link.send(self.link.adapter.encode_get_gains())
        except Exception as exc:
            self._status(f"发送失败: {exc}")

    def _apply_setpoint(self, checked: bool = False, silent: bool = False) -> None:
        if not self._ensure_link(silent=silent):
            return
        assert self.link is not None
        value = self.sp_spin.value()
        try:
            self.link.send(self.link.adapter.encode_set_setpoint(value))
        except Exception as exc:
            self._status(f"发送失败: {exc}")
            return
        if self._capturing:
            self._capture_sp = value
        elif not silent and self.link.buffer.total > 0:
            self._start_capture()
        self._save_settings_from_ui()
        if not silent:
            self._status(f"已下发目标值 {value:g}")

    def _set_mode(self, mode: int) -> None:
        if not self._ensure_link():
            return
        assert self.link is not None
        try:
            self.link.send(self.link.adapter.encode_set_mode(mode))
        except Exception as exc:
            self._status(f"发送失败: {exc}")
            return
        self._status("已使能" if mode == MODE_RUN else "已停止")

    def _start_telemetry(self, checked: bool = False) -> None:
        if not self._ensure_link():
            return
        assert self.link is not None
        period = 5
        if self.workspace is not None:
            period = int(self.workspace.load_settings()["telemetry"].get("period_ms", 5))
        try:
            self.link.send(self.link.adapter.encode_start_telemetry(period))
        except Exception as exc:
            self._status(f"发送失败: {exc}")
            return
        self._status(f"已请求遥测（周期 {period} ms）")

    def _stop_telemetry(self, checked: bool = False) -> None:
        if not self._ensure_link():
            return
        assert self.link is not None
        try:
            self.link.send(self.link.adapter.encode_stop_telemetry())
        except Exception as exc:
            self._status(f"发送失败: {exc}")
            return
        self._status("已停止遥测")

    def _latest_t(self) -> float | None:
        if self.link is None:
            return None
        t = self.link.buffer.tail(1)["t"]
        return float(t[-1]) if t.size else None

    def _on_capture_clicked(self) -> None:
        if self.runner.active:
            self._status("实验运行中，采集由实验脚本控制")
            return
        if not self._ensure_link():
            return
        if self._capturing:
            self._stop_capture()
            self._status("采集结束，可点击“计算指标”")
        else:
            self._start_capture()

    def _stop_capture(self) -> None:
        self._capturing = False
        self.capture_btn.setText("开始采集")

    def _start_capture(self, lookback: float = 0.5) -> None:
        if self.link is None:
            return
        t_min, t_max = self.link.buffer.span()
        if t_max <= 0.0:
            self._status("暂无数据，请确认遥测已开启")
            return
        self._capture_t0 = max(t_min, t_max - lookback)
        self._capture_sp = self.sp_spin.value()
        self._capturing = True
        self.capture_btn.setText("结束采集")
        self._status(f"开始采集（目标 {self._capture_sp:g}，含 {lookback:g}s 基准段）")

    def _compute_metrics(self) -> None:
        if self.link is None or self._capture_t0 is None:
            QtWidgets.QMessageBox.information(self, "无法计算", "请先连接链路并开始采集。")
            return
        data = self.link.buffer.tail(400000)
        t = data["t"]
        if t.size < 50:
            self._status("数据不足，无法计算指标")
            return
        mask = t >= self._capture_t0 - 0.05
        tt = t[mask]
        if tt.size < 50:
            self._status("采集窗口内数据不足，无法计算指标")
            return
        yy = data["feedback"][mask].astype(float)
        metrics = analyze_step(tt, yy, self._capture_sp)
        if metrics is None:
            self._status("采集窗口无效（目标值与初始值过近）")
            return
        self._set_metrics_rows(self._step_metric_rows(metrics))
        self._status("指标计算完成")
        if self.workspace is not None:
            self.workspace.append_trial(
                {
                    "kind": "manual_step",
                    "setpoint": float(self._capture_sp),
                    "gains": self._gains_dict(),
                    "metrics": self._step_metrics_dict(metrics),
                    "capture_s": float(t[-1] - self._capture_t0),
                }
            )

    def _start_experiment(self, kind: str) -> None:
        if not self._ensure_link():
            return
        if self.runner.active:
            self._status("已有实验在运行")
            return
        if kind == "step":
            script = step_response_script(
                self.sp_spin.value(), measure_s=self.measure_spin.value()
            )
        else:
            script = disturbance_script(disturb_s=self.disturb_spin.value())
        self._run_script(script)

    def _run_script(self, script: ExperimentScript) -> None:
        self.plots.clear_markers()
        self._script_marks = []
        self._script_kind = script.name
        self._set_script_ui(True)
        self.runner.start(script)

    def _abort_experiment(self) -> None:
        if self.runner.active:
            self.runner.abort()
        else:
            self._status("当前没有运行中的实验")

    def _set_script_ui(self, active: bool) -> None:
        self.step_exp_btn.setEnabled(not active)
        self.disturb_exp_btn.setEnabled(not active)
        self.abort_exp_btn.setEnabled(active)
        self.capture_btn.setEnabled(not active)

    def _on_banner_confirm(self) -> None:
        self.runner.confirm()

    def _gains_dict(self) -> dict[str, float]:
        return {
            "kp": self.kp_spin.value(),
            "ki": self.ki_spin.value(),
            "kd": self.kd_spin.value(),
        }

    def _on_experiment_event(self, kind: str, data: dict) -> None:
        if kind == "started":
            self._status(f"实验开始: {data.get('name', '')}")
        elif kind == "wait":
            self._status(f"等待 {float(data.get('seconds', 0.0)):.1f} s")
        elif kind == "prompt":
            self._prompt_text = str(data.get("text", ""))
            self._prompt_manual = bool(data.get("manual", False))
            self.banner_label.setText(self._prompt_text)
            self.banner_confirm.setVisible(self._prompt_manual)
            self.banner.show()
        elif kind == "prompt_tick":
            if not self._prompt_manual:
                remaining = float(data.get("remaining", 0.0))
                self.banner_label.setText(f"{self._prompt_text}（{remaining:.1f} s）")
        elif kind == "prompt_done":
            self.banner.hide()
            self.banner_confirm.hide()
        elif kind == "mark":
            t = self._latest_t()
            label = str(data.get("label", ""))
            if t is not None:
                self.plots.add_marker(t, label)
                self._script_marks.append((t, label))
            self._status(f"标记: {label}")
        elif kind == "command":
            self._run_script_command(data)
        elif kind == "collect_start":
            self._start_capture(lookback=float(data.get("lookback", 0.5)))
        elif kind == "collect_end":
            self._finish_collect(str(data.get("kind", "step")))
        elif kind == "finished":
            self._status("实验完成")
            self._set_script_ui(False)
        elif kind == "aborted":
            self.banner.hide()
            self.banner_confirm.hide()
            self._stop_capture()
            self._status("实验已中止")
            self._set_script_ui(False)

    def _run_script_command(self, data: dict) -> None:
        cmd = data.get("cmd")
        if cmd == "mode":
            mode = MODE_RUN if data.get("mode", "run") == "run" else MODE_IDLE
            self._set_mode(mode)
        elif cmd == "setpoint":
            value = float(data.get("value", 0.0))
            self.sp_spin.blockSignals(True)
            self.sp_spin.setValue(value)
            self.sp_spin.blockSignals(False)
            self._apply_setpoint(silent=True)
        elif cmd == "gains":
            self._set_gain_spins(
                float(data.get("kp", 0.0)),
                float(data.get("ki", 0.0)),
                float(data.get("kd", 0.0)),
            )
            self._apply_gains(silent=True)

    def _finish_collect(self, kind: str) -> None:
        self._stop_capture()
        if self.link is None or self._capture_t0 is None:
            self._status("采集数据缺失，无法计算实验指标")
            return
        data = self.link.buffer.tail(400000)
        t = data["t"]
        if t.size < 50:
            self._status("数据不足，无法计算实验指标")
            return
        mask = t >= self._capture_t0 - 0.05
        tt = t[mask]
        yy = data["feedback"][mask].astype(float)
        if tt.size < 50:
            self._status("采集窗口内数据不足，无法计算实验指标")
            return
        if kind == "disturbance":
            marks = {label: mt for mt, label in self._script_marks}
            t_start = marks.get(MARK_DISTURB_START)
            t_end = marks.get(MARK_DISTURB_END)
            if t_start is None or t_end is None or t_end <= t_start:
                self._status("缺少扰动区间标记，无法计算扰动指标")
                return
            band = max(0.02 * abs(self._capture_sp), 0.01)
            m = analyze_disturbance(tt, yy, self._capture_sp, t_start, t_end, band)
            if m is None:
                self._status("扰动指标计算失败（窗口无效）")
                return
            recovery = "—" if m.recovery_time != m.recovery_time else f"{m.recovery_time:.3f} s"
            rows = [
                ("最大偏差", f"{m.max_deviation:.5f}"),
                ("偏差峰值时刻", f"{m.max_deviation_time - float(tt[0]):.3f} s"),
                ("恢复时间", recovery),
                ("残余误差", f"{m.residual_error:+.5f}"),
                ("样本数", str(m.samples)),
            ]
            trial = {
                "kind": "script_disturbance",
                "script": self._script_kind,
                "setpoint": float(self._capture_sp),
                "gains": self._gains_dict(),
                "metrics": {
                    "max_deviation": m.max_deviation,
                    "max_deviation_time": _json_number(m.max_deviation_time),
                    "recovery_time": _json_number(m.recovery_time),
                    "residual_error": m.residual_error,
                    "samples": m.samples,
                },
                "marks": [{"t": mt, "label": label} for mt, label in self._script_marks],
            }
        else:
            m = analyze_step(tt, yy, self._capture_sp)
            if m is None:
                self._status("阶跃窗口无效（目标值与初始值过近）")
                return
            rows = self._step_metric_rows(m)
            trial = {
                "kind": "script_step",
                "script": self._script_kind,
                "setpoint": float(self._capture_sp),
                "gains": self._gains_dict(),
                "metrics": self._step_metrics_dict(m),
                "marks": [{"t": mt, "label": label} for mt, label in self._script_marks],
            }
        self._set_metrics_rows(rows)
        self._status("实验指标已计算")
        if self.workspace is not None:
            self.workspace.append_trial(trial)

    @staticmethod
    def _step_metric_rows(m: StepMetrics) -> list[tuple[str, str]]:
        return [
            ("上升时间 (10-90%)", _fmt_sec(m.rise_time)),
            ("峰值时间", _fmt_sec(m.peak_time)),
            ("超调量", f"{m.overshoot_pct:.2f} %"),
            ("调节时间 (±2%)", _fmt_sec(m.settling_time)),
            ("稳态误差", f"{m.steady_error:+.5f}"),
            ("尾波波动幅值", f"{m.tail_ripple:.5f}"),
            ("持续振荡", "是" if m.oscillating else "否"),
            ("样本数", str(m.samples)),
        ]

    @staticmethod
    def _step_metrics_dict(m: StepMetrics) -> dict:
        return {
            "rise_time": _json_number(m.rise_time),
            "peak_time": _json_number(m.peak_time),
            "overshoot_pct": m.overshoot_pct,
            "settling_time": _json_number(m.settling_time),
            "steady_error": m.steady_error,
            "tail_ripple": m.tail_ripple,
            "oscillating": m.oscillating,
            "samples": m.samples,
        }

    def _set_metrics_rows(self, rows: list[tuple[str, str]]) -> None:
        self.metrics_table.setRowCount(len(rows))
        for i, (name, value) in enumerate(rows):
            self.metrics_table.setItem(i, 0, QtWidgets.QTableWidgetItem(name))
            self.metrics_table.setItem(i, 1, QtWidgets.QTableWidgetItem(value))

    def _tick(self) -> None:
        self.runner.tick()
        link = self.link
        if link is None:
            return
        if not link.is_open:
            error = link.stats.last_error or "未知错误"
            self._disconnect()
            self._status(f"链路已断开: {error}")
            return
        data = link.buffer.tail(250000)
        t = data["t"]
        if t.size:
            self.plots.update_data(data, float(t[-1]))
        for event in link.drain_events():
            if event.kind == "gains":
                self._set_gain_spins(event.data["kp"], event.data["ki"], event.data["kd"])
                self._status("已读取设备参数")
            elif event.kind == "ack":
                if event.data.get("status", 0) != 0:
                    self._status(f"设备返回错误 ACK: 0x{event.data.get('acked', 0):02X}")
            elif event.kind == "text":
                self._status(str(event.data.get("text", "")))
        rate = link.estimate_rate(2.0)
        self.lbl_rate.setText(f"采样率: {rate:.0f} Hz")
        stats = link.stats
        self.lbl_rx.setText(
            f"RX {stats.rx_bytes} B / 帧 {stats.frames} / CRC错 {stats.crc_errors} / 杂散 {stats.garbage_bytes} B"
        )
        self.lbl_state.setText(f"已连接 ({self.link_combo.currentText()})")

    def closeEvent(self, event) -> None:
        self._save_settings_from_ui()
        self._disconnect()
        self.app_config.save()
        super().closeEvent(event)


def main() -> None:
    pg.setConfigOptions(antialias=True)
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    exit_code = app.exec()
    window.close()
    del window
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
