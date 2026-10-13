from __future__ import annotations

import json
import os
import time
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6 import QtWidgets

from pidtool.app_config import AppConfig
from pidtool.codeagent import CodeEditOutcome, git_tools
from pidtool.core.experiments import ExperimentScript, ExperimentStep
from pidtool.core.experiments.specs import MARK_DISTURB_END, MARK_DISTURB_START
from pidtool.link.mock_link import MockLink
from pidtool.link.protocol import (
    MODE_IDLE,
    MODE_RUN,
    DefaultProtocolAdapter,
    LinkEvent,
    Sample,
)
from pidtool.ui import main_window as mw
from pidtool.ui.main_window import MainWindow, _fmt_num, _fmt_sec, _json_number


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    yield app


@pytest.fixture(autouse=True)
def modal_recorder(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    def make(kind: str, result):
        def handler(parent, title, text, *args, **kwargs):
            calls.append((kind, title, text))
            return result

        return staticmethod(handler)

    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "information",
        make("information", QtWidgets.QMessageBox.StandardButton.Ok),
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        make("warning", QtWidgets.QMessageBox.StandardButton.Ok),
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "critical",
        make("critical", QtWidgets.QMessageBox.StandardButton.Ok),
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        make("question", QtWidgets.QMessageBox.StandardButton.Yes),
    )
    return calls


def _make_window(qapp, tmp_path, activate=True, last_workspace=None):
    config = AppConfig(path=tmp_path / "app.toml")
    if last_workspace is not None:
        config.data["last_workspace"] = str(last_workspace)
        config.save()
    window = MainWindow(app_config=config)
    if activate:
        window._activate_workspace(tmp_path)
    return window


@pytest.fixture()
def window(qapp, tmp_path):
    win = _make_window(qapp, tmp_path)
    yield win
    win._disconnect()
    win.close()


def _wait_until(qapp, condition, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture()
def mock_window(qapp, tmp_path, window):
    window.link_combo.setCurrentIndex(window.link_combo.findData("mock"))
    window._connect()
    assert window.link is not None
    assert _wait_until(qapp, lambda: window.link.buffer.total >= 60, 10.0)
    yield window


def _manual_link(window, value_fn, count=200, dt=0.01):
    link = MockLink(DefaultProtocolAdapter())
    for i in range(count):
        t = i * dt
        link.buffer.append([Sample(t, int(t * 1e6), 0.0, value_fn(t), 0.0, 0.0, 0.0, 0.0)])
    window.link = link
    return link


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_tools.run_git(["init"], repo)
    (repo / "pid.c").write_text("float kp = 1.0f;\n", encoding="utf-8")
    git_tools.run_git(["add", "."], repo)
    git_tools.run_git(
        ["-c", "user.name=test", "-c", "user.email=test@test", "commit", "-m", "init"],
        repo,
    )
    return repo


def test_format_helpers():
    assert _fmt_sec(float("nan")) == "—"
    assert _fmt_sec(1.23456) == "1.2346 s"
    assert _fmt_num(None, ".2f") == "—"
    assert _fmt_num("bad", ".2f") == "—"
    assert _fmt_num(float("nan"), ".2f") == "—"
    assert _fmt_num(float("inf"), ".2f") == "—"
    assert _fmt_num(float("-inf"), ".2f") == "—"
    assert _fmt_num(1.234, ".2f") == "1.23"
    assert _json_number(float("nan")) is None
    assert _json_number(1.5) == 1.5


def test_startup_activates_last_workspace(qapp, tmp_path):
    win = _make_window(qapp, tmp_path, activate=False, last_workspace=tmp_path)
    try:
        assert win.workspace is not None
        assert win.workspace.root == tmp_path
    finally:
        win.close()


def test_refresh_ports_restores_selection(window, monkeypatch):
    fakes = [
        types.SimpleNamespace(device="COM7", description="n/a"),
        types.SimpleNamespace(device="COM9", description="USB Serial"),
    ]
    monkeypatch.setattr(mw.list_ports, "comports", lambda: fakes)
    window.port_combo.clear()
    window.port_combo.addItem("COM9 (USB Serial)", "COM9")
    window.port_combo.setCurrentIndex(0)
    window._refresh_ports()
    assert window.port_combo.count() == 2
    assert window.port_combo.currentData() == "COM9"
    assert window.port_combo.itemText(0) == "COM7"


def test_set_workspace_combo_inserts_unknown(window):
    ghost = "Z:/ghost/workspace"
    window._set_workspace_combo(ghost)
    assert window.workspace_combo.currentData() == ghost
    assert window.workspace_combo.findData(ghost) == 0


def test_on_workspace_selected_missing_dir(window, tmp_path, modal_recorder):
    ghost = str(tmp_path / "ghost")
    window.workspace_combo.addItem(ghost, ghost)
    window._on_workspace_selected(window.workspace_combo.count() - 1)
    assert modal_recorder[-1][0] == "warning"
    assert "不存在" in modal_recorder[-1][1]


def test_on_workspace_selected_empty_and_valid(window, tmp_path):
    window.workspace_combo.addItem("（空）", "")
    window._on_workspace_selected(window.workspace_combo.count() - 1)
    assert window.workspace.root == tmp_path

    project = tmp_path / "selected"
    project.mkdir()
    window.workspace_combo.addItem(str(project), str(project))
    window._on_workspace_selected(window.workspace_combo.count() - 1)
    assert window.workspace.root == project


def test_browse_workspace_creates_gitignore(window, tmp_path, monkeypatch, modal_recorder):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *args, **kwargs: str(project)),
    )
    window._browse_workspace()
    assert window.workspace is not None
    assert window.workspace.root == project
    assert modal_recorder[-1][0] == "question"
    assert (project / ".gitignore").read_text(encoding="utf-8").strip() == ".pidtool/"


