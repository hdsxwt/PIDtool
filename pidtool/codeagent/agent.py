from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol


class CodeAgentError(RuntimeError):
    pass


@dataclass
class AgentRunResult:
    exit_code: int
    timed_out: bool
    cancelled: bool
    output_tail: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    error: str = ""


OnLine = Callable[[str], None]
ShouldStop = Callable[[], bool]


class CodeAgent(Protocol):
    name: str

    def build_command(self, prompt: str, workdir: Path) -> list[str]: ...

    def run(
        self, prompt: str, workdir: Path, on_line: OnLine, should_stop: ShouldStop
    ) -> AgentRunResult: ...


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


class OpencodeAgent:
    name = "opencode"

    def __init__(
        self,
        executable: str = "opencode",
        extra_args: list[str] | None = None,
        agent_name: str = "",
        auto_approve: bool = True,
        timeout: float = 900.0,
    ) -> None:
        self.executable = executable
        self.extra_args = list(extra_args or [])
        self.agent_name = agent_name
        self.auto_approve = auto_approve
        self.timeout = float(timeout)

    def resolve_executable(self) -> list[str]:
        resolved = shutil.which(self.executable) or self.executable
        if not Path(resolved).exists():
            raise CodeAgentError(f"未找到 opencode 可执行文件: {self.executable}")
        if resolved.lower().endswith((".bat", ".cmd")):
            self._check_batch_forwards_args(Path(resolved))
            return [os.environ.get("COMSPEC", "cmd.exe"), "/c", resolved]
        return [resolved]

    @staticmethod
    def _check_batch_forwards_args(path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if "%*" in text or any(f"%{i}" in text for i in range(1, 10)):
            return
        raise CodeAgentError(
            f"批处理启动器不转发命令行参数: {path}\n"
            "通过它执行会忽略 run/--dir 等参数（可能直接进入 TUI）。\n"
            "请在“可执行文件”中改填 opencode.cmd 或 opencode.exe 的完整路径。"
        )

    def build_command(self, prompt: str, workdir: Path) -> list[str]:
        command = self.resolve_executable() + ["run", "--dir", str(workdir)]
        if self.auto_approve:
            command.append("--auto")
        if self.agent_name:
            command += ["--agent", self.agent_name]
        command += self.extra_args
        command.append(prompt)
        return command

    def run(
        self, prompt: str, workdir: Path, on_line: OnLine, should_stop: ShouldStop
    ) -> AgentRunResult:
        command = self.build_command(prompt, workdir)
        env = dict(os.environ)
        env.setdefault("OPENCODE_DISABLE_AUTOUPDATE", "1")
        start = time.perf_counter()
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(workdir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            raise CodeAgentError(f"启动 opencode 失败: {exc}") from exc

        lines: queue.Queue[str | None] = queue.Queue()

        def reader() -> None:
            try:
                assert proc.stdout is not None
                for raw in proc.stdout:
                    lines.put(raw.rstrip("\r\n"))
            finally:
                lines.put(None)

        threading.Thread(target=reader, name="codeagent-reader", daemon=True).start()

        tail: list[str] = []
        timed_out = False
        cancelled = False
        while True:
            if should_stop():
                cancelled = True
                proc.kill()
                break
            if time.perf_counter() - start > self.timeout:
                timed_out = True
                proc.kill()
                break
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                break
            tail.append(line)
            if len(tail) > 400:
                tail = tail[-400:]
            on_line(line)

        try:
            exit_code = proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            exit_code = -1

        return AgentRunResult(
            exit_code=exit_code,
            timed_out=timed_out,
            cancelled=cancelled,
            output_tail=tail,
            duration_s=time.perf_counter() - start,
        )
