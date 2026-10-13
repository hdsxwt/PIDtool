from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from pidtool.codeagent import (
    AgentRunResult,
    CodeAgentError,
    CodeEditSession,
    OpencodeAgent,
    build_code_prompt,
)
from pidtool.codeagent import git_tools


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_tools.run_git(["init"], repo)
    (repo / "pid.c").write_text("float kp = 1.0f;\n", encoding="utf-8")
    git_tools.run_git(["add", "."], repo)
    git_tools.run_git(
        ["-c", "user.name=test", "-c", "user.email=test@test", "commit", "-m", "init"], repo
    )
    return repo


def _wait_for_done(session: CodeEditSession, timeout: float = 8.0) -> list[tuple[str, object]]:
    deadline = time.time() + timeout
    events: list[tuple[str, object]] = []
    while time.time() < deadline:
        events.extend(session.poll())
        if any(kind == "done" for kind, _ in events):
            return events
        time.sleep(0.01)
    raise AssertionError("session did not finish")


class StubAgent:
    name = "stub"

    def __init__(self, edits: dict[str, str], exit_code: int = 0) -> None:
        self.edits = edits
        self.exit_code = exit_code

    def build_command(self, prompt: str, workdir: Path) -> list[str]:
        return ["stub"]

    def run(self, prompt, workdir, on_line, should_stop) -> AgentRunResult:
        on_line("stub: working")
        for name, content in self.edits.items():
            (Path(workdir) / name).write_text(content, encoding="utf-8")
        on_line("stub: done")
        return AgentRunResult(
            exit_code=self.exit_code,
            timed_out=False,
            cancelled=False,
            output_tail=["stub: working", "stub: done"],
            duration_s=0.01,
        )


def test_precheck(tmp_path):
    assert CodeEditSession.precheck(tmp_path)
    repo = _make_repo(tmp_path)
    assert CodeEditSession.precheck(repo) == []
    (repo / "pid.c").write_text("float kp = 2.0f;\n", encoding="utf-8")
    problems = CodeEditSession.precheck(repo)
    assert problems and "未提交" in problems[0]


def test_session_flow_keep(tmp_path):
    repo = _make_repo(tmp_path)
    session = CodeEditSession()
    session.start(StubAgent({"pid.c": "float kp = 3.0f;\n"}), repo, "bump kp")
    events = _wait_for_done(session)
    assert any(kind == "started" for kind, _ in events)
    outputs = [payload for kind, payload in events if kind == "output"]
    assert "stub: working" in outputs
    outcome = session.outcome
    assert outcome is not None
    assert outcome.exit_code == 0
    assert "kp = 3.0f" in outcome.diff_text
    assert outcome.changed_files == ["pid.c"]
    assert outcome.new_files == []
    session.clear()
    assert session.outcome is None


def test_session_rollback(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "keep.txt").write_text("keep", encoding="utf-8")
    session = CodeEditSession()
    session.start(
        StubAgent({"pid.c": "float kp = 7.0f;\n", "extra.c": "int extra;\n"}),
        repo,
        "change",
    )
    _wait_for_done(session)
    outcome = session.outcome
    assert outcome is not None
    assert outcome.new_files == ["extra.c"]
    removed = session.rollback()
    assert removed == ["extra.c"]
    assert (repo / "pid.c").read_text(encoding="utf-8") == "float kp = 1.0f;\n"
    assert not (repo / "extra.c").exists()
    assert (repo / "keep.txt").exists()
    assert git_tools.tracked_changes(repo) == []
    assert outcome.reverted_files == ["extra.c"]


def test_build_code_prompt():
    text = build_code_prompt(
        "加入积分抗饱和",
        Path("C:/firmware"),
        {"kp": 1.0, "ki": 2.0, "kd": 0.1},
        {"overshoot_pct": 5.0, "oscillating": False},
    )
    assert "加入积分抗饱和" in text
    assert "Kp=1" in text
    assert "超调=5.00%" in text
    assert "PID 算法" in text


