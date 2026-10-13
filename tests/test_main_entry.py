from __future__ import annotations

import os
import runpy
import subprocess
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets

from pidtool.app_config import AppConfig
from pidtool.ui import main_window as mw


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    yield app


def test_package_dunder_main_calls_entry(monkeypatch):
    called: list[int] = []
    monkeypatch.setattr(mw, "main", lambda: called.append(1))
    runpy.run_module("pidtool.__main__", run_name="__main__")
    assert called == [1]


def test_main_function_with_fake_qt(qapp, monkeypatch):
    calls: dict = {}

    class FakeApp:
        def __init__(self, argv):
            calls["argv"] = list(argv)

        def setStyle(self, style):
            calls["style"] = style

        def exec(self):
            calls["exec"] = True
            return 0

        def quit(self):
            calls["quit"] = True

    class FakeWindow:
        def show(self):
            calls["shown"] = True

        def close(self):
            calls["closed"] = True

    def single_shot(ms, fn):
        calls["smoke_ms"] = ms

    monkeypatch.setattr(mw, "QtWidgets", types.SimpleNamespace(QApplication=FakeApp))
    monkeypatch.setattr(
        mw, "QtCore", types.SimpleNamespace(QTimer=types.SimpleNamespace(singleShot=single_shot))
    )
    monkeypatch.setattr(mw, "MainWindow", FakeWindow)
    monkeypatch.setenv("PIDTOOL_SMOKE_EXIT", "0.5")

    with pytest.raises(SystemExit) as excinfo:
        mw.main()
    assert excinfo.value.code == 0
    assert calls["style"] == "Fusion"
    assert calls["shown"] and calls["closed"] and calls["exec"]
    assert calls["smoke_ms"] == 500


def test_main_window_module_executed_as_script(qapp, monkeypatch):
    class FakeApp:
        def __init__(self, argv):
            pass

        def setStyle(self, style):
            pass

        def exec(self):
            return 0

    monkeypatch.setattr(QtWidgets, "QApplication", FakeApp)
    monkeypatch.setattr(AppConfig, "load", lambda self: None)
    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    monkeypatch.delenv("PIDTOOL_SMOKE_EXIT", raising=False)

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_module("pidtool.ui.main_window", run_name="__main__")
    assert excinfo.value.code == 0


def test_package_runs_as_real_process(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PIDTOOL_SMOKE_EXIT"] = "0.3"
    env["APPDATA"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "pidtool"],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