def test_activate_workspace_gitignore_question_yes(window, tmp_path, modal_recorder):
    project = tmp_path / "fresh"
    project.mkdir()
    window._activate_workspace(project, ask_gitignore=True)
    assert modal_recorder[-1][0] == "question"
    assert (project / ".gitignore").is_file()


def test_apply_settings_adds_port_and_baud(window):
    window._apply_settings_to_ui(
        {"serial": {"port": "COM_FAKE", "baud": 12345}, "link": {"type": "serial"}}
    )
    assert window.port_combo.currentData() == "COM_FAKE"
    assert window.baud_combo.currentData() == 12345
    assert window.link_combo.currentData() == "serial"


def test_toggle_connection(qapp, window):
    assert window.link is None
    window.link_combo.setCurrentIndex(window.link_combo.findData("mock"))
    window._toggle_connection()
    assert window.link is not None
    assert window.connect_btn.text() == "断开"
    window._toggle_connection()
    assert window.link is None
    assert window.connect_btn.text() == "连接"


def test_connect_adapter_failure(window, modal_recorder):
    settings = window.workspace.load_settings()
    settings["serial"]["adapter"] = "definitely_missing_adapter.py"
    window.workspace.save_settings(settings)
    window._connect()
    assert window.link is None
    assert modal_recorder[-1][0] == "critical"
    assert "适配器加载失败" in modal_recorder[-1][1]


def test_connect_serial_requires_port(window, modal_recorder):
    window.link_combo.setCurrentIndex(window.link_combo.findData("serial"))
    window.port_combo.clear()
    window._connect()
    assert window.link is None
    assert modal_recorder[-1][0] == "information"
    assert "无串口" in modal_recorder[-1][1]


def test_connect_serial_open_failure(window, monkeypatch, modal_recorder):
    def boom(*args, **kwargs):
        raise OSError("cannot open")

    monkeypatch.setattr("pidtool.link.serial_link.serial.Serial", boom)
    window.link_combo.setCurrentIndex(window.link_combo.findData("serial"))
    window.port_combo.clear()
    window.port_combo.addItem("COM_FAKE", "COM_FAKE")
    window._connect()
    assert window.link is None
    assert modal_recorder[-1][0] == "critical"
    assert "连接失败" in modal_recorder[-1][1]


def test_disconnect_aborts_runner_and_tuner(mock_window):
    mock_window._run_script(
        ExperimentScript("长实验", (ExperimentStep("wait", {"seconds": 60.0}),))
    )
    assert mock_window.runner.active
    mock_window.tuner._active = True
    mock_window._disconnect()
    assert not mock_window.runner.active
    assert not mock_window.tuner.active
    assert mock_window.link is None


def test_disconnect_aborts_tuner_without_runner(mock_window):
    mock_window.tuner._active = True
    mock_window._disconnect()
    assert not mock_window.tuner.active
    assert mock_window.link is None


def test_disconnect_swallows_close_errors(mock_window, monkeypatch):
    def boom():
        raise OSError("close failed")

    monkeypatch.setattr(mock_window.link, "close", boom)
    mock_window._disconnect()
    assert mock_window.link is None


def test_actions_without_link_show_message(window, modal_recorder):
    window._apply_gains()
    window._read_gains()
    window._apply_setpoint()
    window._set_mode(MODE_RUN)
    window._start_telemetry()
    window._stop_telemetry()
    assert len(modal_recorder) == 6
    assert all(call[0] == "information" and call[1] == "未连接" for call in modal_recorder)


def test_apply_gains_clamped_and_status(mock_window):
    mock_window.safety["kp"]["max"] = 5.0
    messages: list[str] = []
    mock_window._status = lambda message, timeout=5000: messages.append(message)
    mock_window._set_gain_spins(50.0, 60.0, 0.05)
    mock_window._apply_gains()
    assert mock_window.kp_spin.value() == 5.0
    assert mock_window.link.kp == 5.0
    assert any("限幅" in message for message in messages)
    messages.clear()
    mock_window._apply_gains()
    assert any("已下发" in message for message in messages)
    assert not any("限幅" in message for message in messages)