def test_opencode_build_command(tmp_path):
    agent = OpencodeAgent(executable=sys.executable, timeout=5)
    command = agent.build_command("hello", tmp_path)
    assert command[0] == sys.executable
    assert command[1:3] == ["run", "--dir"]
    assert command[3] == str(tmp_path)
    assert "--auto" in command
    assert command[-1] == "hello"

    named = OpencodeAgent(executable=sys.executable, agent_name="pid-editor")
    named_command = named.build_command("x", tmp_path)
    assert named_command[-3:-1] == ["--agent", "pid-editor"]

    manual = OpencodeAgent(executable=sys.executable, auto_approve=False)
    assert "--auto" not in manual.build_command("x", tmp_path)


def test_opencode_bat_wrapping(tmp_path):
    bat = tmp_path / "opencode.bat"
    bat.write_text('@echo off\n"opencode.exe" %*\n', encoding="utf-8")
    agent = OpencodeAgent(executable=str(bat))
    command = agent.build_command("x", tmp_path)
    assert command[0].lower().endswith("cmd.exe")
    assert command[1] == "/c"
    assert command[2] == str(bat)


def test_batch_without_arg_forwarding_rejected(tmp_path):
    bat = tmp_path / "opencode.bat"
    bat.write_text('"opencode.exe"\n', encoding="utf-8")
    agent = OpencodeAgent(executable=str(bat))
    with pytest.raises(CodeAgentError):
        agent.build_command("x", tmp_path)


def test_opencode_missing_executable(tmp_path):
    agent = OpencodeAgent(executable="definitely-not-a-real-exe-xyz")
    with pytest.raises(CodeAgentError):
        agent.build_command("x", tmp_path)


class FakeProcAgent(OpencodeAgent):
    def __init__(self, code: str, **kwargs) -> None:
        super().__init__(executable=sys.executable, **kwargs)
        self._code = code

    def build_command(self, prompt: str, workdir: Path) -> list[str]:
        return [sys.executable, "-u", "-c", self._code]


def test_run_streams_output(tmp_path):
    agent = FakeProcAgent("print('line1'); print('line2')", timeout=10)
    lines: list[str] = []
    result = agent.run("x", tmp_path, lines.append, lambda: False)
    assert result.exit_code == 0
    assert not result.timed_out and not result.cancelled
    assert "line1" in lines and "line2" in lines


def test_run_times_out(tmp_path):
    agent = FakeProcAgent("import time; print('line1'); time.sleep(10)", timeout=0.8)
    lines: list[str] = []
    result = agent.run("x", tmp_path, lines.append, lambda: False)
    assert result.timed_out
    assert "line1" in lines


def test_run_cancelled(tmp_path):
    agent = FakeProcAgent("import time; print('go'); time.sleep(10)", timeout=60)
    counter = {"n": 0}

    def should_stop() -> bool:
        counter["n"] += 1
        return counter["n"] > 3

    result = agent.run("x", tmp_path, lambda line: None, should_stop)
    assert result.cancelled
    assert not result.timed_out


class BlockingAgent:
    name = "blocking"

    def __init__(self, workdir_file: str) -> None:
        self.workdir_file = workdir_file

    def build_command(self, prompt, workdir):
        return ["blocking"]

    def run(self, prompt, workdir, on_line, should_stop) -> AgentRunResult:
        (Path(workdir) / self.workdir_file).write_text("x", encoding="utf-8")
        while not should_stop():
            time.sleep(0.01)
        return AgentRunResult(
            exit_code=0,
            timed_out=False,
            cancelled=True,
            output_tail=[],
            duration_s=0.1,
        )


class RaisingAgent:
    name = "raising"

    def build_command(self, prompt, workdir):
        return ["raising"]

    def run(self, prompt, workdir, on_line, should_stop):
        raise RuntimeError("agent exploded")


def test_precheck_missing_directory(tmp_path):
    assert CodeEditSession.precheck(tmp_path / "nope") == ["工作目录不存在"]


def test_build_code_prompt_without_context():
    text = build_code_prompt("只改 pid.c", Path("C:/firmware"))
    assert "只改 pid.c" in text
    assert "当前 PID 参数" not in text
    assert "最近一次实验指标" not in text


