from __future__ import annotations

import os
import pathlib
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets

from pidtool.app_config import AppConfig
from pidtool.codeagent import AgentRunResult, git_tools
from pidtool.ui.main_window import MainWindow


class StubAgent:
    name = "stub"

    def __init__(self, edits: dict[str, str]) -> None:
        self.edits = edits

    def build_command(self, prompt, workdir):
        return ["stub"]

    def run(self, prompt, workdir, on_line, should_stop) -> AgentRunResult:
        on_line("stub running")
        for name, content in self.edits.items():
            (pathlib.Path(workdir) / name).write_text(content, encoding="utf-8")
        return AgentRunResult(
            exit_code=0,
            timed_out=False,
            cancelled=False,
            output_tail=["stub running"],
            duration_s=0.01,
        )


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    yield app


def _wait_until(qapp, condition, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def _make_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_tools.run_git(["init"], repo)
    (repo / "pid.c").write_text("float kp = 1.0f;\n", encoding="utf-8")
    git_tools.run_git(["add", "."], repo)
    git_tools.run_git(
        ["-c", "user.name=test", "-c", "user.email=test@test", "commit", "-m", "init"], repo
    )
    return repo


def test_code_tab_flow_keep(qapp, tmp_path):
    repo = _make_repo(tmp_path)
    config = AppConfig(path=tmp_path / "app.toml")
    window = MainWindow(app_config=config)
    window._activate_workspace(repo)
    window.code_request_edit.setPlainText("把 Kp 默认值改为 3.0")
    window._code_preview_clicked()
    assert window.code_run_btn.isEnabled()
    assert "预检查" in window.code_console.toPlainText()

    window._make_code_agent = lambda: StubAgent({"pid.c": "float kp = 3.0f;\n"})
    window._code_run_clicked()
    assert window.code_session.active
    assert not window.code_preview_btn.isEnabled()
    assert _wait_until(
        qapp,
        lambda: not window.code_session.active
        and (window.code_keep_btn.isEnabled() or window.code_rollback_btn.isEnabled()),
    )
    assert window.code_keep_btn.isEnabled()
    assert "kp = 3.0f" in window.code_diff.toPlainText()
    window._code_keep_clicked()
    assert not window.code_keep_btn.isEnabled()
    trials = window.workspace.read_trials()
    assert trials[-1]["kind"] == "code_edit"
    assert trials[-1]["outcome"] == "kept"
    assert trials[-1]["changed_files"] == ["pid.c"]
    window.close()


def test_code_tab_flow_rollback(qapp, tmp_path):
    repo = _make_repo(tmp_path)
    config = AppConfig(path=tmp_path / "app.toml")
    window = MainWindow(app_config=config)
    window._activate_workspace(repo)
    window.code_request_edit.setPlainText("危险改动")
    window._code_preview_clicked()
    window._make_code_agent = lambda: StubAgent(
        {"pid.c": "float kp = 9.0f;\n", "new.c": "int x;\n"}
    )
    window._code_run_clicked()
    assert _wait_until(
        qapp,
        lambda: not window.code_session.active and window.code_rollback_btn.isEnabled(),
    )
    assert window.code_rollback_btn.isEnabled()
    window._code_rollback_clicked()
    assert (repo / "pid.c").read_text(encoding="utf-8") == "float kp = 1.0f;\n"
    assert not (repo / "new.c").exists()
    trials = window.workspace.read_trials()
    assert trials[-1]["kind"] == "code_edit"
    assert trials[-1]["outcome"] == "rolled_back"
    window.close()