def test_send_failures_are_reported(mock_window, monkeypatch):
    def boom(data):
        raise RuntimeError("write failed")

    monkeypatch.setattr(mock_window.link, "send", boom)
    mock_window._apply_gains()
    assert "发送失败" in mock_window.statusBar().currentMessage()
    mock_window._read_gains()
    assert "发送失败" in mock_window.statusBar().currentMessage()
    mock_window._apply_setpoint()
    assert "发送失败" in mock_window.statusBar().currentMessage()
    mock_window._set_mode(MODE_RUN)
    assert "发送失败" in mock_window.statusBar().currentMessage()
    mock_window._start_telemetry()
    assert "发送失败" in mock_window.statusBar().currentMessage()
    mock_window._stop_telemetry()
    assert "发送失败" in mock_window.statusBar().currentMessage()


def test_read_gains_updates_spins(qapp, mock_window):
    mock_window._set_gain_spins(1.0, 2.0, 3.0)
    mock_window._apply_gains(silent=True)
    mock_window._set_gain_spins(9.0, 9.0, 9.0)
    mock_window._read_gains()
    mock_window._tick()
    assert mock_window.kp_spin.value() == 1.0
    assert mock_window.ki_spin.value() == 2.0
    assert mock_window.kd_spin.value() == 3.0
    assert mock_window.statusBar().currentMessage() == "已读取设备参数"


def test_set_mode_and_telemetry_roundtrip(mock_window):
    mock_window._set_mode(MODE_IDLE)
    assert mock_window.link.mode == MODE_IDLE
    assert mock_window.statusBar().currentMessage() == "已停止"
    mock_window._set_mode(MODE_RUN)
    assert mock_window.link.mode == MODE_RUN
    assert mock_window.statusBar().currentMessage() == "已使能"

    mock_window._start_telemetry()
    assert mock_window.link.telemetry_enabled
    assert mock_window.link.telemetry_period_ms == 5
    mock_window._stop_telemetry()
    assert not mock_window.link.telemetry_enabled


def test_latest_t_without_link(window):
    assert window._latest_t() is None


def test_on_capture_clicked_guards(qapp, mock_window):
    mock_window._run_script(
        ExperimentScript("长实验", (ExperimentStep("wait", {"seconds": 60.0}),))
    )
    mock_window._on_capture_clicked()
    assert "实验运行中" in mock_window.statusBar().currentMessage()
    mock_window.runner.abort()
    mock_window.tuner._active = True
    mock_window._on_capture_clicked()
    assert "自动调参进行中" in mock_window.statusBar().currentMessage()
    mock_window.tuner._active = False


def test_capture_click_requires_link(window, modal_recorder):
    window._on_capture_clicked()
    assert modal_recorder[-1][0] == "information"
    assert modal_recorder[-1][1] == "未连接"
    assert not window._capturing


def test_capture_click_toggles(mock_window):
    mock_window._on_capture_clicked()
    assert mock_window._capturing
    assert mock_window.capture_btn.text() == "结束采集"
    mock_window._on_capture_clicked()
    assert not mock_window._capturing
    assert mock_window.capture_btn.text() == "开始采集"


def test_start_capture_guards(window):
    window._start_capture()
    assert not window._capturing
    window.link = MockLink(DefaultProtocolAdapter())
    window._start_capture()
    assert not window._capturing
    assert "暂无数据" in window.statusBar().currentMessage()


def test_capture_snapshot_guards(window):
    assert window._capture_snapshot() == {}
    window.link = MockLink(DefaultProtocolAdapter())
    assert window._capture_snapshot() == {}


def test_compute_metrics_guards(window, modal_recorder):
    window._compute_metrics()
    assert modal_recorder[-1][0] == "information"
    assert modal_recorder[-1][1] == "无法计算"

    _manual_link(window, lambda t: 1.0, count=10)
    window._capture_t0 = 0.0
    window._compute_metrics()
    assert "数据不足" in window.statusBar().currentMessage()

    _manual_link(window, lambda t: 1.0, count=200)
    window._capture_t0 = 1.9
    window._capture_t1 = None
    window._compute_metrics()
    assert "采集窗口内数据不足" in window.statusBar().currentMessage()

    _manual_link(window, lambda t: 0.5, count=200)
    window._capture_t0 = 0.0
    window._capture_sp = 0.5
    window._compute_metrics()
    assert "采集窗口无效" in window.statusBar().currentMessage()


def test_start_experiment_guards(qapp, mock_window):
    mock_window.tuner._active = True
    mock_window._start_experiment("step")
    assert "自动调参进行中" in mock_window.statusBar().currentMessage()
    mock_window.tuner._active = False

    mock_window._run_script(
        ExperimentScript("长实验", (ExperimentStep("wait", {"seconds": 60.0}),))
    )
    mock_window._start_experiment("step")
    assert "已有实验在运行" in mock_window.statusBar().currentMessage()
    mock_window.runner.abort()

    mock_window.link = None
    mock_window._start_experiment("step")
    assert not mock_window.runner.active