def test_build_code_prompt_full_metrics_summary():
    text = build_code_prompt(
        "调参",
        Path("C:/firmware"),
        metrics={
            "overshoot_pct": 5.0,
            "rise_time": 0.049,
            "settling_time": 0.75,
            "steady_error": 0.0003,
            "recovery_time": 1.234,
            "oscillating": True,
        },
    )
    assert "超调=5.00%" in text
    assert "上升=0.0490s" in text
    assert "调节=0.7500s" in text
    assert "稳态误差=+0.00030" in text
    assert "恢复=1.234s" in text
    assert "振荡=是" in text


def test_session_properties_and_rollback_without_outcome(tmp_path):
    session = CodeEditSession()
    assert not session.active
    assert session.workdir is None
    assert session.outcome is None
    assert session.rollback() == []
    session.clear()
    assert session.outcome is None


def test_session_rejects_start_while_active(tmp_path):
    repo = _make_repo(tmp_path)
    session = CodeEditSession()
    session.start(BlockingAgent("a.c"), repo, "one")
    try:
        with pytest.raises(RuntimeError):
            session.start(StubAgent({}), repo, "two")
    finally:
        session.cancel()
        _wait_for_done(session)
    assert not session.active
    assert session.outcome is not None
    assert session.outcome.cancelled


def test_session_restart_clears_previous_outcome(tmp_path):
    repo = _make_repo(tmp_path)
    session = CodeEditSession()
    session.start(StubAgent({"pid.c": "float kp = 2.0f;\n"}), repo, "one")
    _wait_for_done(session)
    assert session.outcome is not None
    session.start(StubAgent({"pid.c": "float kp = 3.0f;\n"}), repo, "two")
    events = _wait_for_done(session)
    assert any(kind == "started" for kind, _ in events)
    assert session.outcome is not None
    assert "kp = 3.0f" in session.outcome.diff_text


def test_session_captures_agent_exception(tmp_path):
    repo = _make_repo(tmp_path)
    session = CodeEditSession()
    session.start(RaisingAgent(), repo, "boom")
    _wait_for_done(session)
    outcome = session.outcome
    assert outcome is not None
    assert outcome.exit_code == -1
    assert "agent exploded" in outcome.error
    assert not outcome.timed_out and not outcome.cancelled
    session.clear()
    assert session.outcome is None


def test_session_clear_noop_while_active(tmp_path):
    repo = _make_repo(tmp_path)
    session = CodeEditSession()
    session.start(BlockingAgent("a.c"), repo, "keep")
    session.clear()
    assert session.active
    session.cancel()
    _wait_for_done(session)
    assert session.outcome is not None


def test_session_tracks_workdir(tmp_path):
    repo = _make_repo(tmp_path)
    session = CodeEditSession()
    session.start(StubAgent({}), repo, "noop")
    assert session.workdir == repo
    _wait_for_done(session)


def test_run_raises_code_agent_error(tmp_path):
    class MissingExeAgent(OpencodeAgent):
        def build_command(self, prompt, workdir):
            return ["definitely-not-a-real-exe-xyz"]

    agent = MissingExeAgent()
    with pytest.raises(CodeAgentError):
        agent.run("x", tmp_path, lambda line: None, lambda: False)


def test_run_trims_output_tail(tmp_path):
    agent = FakeProcAgent(
        "for i in range(450): print('line-%d' % i)", timeout=30
    )
    lines: list[str] = []
    result = agent.run("x", tmp_path, lines.append, lambda: False)
    assert result.exit_code == 0
    assert len(result.output_tail) == 400
    assert result.output_tail[-1] == "line-449"
    assert len(lines) == 450


def test_run_kill_after_wait_timeout(monkeypatch, tmp_path):
    class FakeProc:
        def __init__(self) -> None:
            self.stdout = iter(())
            self.killed = False

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 1)

    proc = FakeProc()
    monkeypatch.setattr(
        "pidtool.codeagent.agent.subprocess.Popen", lambda *args, **kwargs: proc
    )
    agent = OpencodeAgent(executable=sys.executable, timeout=5)
    result = agent.run("x", tmp_path, lambda line: None, lambda: False)
    assert result.exit_code == -1
    assert proc.killed


def test_batch_launcher_directory_is_tolerated(tmp_path):
    bat_dir = tmp_path / "launcher.bat"
    bat_dir.mkdir()
    agent = OpencodeAgent(executable=str(bat_dir))
    command = agent.build_command("x", tmp_path)
    assert command[1] == "/c"
    assert command[2] == str(bat_dir)
