from __future__ import annotations

import copy
import os
import sys
from datetime import datetime
from pathlib import Path

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets
from serial.tools import list_ports

from pidtool.ai.llm import DeepSeekTuner
from pidtool.ai.tuner import AutoTuner
from pidtool.app_config import DEFAULT_LLM_MODEL, AppConfig
from pidtool.codeagent import CodeEditSession, OpencodeAgent, build_code_prompt
from pidtool.ui.dialogs import LLMSettingsDialog
from pidtool.core.experiments import (
    ExperimentRunner,
    ExperimentScript,
    autotune_step_script,
    disturbance_script,
    step_response_script,
)
from pidtool.core.experiments.specs import MARK_DISTURB_END, MARK_DISTURB_START, MARK_STEP
from pidtool.core.export import export_artifacts
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


def _fmt_num(value, spec: str) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number or number in (float("inf"), float("-inf")):
        return "—"
    return format(number, spec)


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
        self._last_plot_t: float | None = None
        self.runner = ExperimentRunner(self._on_experiment_event)
        self.tuner = AutoTuner(self._on_tuner_event)
        self.code_session = CodeEditSession()
        self._code_prompt = ""
        self._code_decided = False
        self._code_review_shown = False
        self._script_marks: list[tuple[float, str]] = []
        self._script_kind = ""
        self._prompt_text = ""
        self._prompt_manual = False
        self._last_metrics: dict | None = None
        self._trial_records: list[dict] = []
        self._ai_settle_s = 2.0
        self._ai_countdown_s = 3.0

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
        self.plots.clear_btn.clicked.connect(self._clear_plots)
        splitter.addWidget(self.plots)

        controls = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(controls)
        row.addWidget(self._build_gains_group())
        row.addWidget(self._build_experiment_group())
        row.addWidget(self._build_script_group())
        row.addWidget(self._build_metrics_group())

        self.bottom_tabs = QtWidgets.QTabWidget()
        self.bottom_tabs.addTab(controls, "实验")
        self.bottom_tabs.addTab(self._build_ai_tab(), "AI 调参")
        self.bottom_tabs.addTab(self._build_code_tab(), "代码修改")
        self.bottom_tabs.addTab(self._build_history_tab(), "历史")
        splitter.addWidget(self.bottom_tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([660, 180])

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
        self.metrics_table.setMaximumHeight(150)
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

    def _build_ai_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(tab)

        left = QtWidgets.QVBoxLayout()
        exp_group = QtWidgets.QGroupBox("自动调参")
        exp_form = QtWidgets.QFormLayout(exp_group)
        self.ai_target_spin = QtWidgets.QDoubleSpinBox()
        self.ai_target_spin.setDecimals(4)
        self.ai_target_spin.setRange(-100000.0, 100000.0)
        self.ai_target_spin.setValue(2.0)
        self.ai_iters_spin = QtWidgets.QSpinBox()
        self.ai_iters_spin.setRange(1, 50)
        self.ai_iters_spin.setValue(8)
        self.ai_measure_spin = QtWidgets.QDoubleSpinBox()
        self.ai_measure_spin.setRange(0.5, 120.0)
        self.ai_measure_spin.setValue(4.0)
        self.ai_measure_spin.setDecimals(1)
        self.ai_measure_spin.setSuffix(" s")
        self.ai_wave_check = QtWidgets.QCheckBox("发送降采样原始波形给 AI")
        self.ai_wave_check.setToolTip(
            "勾选后，每轮实验会附带约 80 点的降采样波形（t,sp,fb,u,up,ui,ud），"
            "供 AI 分析响应形状与输出饱和；会增大 token 消耗"
        )
        exp_form.addRow("目标设定值", self.ai_target_spin)
        exp_form.addRow("最大轮数", self.ai_iters_spin)
        exp_form.addRow("单轮观测时长", self.ai_measure_spin)
        exp_form.addRow(self.ai_wave_check)
        button_row = QtWidgets.QHBoxLayout()
        self.ai_start_btn = QtWidgets.QPushButton("开始自动调参")
        self.ai_start_btn.clicked.connect(self._start_autotune)
        self.ai_abort_btn = QtWidgets.QPushButton("中止")
        self.ai_abort_btn.setEnabled(False)
        self.ai_abort_btn.clicked.connect(self._abort_autotune)
        button_row.addWidget(self.ai_start_btn)
        button_row.addWidget(self.ai_abort_btn)
        exp_form.addRow(button_row)
        left.addWidget(exp_group)

        llm_row = QtWidgets.QHBoxLayout()
        self.llm_settings_btn = QtWidgets.QPushButton("DeepSeek 设置…")
        self.llm_settings_btn.clicked.connect(self._open_llm_settings_dialog)
        self.llm_summary = QtWidgets.QLabel(self._llm_summary_text())
        self.llm_summary.setStyleSheet("color: #888;")
        llm_row.addWidget(self.llm_settings_btn)
        llm_row.addWidget(self.llm_summary)
        llm_row.addStretch(1)
        left.addLayout(llm_row)
        left.addStretch(1)
        layout.addLayout(left)

        right = QtWidgets.QVBoxLayout()
        self.ai_log = QtWidgets.QTableWidget(0, 8)
        self.ai_log.setHorizontalHeaderLabels(
            ["轮次", "Kp", "Ki", "Kd", "评分", "超调%", "调节(s)", "备注"]
        )
        self.ai_log.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.ai_log.verticalHeader().setVisible(False)
        self.ai_log.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ai_log.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.ai_status = QtWidgets.QLabel("就绪：配置 API Key 后可开始自动调参")
        right.addWidget(self.ai_log, 1)
        right.addWidget(self.ai_status)
        layout.addLayout(right, 1)

        return tab

    def _build_history_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        self.trials_table = QtWidgets.QTableWidget(0, 10)
        self.trials_table.setHorizontalHeaderLabels(
            ["时间", "类型", "Kp", "Ki", "Kd", "超调%", "上升(s)", "调节(s)", "稳态误差", "恢复(s)"]
        )
        self.trials_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.trials_table.verticalHeader().setVisible(False)
        self.trials_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.trials_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.trials_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        layout.addWidget(self.trials_table, 1)

        button_row = QtWidgets.QHBoxLayout()
        refresh_btn = QtWidgets.QPushButton("刷新")
        refresh_btn.clicked.connect(self._refresh_trials_table)
        export_sel_btn = QtWidgets.QPushButton("导出选中为 config.json / .h")
        export_sel_btn.clicked.connect(self._export_selected_trial)
        export_cur_btn = QtWidgets.QPushButton("导出当前参数")
        export_cur_btn.clicked.connect(self._export_current)
        button_row.addWidget(refresh_btn)
        button_row.addWidget(export_sel_btn)
        button_row.addWidget(export_cur_btn)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        return tab

    def _build_code_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(tab)

        left = QtWidgets.QVBoxLayout()
        request_group = QtWidgets.QGroupBox("修改需求（仅限 PID 算法相关文件）")
        request_layout = QtWidgets.QVBoxLayout(request_group)
        self.code_request_edit = QtWidgets.QPlainTextEdit()
        self.code_request_edit.setPlaceholderText(
            "例如：给 PID 增加积分抗饱和（clamping），防止输出饱和时积分继续累积"
        )
        self.code_request_edit.setMaximumHeight(90)
        self.code_context_check = QtWidgets.QCheckBox("附带当前 PID 参数与最近一次实验指标")
        self.code_context_check.setChecked(True)
        request_layout.addWidget(self.code_request_edit)
        request_layout.addWidget(self.code_context_check)
        left.addWidget(request_group)

        settings_group = QtWidgets.QGroupBox("opencode 设置（全局配置）")
        settings_form = QtWidgets.QFormLayout(settings_group)
        self.code_exe_edit = QtWidgets.QLineEdit()
        self.code_agent_edit = QtWidgets.QLineEdit()
        self.code_agent_edit.setPlaceholderText("可选：受限 agent 名称")
        self.code_timeout_spin = QtWidgets.QSpinBox()
        self.code_timeout_spin.setRange(60, 3600)
        self.code_timeout_spin.setSuffix(" s")
        settings_form.addRow("可执行文件", self.code_exe_edit)
        settings_form.addRow("Agent", self.code_agent_edit)
        settings_form.addRow("超时", self.code_timeout_spin)
        code_save_btn = QtWidgets.QPushButton("保存设置")
        code_save_btn.clicked.connect(self._save_codeagent_settings)
        settings_form.addRow(code_save_btn)
        left.addWidget(settings_group)

        button_row = QtWidgets.QHBoxLayout()
        self.code_preview_btn = QtWidgets.QPushButton("生成预览")
        self.code_preview_btn.clicked.connect(self._code_preview_clicked)
        self.code_run_btn = QtWidgets.QPushButton("授权执行")
        self.code_run_btn.setEnabled(False)
        self.code_run_btn.clicked.connect(self._code_run_clicked)
        self.code_cancel_btn = QtWidgets.QPushButton("中止")
        self.code_cancel_btn.setEnabled(False)
        self.code_cancel_btn.clicked.connect(self._code_cancel_clicked)
        button_row.addWidget(self.code_preview_btn)
        button_row.addWidget(self.code_run_btn)
        button_row.addWidget(self.code_cancel_btn)
        left.addLayout(button_row)

        self.code_status = QtWidgets.QLabel(
            "流程：填写需求 → 生成预览 → 授权执行 → 审查 git diff → 保留或回滚"
        )
        self.code_status.setWordWrap(True)
        left.addWidget(self.code_status)
        left.addStretch(1)
        layout.addLayout(left)

        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        self.code_console = QtWidgets.QPlainTextEdit()
        self.code_console.setReadOnly(True)
        self.code_console.setFont(mono)
        self.code_console.setMaximumBlockCount(4000)
        self.code_diff = QtWidgets.QPlainTextEdit()
        self.code_diff.setReadOnly(True)
        self.code_diff.setFont(mono)
        self.code_diff.setMaximumBlockCount(6000)

        review_row = QtWidgets.QHBoxLayout()
        self.code_keep_btn = QtWidgets.QPushButton("保留修改")
        self.code_keep_btn.setEnabled(False)
        self.code_keep_btn.clicked.connect(self._code_keep_clicked)
        self.code_rollback_btn = QtWidgets.QPushButton("回滚修改")
        self.code_rollback_btn.setEnabled(False)
        self.code_rollback_btn.clicked.connect(self._code_rollback_clicked)
        review_row.addWidget(self.code_keep_btn)
        review_row.addWidget(self.code_rollback_btn)
        review_row.addStretch(1)

        right = QtWidgets.QVBoxLayout()
        right.addWidget(QtWidgets.QLabel("预览 / 执行输出"))
        right.addWidget(self.code_console, 2)
        right.addWidget(QtWidgets.QLabel("变更审查（git diff）"))
        right.addWidget(self.code_diff, 3)
        right.addLayout(review_row)
        layout.addLayout(right, 1)

        self._load_codeagent_settings_into_ui()
        return tab

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

    def _clear_plots(self) -> None:
        self.plots.clear()
        self._last_plot_t = None

    def _plot_span(self) -> float:
        window_s = float(self.plots.window_spin.value())
        return max(5.0, window_s * 2.0)

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
        self._refresh_trials_table()
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
        if self.tuner.active:
            self.tuner.abort()
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
        self._last_plot_t = None
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
        if self.tuner.active:
            self._status("自动调参进行中，请先中止")
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

    def _capture_snapshot(self) -> dict:
        if self.link is None or self._capture_t0 is None:
            return {}
        _, t_latest = self.link.buffer.span()
        span = max(1.0, t_latest - self._capture_t0 + 1.0)
        return self.link.buffer.tail_span(span)

    def _compute_metrics(self) -> None:
        if self.link is None or self._capture_t0 is None:
            QtWidgets.QMessageBox.information(self, "无法计算", "请先连接链路并开始采集。")
            return
        data = self._capture_snapshot()
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
        self._last_metrics = self._step_metrics_dict(metrics)
        if self.workspace is not None:
            self.workspace.append_trial(
                {
                    "kind": "manual_step",
                    "setpoint": float(self._capture_sp),
                    "gains": self._gains_dict(),
                    "metrics": self._last_metrics,
                    "capture_s": float(t[-1] - self._capture_t0),
                }
            )
            self._refresh_trials_table()

    def _start_experiment(self, kind: str) -> None:
        if not self._ensure_link():
            return
        if self.tuner.active:
            self._status("自动调参进行中，请先中止")
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
        busy = active or self.tuner.active
        self.step_exp_btn.setEnabled(not busy)
        self.disturb_exp_btn.setEnabled(not busy)
        self.abort_exp_btn.setEnabled(active)
        self.capture_btn.setEnabled(not busy)

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
            if self.tuner.active:
                self.tuner.abort()

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
        data = self._capture_snapshot()
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
            marks = {label: mt for mt, label in self._script_marks}
            step_t = marks.get(MARK_STEP)
            keep = None
            if step_t is not None:
                start = max(float(tt[0]), float(step_t) - 0.3)
                keep = tt >= start
                tt = tt[keep]
                yy = yy[keep]
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
            waveform = None
            if self.tuner.active and self.ai_wave_check.isChecked():
                selection = slice(None) if keep is None else keep
                waveform = self._downsample_waveform(
                    tt,
                    data["setpoint"][mask][selection],
                    yy,
                    data["u"][mask][selection],
                    data["up"][mask][selection],
                    data["ui"][mask][selection],
                    data["ud"][mask][selection],
                )
        self._set_metrics_rows(rows)
        self._status("实验指标已计算")
        self._last_metrics = trial["metrics"]
        if self.workspace is not None:
            self.workspace.append_trial(trial)
            self._refresh_trials_table()
        if kind == "step" and self.tuner.active:
            self.tuner.on_trial(
                m, self._gains_dict(), float(self._capture_sp), waveform=waveform
            )

    @staticmethod
    def _downsample_waveform(t, setpoint, feedback, u, up, ui, ud, max_points: int = 80) -> dict:
        n = int(t.size)
        if n == 0:
            return {}
        step = max(1, (n + max_points - 1) // max_points)
        idx = slice(None, None, step)
        t0 = float(t[0])

        def series(values):
            return [round(float(value), 4) for value in values[idx]]

        return {
            "t": [round(float(value) - t0, 4) for value in t[idx]],
            "setpoint": series(setpoint),
            "feedback": series(feedback),
            "u": series(u),
            "up": series(up),
            "ui": series(ui),
            "ud": series(ud),
        }

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

    def _llm_summary_text(self) -> str:
        llm = self.app_config.data.get("llm", {})
        model = str(llm.get("model", DEFAULT_LLM_MODEL))
        state = "已配置 Key" if str(llm.get("api_key", "")).strip() else "未配置 Key"
        return f"{model} · {state}"

    def _open_llm_settings_dialog(self) -> None:
        llm = dict(self.app_config.data.get("llm", {}))
        dialog = LLMSettingsDialog(llm, self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._apply_llm_settings(dialog.values())

    def _apply_llm_settings(self, values: dict) -> None:
        llm = self.app_config.data.setdefault("llm", {})
        llm["base_url"] = values.get("base_url") or "https://api.deepseek.com"
        llm["model"] = values.get("model") or DEFAULT_LLM_MODEL
        llm["api_key"] = values.get("api_key", "")
        self.app_config.save()
        self.llm_summary.setText(self._llm_summary_text())
        self._status("LLM 设置已保存（全局配置，不写入固件目录）")

    def _make_suggest(self):
        llm = self.app_config.data.get("llm", {})
        client = DeepSeekTuner(
            api_key=str(llm.get("api_key", "")),
            base_url=str(llm.get("base_url", "https://api.deepseek.com")),
            model=str(llm.get("model", DEFAULT_LLM_MODEL)),
        )
        return client.suggest

    def _start_autotune(self) -> None:
        if not self._ensure_link():
            return
        if self.tuner.active:
            return
        if self.runner.active:
            self._status("实验运行中，无法启动自动调参")
            return
        if not str(self.app_config.data.get("llm", {}).get("api_key", "")).strip():
            QtWidgets.QMessageBox.information(
                self, "缺少 API Key", "请先点击“DeepSeek 设置…”填写 API Key 并保存。"
            )
            return
        try:
            suggest = self._make_suggest()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "AI 客户端初始化失败", str(exc))
            return
        self.ai_log.setRowCount(0)
        self.sp_spin.setValue(self.ai_target_spin.value())
        self._set_ai_ui(True)
        try:
            self.tuner.start(
                self.kp_spin.value(),
                self.ki_spin.value(),
                self.kd_spin.value(),
                max_iters=self.ai_iters_spin.value(),
                suggest=suggest,
                limits=copy.deepcopy(self.safety),
                setpoint=self.ai_target_spin.value(),
            )
        except Exception as exc:
            self._set_ai_ui(False)
            QtWidgets.QMessageBox.critical(self, "启动失败", str(exc))

    def _abort_autotune(self) -> None:
        if self.tuner.active:
            self.tuner.abort()
        if self.runner.active:
            self.runner.abort()

    def _set_ai_ui(self, active: bool) -> None:
        self.ai_start_btn.setEnabled(not active)
        self.ai_abort_btn.setEnabled(active)
        self._set_script_ui(self.runner.active)

    def _on_tuner_event(self, kind: str, data: dict) -> None:
        if kind == "status":
            text = str(data.get("text", ""))
            self.ai_status.setText(text)
            self._status(text)
        elif kind == "apply_gains":
            self._set_gain_spins(
                float(data.get("kp", 0.0)),
                float(data.get("ki", 0.0)),
                float(data.get("kd", 0.0)),
            )
            self._apply_gains(silent=True)
        elif kind == "run_experiment":
            script = autotune_step_script(
                self.ai_target_spin.value(),
                settle_s=self._ai_settle_s,
                countdown_s=self._ai_countdown_s,
                measure_s=self.ai_measure_spin.value(),
            )
            self._run_script(script)
        elif kind == "iteration":
            self._append_ai_log_row(data)
        elif kind == "finished":
            self._set_ai_ui(False)
            best = data.get("best_gains")
            score = data.get("best_score")
            text = f"{data.get('reason', '完成')}；共 {data.get('iterations', 0)} 轮"
            if best is not None and score is not None:
                text += (
                    f"，最优评分 {score:.2f} "
                    f"(Kp={best['kp']:g} Ki={best['ki']:g} Kd={best['kd']:g})"
                )
            self.ai_status.setText(text)
            self._status(text)
            self._refresh_trials_table()

    def _append_ai_log_row(self, entry: dict) -> None:
        row = self.ai_log.rowCount()
        self.ai_log.insertRow(row)
        values = [
            str(entry.get("iteration", "")),
            _fmt_num(entry.get("kp"), ".4g"),
            _fmt_num(entry.get("ki"), ".4g"),
            _fmt_num(entry.get("kd"), ".4g"),
            _fmt_num(entry.get("score"), ".2f"),
            _fmt_num(entry.get("overshoot_pct"), ".2f"),
            _fmt_num(entry.get("settling_time"), ".3f"),
            "当前最优" if entry.get("best") else "",
        ]
        for col, text in enumerate(values):
            self.ai_log.setItem(row, col, QtWidgets.QTableWidgetItem(text))

    def _refresh_trials_table(self) -> None:
        self.trials_table.setRowCount(0)
        self._trial_records = []
        if self.workspace is None:
            return
        records = list(reversed(self.workspace.read_trials()))
        self._trial_records = records
        for trial in records:
            row = self.trials_table.rowCount()
            self.trials_table.insertRow(row)
            gains = trial.get("gains") or {}
            metrics = trial.get("metrics") or {}
            timestamp = trial.get("ts")
            time_text = (
                datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M:%S")
                if isinstance(timestamp, (int, float))
                else "—"
            )
            values = [
                time_text,
                str(trial.get("kind", "")),
                _fmt_num(gains.get("kp"), ".4g"),
                _fmt_num(gains.get("ki"), ".4g"),
                _fmt_num(gains.get("kd"), ".4g"),
                _fmt_num(metrics.get("overshoot_pct"), ".2f"),
                _fmt_num(metrics.get("rise_time"), ".3f"),
                _fmt_num(metrics.get("settling_time"), ".3f"),
                _fmt_num(metrics.get("steady_error"), "+.5f"),
                _fmt_num(metrics.get("recovery_time"), ".3f"),
            ]
            for col, text in enumerate(values):
                self.trials_table.setItem(row, col, QtWidgets.QTableWidgetItem(text))
        if records:
            self.trials_table.selectRow(0)

    def _export_selected_trial(self) -> None:
        if self.workspace is None:
            self._status("请先选择工作目录")
            return
        row = self.trials_table.currentRow()
        if row < 0 or row >= len(self._trial_records):
            self._status("请先在历史列表中选择一条记录")
            return
        trial = self._trial_records[row]
        gains = trial.get("gains") or self._gains_dict()
        try:
            config_path, header_path = export_artifacts(
                self.workspace,
                gains,
                metrics=trial.get("metrics"),
                source=str(trial.get("kind", "trial")),
                setpoint=trial.get("setpoint"),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "导出失败", str(exc))
            return
        self._status(f"已导出 {config_path.name} 与 {header_path.name} 到 {config_path.parent}")

    def _export_current(self) -> None:
        if self.workspace is None:
            self._status("请先选择工作目录")
            return
        try:
            config_path, header_path = export_artifacts(
                self.workspace,
                self._gains_dict(),
                metrics=self._last_metrics,
                source="current",
                setpoint=self.sp_spin.value(),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "导出失败", str(exc))
            return
        self._status(f"已导出 {config_path.name} 与 {header_path.name} 到 {config_path.parent}")

    def _load_codeagent_settings_into_ui(self) -> None:
        cfg = self.app_config.data.get("codeagent", {})
        self.code_exe_edit.setText(str(cfg.get("executable", "opencode")))
        self.code_agent_edit.setText(str(cfg.get("agent_name", "")))
        self.code_timeout_spin.setValue(int(cfg.get("timeout_s", 900)))

    def _save_codeagent_settings(self) -> None:
        cfg = self.app_config.data.setdefault("codeagent", {})
        cfg["executable"] = self.code_exe_edit.text().strip() or "opencode"
        cfg["agent_name"] = self.code_agent_edit.text().strip()
        cfg["timeout_s"] = int(self.code_timeout_spin.value())
        self.app_config.save()
        self._status("opencode 设置已保存（全局配置）")

    def _make_code_agent(self) -> OpencodeAgent:
        cfg = self.app_config.data.get("codeagent", {})
        return OpencodeAgent(
            executable=self.code_exe_edit.text().strip() or "opencode",
            agent_name=self.code_agent_edit.text().strip(),
            auto_approve=bool(cfg.get("auto_approve", True)),
            timeout=float(self.code_timeout_spin.value()),
        )

    def _code_preview_clicked(self) -> None:
        if self.workspace is None:
            self._status("请先选择工作目录")
            return
        request = self.code_request_edit.toPlainText().strip()
        if not request:
            self._status("请先填写修改需求")
            return
        gains = self._gains_dict() if self.code_context_check.isChecked() else None
        metrics = self._last_metrics if self.code_context_check.isChecked() else None
        self._code_prompt = build_code_prompt(request, self.workspace.root, gains, metrics)
        exe = self.code_exe_edit.text().strip() or "opencode"
        agent_part = f" --agent {self.code_agent_edit.text().strip()}" if self.code_agent_edit.text().strip() else ""
        command_line = f'{exe} run --dir "{self.workspace.root}" --auto{agent_part} "<提示词>"'
        problems = CodeEditSession.precheck(self.workspace.root)
        problem_text = "\n".join(f"- {p}" for p in problems)
        preview = [
            "=== 将执行的命令 ===",
            command_line,
            "",
            "=== 预检查 ===",
            problem_text if problems else "通过：git 仓库、已跟踪文件无未提交改动",
            "",
            "=== 提示词 ===",
            self._code_prompt,
        ]
        self.code_console.setPlainText("\n".join(preview))
        self.code_diff.clear()
        self._code_decided = False
        self.code_run_btn.setEnabled(not problems)
        if problems:
            self.code_status.setText("预检查未通过，请先处理上述问题再授权执行")
        else:
            self.code_status.setText("预览已生成；确认无误后点击“授权执行”")

    def _code_run_clicked(self) -> None:
        if self.workspace is None or not self._code_prompt:
            self._status("请先生成预览")
            return
        if self.code_session.active:
            return
        problems = CodeEditSession.precheck(self.workspace.root)
        if problems:
            QtWidgets.QMessageBox.warning(
                self, "预检查未通过", "\n".join(f"- {p}" for p in problems)
            )
            return
        try:
            agent = self._make_code_agent()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "opencode 初始化失败", str(exc))
            return
        try:
            self.code_session.start(agent, self.workspace.root, self._code_prompt)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "启动失败", str(exc))
            return
        self.code_console.appendPlainText("\n=== 开始执行 ===")
        self.code_diff.clear()
        self._code_decided = False
        self._code_review_shown = False
        self._set_code_running_ui(True)
        self.code_status.setText("opencode 执行中…（可点中止）")

    def _code_cancel_clicked(self) -> None:
        if self.code_session.active:
            self.code_session.cancel()
            self.code_status.setText("正在中止…")

    def _set_code_running_ui(self, running: bool) -> None:
        self.code_preview_btn.setEnabled(not running)
        self.code_run_btn.setEnabled(not running and bool(self._code_prompt))
        self.code_cancel_btn.setEnabled(running)
        if running:
            self.code_keep_btn.setEnabled(False)
            self.code_rollback_btn.setEnabled(False)

    def _on_code_done(self, outcome) -> None:
        self._set_code_running_ui(False)
        lines = []
        status_bits = [f"退出码 {outcome.exit_code}", f"耗时 {outcome.duration_s:.1f}s"]
        if outcome.timed_out:
            status_bits.append("超时中止")
        if outcome.cancelled:
            status_bits.append("已取消")
        if outcome.error:
            status_bits.append(f"错误: {outcome.error}")
            lines.append(f"[错误] {outcome.error}")
        if outcome.diff_text.strip():
            lines.append(outcome.diff_text)
        if outcome.new_files:
            lines.append("新增文件:")
            lines += [f"  + {name}" for name in outcome.new_files]
        if not outcome.diff_text.strip() and not outcome.new_files:
            lines.append("(没有检测到任何文件改动)")
        self.code_diff.setPlainText("\n".join(lines))
        has_changes = bool(outcome.diff_text.strip() or outcome.new_files)
        self.code_keep_btn.setEnabled(has_changes)
        self.code_rollback_btn.setEnabled(True)
        self.code_status.setText("执行结束（{}）；请审查 diff 后选择保留或回滚".format("，".join(status_bits)))

    def _code_keep_clicked(self) -> None:
        outcome = self.code_session.outcome
        if outcome is None or self._code_decided:
            return
        self._code_decided = True
        if self.workspace is not None:
            self.workspace.append_trial(
                {
                    "kind": "code_edit",
                    "request": self.code_request_edit.toPlainText().strip(),
                    "outcome": "kept",
                    "exit_code": outcome.exit_code,
                    "timed_out": outcome.timed_out,
                    "cancelled": outcome.cancelled,
                    "duration_s": outcome.duration_s,
                    "changed_files": outcome.changed_files,
                    "new_files": outcome.new_files,
                    "diff_stat": outcome.diff_stat,
                    "output_tail": outcome.output_tail[-40:],
                }
            )
            self._refresh_trials_table()
        self.code_keep_btn.setEnabled(False)
        self.code_rollback_btn.setEnabled(False)
        self.code_status.setText("已保留修改（已记入历史）。请自行编译/烧录并验证")
        self.code_session.clear()

    def _code_rollback_clicked(self) -> None:
        outcome = self.code_session.outcome
        if outcome is None or self._code_decided:
            return
        removed = self.code_session.rollback()
        self._code_decided = True
        if self.workspace is not None:
            self.workspace.append_trial(
                {
                    "kind": "code_edit",
                    "request": self.code_request_edit.toPlainText().strip(),
                    "outcome": "rolled_back",
                    "exit_code": outcome.exit_code,
                    "timed_out": outcome.timed_out,
                    "cancelled": outcome.cancelled,
                    "duration_s": outcome.duration_s,
                    "changed_files": outcome.changed_files,
                    "new_files": outcome.new_files,
                    "removed_files": removed,
                    "output_tail": outcome.output_tail[-40:],
                }
            )
            self._refresh_trials_table()
        self.code_keep_btn.setEnabled(False)
        self.code_rollback_btn.setEnabled(False)
        removed_text = f"，并删除新增文件 {len(removed)} 个" if removed else ""
        self.code_status.setText(f"已回滚全部改动{removed_text}（已记入历史）")
        self.code_diff.appendPlainText("\n=== 已回滚，工作区恢复到执行前状态 ===")
        self.code_session.clear()

    def _tick(self) -> None:
        self.runner.tick()
        self.tuner.tick()
        for event_kind, payload in self.code_session.poll():
            if event_kind == "output":
                self.code_console.appendPlainText(str(payload))
        outcome = self.code_session.outcome
        if outcome is not None and not self._code_review_shown:
            self._code_review_shown = True
            self._on_code_done(outcome)
        link = self.link
        if link is None:
            return
        if not link.is_open:
            error = link.stats.last_error or "未知错误"
            self._disconnect()
            self._status(f"链路已断开: {error}")
            return
        data = link.buffer.tail_span(self._plot_span())
        t = data["t"]
        if t.size:
            t_last = float(t[-1])
            if t_last != self._last_plot_t:
                self._last_plot_t = t_last
                self.plots.update_data(data, t_last)
        for event in link.drain_events():
            if event.kind == "gains":
                self._set_gain_spins(event.data["kp"], event.data["ki"], event.data["kd"])
                self._status("已读取设备参数")
            elif event.kind == "ack":
                if event.data.get("status", 0) != 0:
                    self._status(f"设备返回错误 ACK: 0x{event.data.get('acked', 0):02X}")
            elif event.kind == "text":
                self._status(str(event.data.get("text", "")))
        ts = data["t"]
        if ts.size >= 2 and float(ts[-1]) > float(ts[0]):
            rate = (ts.size - 1) / (float(ts[-1]) - float(ts[0]))
        else:
            rate = 0.0
        self.lbl_rate.setText(f"采样率: {rate:.0f} Hz")
        stats = link.stats
        self.lbl_rx.setText(
            f"RX {stats.rx_bytes} B / 帧 {stats.frames} / CRC错 {stats.crc_errors} / 杂散 {stats.garbage_bytes} B"
        )
        self.lbl_state.setText(f"已连接 ({self.link_combo.currentText()})")

    def closeEvent(self, event) -> None:
        self._save_settings_from_ui()
        if self.code_session.active:
            self.code_session.cancel()
        self._disconnect()
        self.app_config.save()
        super().closeEvent(event)


def main() -> None:
    pg.setConfigOptions(antialias=True)
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    smoke = os.environ.get("PIDTOOL_SMOKE_EXIT")
    if smoke:
        QtCore.QTimer.singleShot(int(float(smoke) * 1000), app.quit)
    exit_code = app.exec()
    window.close()
    del window
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