def test_start_experiment_both_kinds(qapp, mock_window):
    mock_window._start_experiment("step")
    assert mock_window.runner.active
    assert mock_window.runner.script_name == "阶跃响应"
    mock_window.runner.abort()

    mock_window._start_experiment("disturbance")
    assert mock_window.runner.active
    assert mock_window.runner.script_name == "扰动抑制"
    mock_window.runner.abort()


def test_abort_experiment_without_runner(window):
    window._abort_experiment()
    assert window.statusBar().currentMessage() == "当前没有运行中的实验"


def test_abort_experiment_with_runner(qapp, mock_window):
    mock_window._run_script(
        ExperimentScript("长实验", (ExperimentStep("wait", {"seconds": 60.0}),))
    )
    assert mock_window.runner.active
    mock_window._abort_experiment()
    assert not mock_window.runner.active


def test_banner_confirm_noop(window):
    window._on_banner_confirm()


def test_experiment_events_direct(window, modal_recorder):
    window._on_experiment_event("started", {"name": "X"})
    assert "实验开始" in window.statusBar().currentMessage()
    window._on_experiment_event("wait", {"seconds": 1.25})
    assert "等待 1.2 s" in window.statusBar().currentMessage()

    window._on_experiment_event("prompt", {"text": "倒计时", "manual": False})
    assert not window.banner.isHidden()
    assert window.banner_confirm.isHidden()
    window._on_experiment_event("prompt_tick", {"remaining": 1.5})
    assert "倒计时" in window.banner_label.text()
    assert "1.5 s" in window.banner_label.text()

    window._on_experiment_event("prompt", {"text": "手动", "manual": True})
    assert not window.banner_confirm.isHidden()
    window._on_experiment_event("prompt_tick", {"remaining": 1.5})
    window._on_experiment_event("prompt_done", {})
    assert window.banner.isHidden()

    window._on_experiment_event("mark", {"label": "标记A"})
    assert "标记: 标记A" in window.statusBar().currentMessage()
    window._on_experiment_event("command", {"cmd": "mode", "mode": "run"})
    window._on_experiment_event("collect_start", {"lookback": 0.5})
    window._on_experiment_event("collect_end", {"kind": "step"})
    window._on_experiment_event("finished", {})
    assert window.statusBar().currentMessage() == "实验完成"


def test_experiment_event_aborted(window):
    messages: list[str] = []
    window._status = lambda message, timeout=5000: messages.append(message)
    window.tuner._active = True
    window._on_experiment_event("aborted", {})
    assert "实验已中止" in messages
    assert not window.tuner.active


def test_run_script_commands(mock_window):
    mock_window._run_script_command({"cmd": "mode", "mode": "idle"})
    assert mock_window.link.mode == MODE_IDLE
    mock_window._run_script_command({"cmd": "mode", "mode": "run"})
    assert mock_window.link.mode == MODE_RUN
    mock_window._run_script_command({"cmd": "setpoint", "value": 3.0})
    assert abs(mock_window.link.setpoint - 3.0) < 1e-6
    assert mock_window.sp_spin.value() == 3.0
    mock_window._run_script_command({"cmd": "gains", "kp": 7.0, "ki": 70.0, "kd": 0.07})
    assert mock_window.link.kp == 7.0
    assert mock_window.link.ki == 70.0
    assert abs(mock_window.link.kd - 0.07) < 1e-6


def test_finish_collect_guards(window):
    window._finish_collect("step")
    assert "采集数据缺失" in window.statusBar().currentMessage()

    window.link = MockLink(DefaultProtocolAdapter())
    window._capture_t0 = 0.0
    window._finish_collect("step")
    assert "数据不足" in window.statusBar().currentMessage()

    _manual_link(window, lambda t: 1.0, count=200)
    window._capture_t0 = 1.9
    window._finish_collect("step")
    assert "采集窗口内数据不足" in window.statusBar().currentMessage()

    _manual_link(window, lambda t: 1.0, count=200)
    window._capture_t0 = 0.0
    window._capture_sp = 1.0
    window._script_marks = []
    window._finish_collect("disturbance")
    assert "缺少扰动区间标记" in window.statusBar().currentMessage()

    window._script_marks = [(50.0, MARK_DISTURB_START), (51.0, MARK_DISTURB_END)]
    window._finish_collect("disturbance")
    assert "扰动指标计算失败" in window.statusBar().currentMessage()

    _manual_link(window, lambda t: 0.5, count=200)
    window._capture_t0 = 0.0
    window._capture_sp = 0.5
    window._script_marks = []
    window._finish_collect("step")
    assert "阶跃窗口无效" in window.statusBar().currentMessage()


def test_finish_collect_disturbance_with_waveform(window, tmp_path):
    def value(t: float) -> float:
        return 0.6 if 2.0 <= t < 2.5 else 1.0

    _manual_link(window, value, count=400)
    window._capture_t0 = 0.0
    window._capture_sp = 1.0
    window._script_marks = [(2.0, MARK_DISTURB_START), (2.5, MARK_DISTURB_END)]
    window.ai_wave_check.setChecked(True)
    captured: dict = {}

    def spy(metrics, gains, setpoint, kind="step", waveform=None):
        captured["metrics"] = metrics
        captured["kind"] = kind
        captured["waveform"] = waveform

    window.tuner._active = True
    window.tuner.on_trial = spy
    try:
        window._finish_collect("disturbance")
    finally:
        window.tuner._active = False

    assert captured["kind"] == "disturbance"
    assert captured["metrics"] is not None
    waveform = captured["waveform"]
    assert waveform is not None
    assert 0 < len(waveform["t"]) <= 80
    assert len(waveform["feedback"]) == len(waveform["t"])
    labels = [
        window.metrics_table.item(i, 0).text() for i in range(window.metrics_table.rowCount())
    ]
    assert "最大偏差" in labels
    trials = window.workspace.read_trials()
    assert trials[-1]["kind"] == "script_disturbance"
    assert trials[-1]["setpoint"] == 1.0


def test_downsample_waveform_empty(window):
    assert window._downsample_waveform(
        np.array([]), [], [], [], [], [], []
    ) == {}


def test_downsample_waveform_limits_points(window):
    t = np.arange(0.0, 10.0, 0.001)
    ones = np.ones_like(t)
    data = window._downsample_waveform(t, ones, ones, ones, ones, ones, ones)
    assert len(data["t"]) <= 80
    assert data["t"][0] == 0.0
    assert data["setpoint"][0] == 1.0


def test_open_dialogs_accepted(window, monkeypatch):
    class FakeLLMDialog:
        def __init__(self, values, parent=None):
            self._values = values

        def exec(self):
            return QtWidgets.QDialog.DialogCode.Accepted

        def values(self):
            return {"base_url": "", "model": "", "api_key": "k3"}

    monkeypatch.setattr(mw, "LLMSettingsDialog", FakeLLMDialog)
    window._open_llm_settings_dialog()
    assert window.app_config.data["llm"]["api_key"] == "k3"
    assert window.app_config.data["llm"]["base_url"] == "https://api.deepseek.com"
    assert "已配置 Key" in window.llm_summary.text()

    class FakePlanDialog:
        def __init__(self, values, parent=None):
            self._values = values

        def exec(self):
            return QtWidgets.QDialog.DialogCode.Accepted

        def values(self):
            return {"plan": ["disturbance"], "loops": 3, "disturb_s": 2.0, "recover_s": 1.0}

    monkeypatch.setattr(mw, "AutotunePlanDialog", FakePlanDialog)
    window._open_plan_dialog()
    assert window.app_config.data["autotune"]["plan"] == ["disturbance"]
    assert "→ 3 轮" in window.ai_plan_summary.text()


def test_open_dialogs_rejected(window, monkeypatch):
    class RejectedDialog:
        def __init__(self, values, parent=None):
            self._values = values

        def exec(self):
            return QtWidgets.QDialog.DialogCode.Rejected

        def values(self):
            return {"api_key": "never"}

    monkeypatch.setattr(mw, "LLMSettingsDialog", RejectedDialog)
    monkeypatch.setattr(mw, "AutotunePlanDialog", RejectedDialog)
    window._open_llm_settings_dialog()
    window._open_plan_dialog()
    assert window.app_config.data["llm"]["api_key"] != "never"


def test_make_suggest_callable(window):
    window.app_config.data["llm"]["api_key"] = "test-key"
    suggest = window._make_suggest()
    assert callable(suggest)


def test_start_autotune_guards(qapp, tmp_path, modal_recorder):
    win = _make_window(qapp, tmp_path)
    try:
        win._start_autotune()
        assert modal_recorder[-1][1] == "未连接"

        win.link_combo.setCurrentIndex(win.link_combo.findData("mock"))
        win._connect()
        win.tuner._active = True
        win._start_autotune()
        assert win.tuner._suggest is None
        win.tuner._active = False

        win.app_config.data["llm"]["api_key"] = ""
        win._start_autotune()
        assert "缺少 API Key" in modal_recorder[-1][1]

        win._run_script(
            ExperimentScript("长实验", (ExperimentStep("wait", {"seconds": 60.0}),))
        )
        win._start_autotune()
        assert "实验运行中" in win.statusBar().currentMessage()
        win.runner.abort()

        win.app_config.data["llm"]["api_key"] = "k"

        def boom():
            raise RuntimeError("client boom")

        win._make_suggest = boom
        win._start_autotune()
        assert "AI 客户端初始化失败" in modal_recorder[-1][1]

        win._make_suggest = lambda: (lambda history, limits, setpoint: None)

        def start_boom(*args, **kwargs):
            raise RuntimeError("start boom")

        win.tuner.start = start_boom
        win._start_autotune()
        assert "启动失败" in modal_recorder[-1][1]
        assert win.ai_start_btn.isEnabled()
    finally:
        win._disconnect()
        win.close()


def test_start_autotune_and_abort(qapp, tmp_path):
    win = _make_window(qapp, tmp_path)
    try:
        win.link_combo.setCurrentIndex(win.link_combo.findData("mock"))
        win._connect()
        win.app_config.data["llm"]["api_key"] = "k"
        win.app_config.data["autotune"] = {
            "plan": ["step"],
            "loops": 1,
            "disturb_s": 1.0,
            "recover_s": 1.0,
        }
        win._ai_settle_s = 0.2
        win._ai_countdown_s = 0.2
        win.ai_measure_spin.setValue(0.5)
        win._make_suggest = lambda: (lambda history, limits, setpoint: None)
        win._start_autotune()
        assert win.tuner.active
        assert not win.ai_start_btn.isEnabled()
        assert win.ai_abort_btn.isEnabled()
        win._abort_autotune()
        assert not win.tuner.active
        assert win.ai_start_btn.isEnabled()
    finally:
        win._disconnect()
        win.close()


def test_abort_autotune_aborts_runner(mock_window):
    mock_window._run_script(
        ExperimentScript("长实验", (ExperimentStep("wait", {"seconds": 60.0}),))
    )
    assert mock_window.runner.active
    mock_window._abort_autotune()
    assert not mock_window.runner.active


def test_tuner_events_restore_and_finished(window):
    window._on_tuner_event("status", {"text": "正在请求 AI 建议…"})
    assert window.ai_status.text() == "正在请求 AI 建议…"
    window._on_tuner_event("apply_gains", {"kp": 1.0, "ki": 2.0, "kd": 0.3})
    assert "恢复最优参数" in window.ai_current_label.text()
    window._on_tuner_event("apply_gains", {"kp": 4.0, "ki": 60.0, "kd": 0.05, "round": 1, "total": 2, "kind": "step"})
    assert "第 1/2 轮" in window.ai_current_label.text()
    window._on_tuner_event("run_experiment", {"kind": "step"})
    assert "测试中" in window.ai_current_label.text()
    window._on_tuner_event("pause_motor", {})
    assert "等待 AI 建议" in window.ai_current_label.text()
    window._on_tuner_event("finished", {"reason": "完成", "iterations": 0, "best_gains": None, "best_score": None})
    assert window.ai_current_label.text() == "—"
    window._on_tuner_event(
        "finished",
        {
            "reason": "完成全部轮次",
            "iterations": 2,
            "best_gains": {"kp": 4.0, "ki": 60.0, "kd": 0.05},
            "best_score": 12.5,
        },
    )
    assert "最优参数" in window.ai_current_label.text()
    assert "12.50" in window.ai_current_label.text()


def test_tuner_iteration_log_rows(window):
    window._on_tuner_event(
        "iteration",
        {
            "iteration": 1,
            "kind": "step",
            "kp": 4.0,
            "ki": 60.0,
            "kd": 0.05,
            "score": 12.3,
            "overshoot_pct": 5.4,
            "settling_time": 0.75,
            "best": True,
        },
    )
    window._on_tuner_event(
        "iteration",
        {
            "iteration": 2,
            "kind": "disturbance",
            "kp": 5.0,
            "ki": 40.0,
            "kd": 0.1,
            "score": 23.1,
            "max_deviation": 0.32,
            "recovery_time": 1.35,
        },
    )
    assert window.ai_log.rowCount() == 2
    assert window.ai_log.item(0, 1).text() == "阶跃"
    assert window.ai_log.item(0, 8).text() == "当前最优"
    assert window.ai_log.item(1, 1).text() == "扰动"
    assert window.ai_log.item(1, 6).text() == "0.32"


def test_refresh_trials_without_workspace(qapp, tmp_path):
    win = _make_window(qapp, tmp_path, activate=False)
    try:
        assert win.workspace is None
        win._refresh_trials_table()
        assert win.trials_table.rowCount() == 0
    finally:
        win.close()


def test_export_selected_trial(window):
    window.workspace.append_trial(
        {
            "kind": "manual_step",
            "setpoint": 2.0,
            "gains": {"kp": 3.0, "ki": 30.0, "kd": 0.03},
            "metrics": {"overshoot_pct": 4.0},
        }
    )
    window.workspace.append_trial({"kind": "no_gains"})
    window._set_gain_spins(8.0, 80.0, 0.08)
    window._refresh_trials_table()
    assert window.trials_table.rowCount() == 2
    window.trials_table.selectRow(0)
    window._export_selected_trial()
    data = json.loads((window.workspace.exports_dir / "config.json").read_text(encoding="utf-8"))
    assert data["gains"] == {"kp": 8.0, "ki": 80.0, "kd": 0.08}
    assert data["source"] == "no_gains"
    assert "已导出" in window.statusBar().currentMessage()

    window.trials_table.selectRow(1)
    window._export_selected_trial()
    data = json.loads((window.workspace.exports_dir / "config.json").read_text(encoding="utf-8"))
    assert data["gains"] == {"kp": 3.0, "ki": 30.0, "kd": 0.03}
    assert data["source"] == "manual_step"
    assert data["setpoint"] == 2.0


def test_export_guards_without_workspace(qapp, tmp_path):
    win = _make_window(qapp, tmp_path, activate=False)
    try:
        win._export_selected_trial()
        assert "请先选择工作目录" in win.statusBar().currentMessage()
        win._export_current()
        assert "请先选择工作目录" in win.statusBar().currentMessage()
        win._code_preview_clicked()
        assert "请先选择工作目录" in win.statusBar().currentMessage()
    finally:
        win.close()


def test_export_no_selection(window):
    window._refresh_trials_table()
    window._export_selected_trial()
    assert "请先在历史列表中选择" in window.statusBar().currentMessage()


def test_export_failures_show_critical(window, monkeypatch, modal_recorder):
    window.workspace.append_trial({"kind": "x", "gains": {"kp": 1, "ki": 1, "kd": 1}})
    window._refresh_trials_table()
    window.trials_table.selectRow(0)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(mw, "export_artifacts", boom)
    window._export_selected_trial()
    assert modal_recorder[-1][0] == "critical"
    assert modal_recorder[-1][1] == "导出失败"
    window._export_current()
    assert modal_recorder[-1][0] == "critical"
    assert modal_recorder[-1][1] == "导出失败"


def test_export_current_uses_last_metrics(window):
    window._set_gain_spins(2.5, 25.0, 0.25)
    window._last_metrics = {"overshoot_pct": 9.9}
    window.sp_spin.setValue(1.5)
    window._export_current()
    data = json.loads((window.workspace.exports_dir / "config.json").read_text(encoding="utf-8"))
    assert data["gains"] == {"kp": 2.5, "ki": 25.0, "kd": 0.25}
    assert data["source"] == "current"
    assert data["setpoint"] == 1.5
    assert data["metrics"]["overshoot_pct"] == 9.9


def test_codeagent_settings_roundtrip(window):
    window.code_exe_edit.setText("C:/tools/opencode.exe")
    window.code_agent_edit.setText("pid-editor")
    window.code_timeout_spin.setValue(120)
    window._save_codeagent_settings()
    cfg = window.app_config.data["codeagent"]
    assert cfg["executable"] == "C:/tools/opencode.exe"
    assert cfg["agent_name"] == "pid-editor"
    assert cfg["timeout_s"] == 120
    assert "已保存" in window.statusBar().currentMessage()

    window.code_exe_edit.setText("")
    window.code_agent_edit.setText("")
    agent = window._make_code_agent()
    assert agent.executable == "opencode"
    assert agent.agent_name == ""
    assert agent.timeout == 120.0


def test_code_preview_guards(window, tmp_path):
    window.code_request_edit.setPlainText("把 Kp 改为 3")
    window._code_preview_clicked()
    assert "预检查未通过" in window.code_status.text()
    assert not window.code_run_btn.isEnabled()
    assert "不是 git 仓库" in window.code_console.toPlainText()


def test_code_preview_empty_request(window):
    window.code_request_edit.setPlainText("   ")
    window._code_preview_clicked()
    assert "请先填写修改需求" in window.statusBar().currentMessage()


def test_code_run_without_preview(window):
    window._code_prompt = ""
    window._code_run_clicked()
    assert "请先生成预览" in window.statusBar().currentMessage()


def test_code_run_while_active(window, tmp_path):
    repo = _make_repo(tmp_path)
    window._activate_workspace(repo)
    window.code_request_edit.setPlainText("改 Kp")
    window._code_preview_clicked()
    assert window.code_run_btn.isEnabled()
    started: list[int] = []
    window.code_session.start = lambda *args, **kwargs: started.append(1)
    window.code_session._thread = object()
    try:
        window._code_run_clicked()
    finally:
        window.code_session._thread = None
    assert started == []


def test_code_run_precheck_problems(window, tmp_path, modal_recorder):
    repo = _make_repo(tmp_path)
    window._activate_workspace(repo)
    window.code_request_edit.setPlainText("改 Kp")
    window._code_preview_clicked()
    (repo / "pid.c").write_text("float kp = 5.0f;\n", encoding="utf-8")
    window._code_run_clicked()
    assert modal_recorder[-1][0] == "warning"
    assert modal_recorder[-1][1] == "预检查未通过"
    assert not window.code_session.active


def test_code_run_agent_and_start_failures(window, tmp_path, modal_recorder):
    repo = _make_repo(tmp_path)
    window._activate_workspace(repo)
    window.code_request_edit.setPlainText("改 Kp")
    window._code_preview_clicked()

    def boom():
        raise RuntimeError("agent boom")

    window._make_code_agent = boom
    window._code_run_clicked()
    assert modal_recorder[-1][1] == "opencode 初始化失败"

    window._make_code_agent = lambda: object()

    def start_boom(*args, **kwargs):
        raise RuntimeError("start boom")

    window.code_session.start = start_boom
    window._code_run_clicked()
    assert modal_recorder[-1][1] == "启动失败"


def test_code_cancel_clicked(window):
    cancelled: list[int] = []
    window.code_session.cancel = lambda: cancelled.append(1)
    window.code_session._thread = object()
    try:
        window._code_cancel_clicked()
    finally:
        window.code_session._thread = None
    assert cancelled == [1]
    assert window.code_status.text() == "正在中止…"


def test_on_code_done_variants(window):
    outcome = CodeEditOutcome(
        workdir=window.workspace.root,
        prompt="p",
        exit_code=124,
        timed_out=True,
        cancelled=False,
        duration_s=12.5,
    )
    window._on_code_done(outcome)
    assert "超时中止" in window.code_status.text()
    assert "(没有检测到任何文件改动)" in window.code_diff.toPlainText()
    assert not window.code_keep_btn.isEnabled()
    assert window.code_rollback_btn.isEnabled()

    outcome = CodeEditOutcome(
        workdir=window.workspace.root,
        prompt="p",
        exit_code=-1,
        timed_out=False,
        cancelled=True,
        duration_s=0.2,
        error="boom",
    )
    window._on_code_done(outcome)
    assert "已取消" in window.code_status.text()
    assert "[错误] boom" in window.code_diff.toPlainText()

    outcome = CodeEditOutcome(
        workdir=window.workspace.root,
        prompt="p",
        exit_code=0,
        timed_out=False,
        cancelled=False,
        duration_s=0.3,
        diff_text="diff --git a/pid.c b/pid.c\n",
        new_files=["new.c"],
    )
    window._on_code_done(outcome)
    assert window.code_keep_btn.isEnabled()
    assert "新增文件:" in window.code_diff.toPlainText()
    assert "  + new.c" in window.code_diff.toPlainText()


def test_code_keep_and_rollback_guards(window):
    window._code_keep_clicked()
    window._code_rollback_clicked()
    assert not window.code_keep_btn.isEnabled()


def test_close_event_cancels_active_session(window):
    cancelled: list[int] = []
    window.code_session.cancel = lambda: cancelled.append(1)
    window.code_session._thread = object()
    window.close()
    assert cancelled == [1]


def test_tick_detects_closed_link(window):
    link = MockLink(DefaultProtocolAdapter())
    link._open = True
    window.link = link
    window._tick()
    assert window.lbl_rate.text() == "采样率: 0 Hz"
    assert "RX 0 B" in window.lbl_rx.text()

    link._open = False
    link.stats.last_error = "线缆断开"
    window._tick()
    assert window.link is None
    assert "链路已断开" in window.statusBar().currentMessage()


def test_tick_processes_link_events(mock_window):
    now = time.perf_counter()
    mock_window.link.events.put(LinkEvent(now, "gains", {"kp": 1.5, "ki": 2.5, "kd": 0.25}))
    mock_window.link.events.put(LinkEvent(now, "ack", {"acked": 0x10, "status": 7}))
    mock_window.link.events.put(LinkEvent(now, "text", {"text": "设备消息"}))
    mock_window._tick()
    assert mock_window.kp_spin.value() == 1.5
    assert mock_window.statusBar().currentMessage() == "设备消息"


def test_plot_clear_resets_reference(window):
    window._last_plot_t = 123.0
    window._clear_plots()
    assert window._last_plot_t is None


def test_banner_text_with_round_base(window):
    window._prompt_text = "提示"
    window.tuner._active = True
    window._ai_round_base = "第 1/1 轮"
    text = window._banner_text("（3 s）")
    assert text.startswith("当前测试参数")
    assert "提示（3 s）" in text
    window.tuner._active = False
    assert window._banner_text() == "提示"


def test_metrics_dict_and_rows(window):
    from pidtool.core.metrics import StepMetrics

    metrics = StepMetrics(
        rise_time=0.05,
        peak_time=0.2,
        overshoot_pct=5.0,
        settling_time=float("nan"),
        steady_error=0.001,
        tail_ripple=0.01,
        oscillating=False,
        samples=100,
    )
    rows = window._step_metric_rows(metrics)
    assert dict(rows)["调节时间 (±2%)"] == "—"
    data = window._step_metrics_dict(metrics)
    assert data["settling_time"] is None
    assert data["rise_time"] == 0.05
    window._set_metrics_rows(rows)
    assert window.metrics_table.rowCount() == len(rows)


def test_ensure_link_silent(window):
    assert not window._ensure_link(silent=True)
    assert not window._ensure_link()
