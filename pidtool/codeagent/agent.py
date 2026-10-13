from __future__ import annotations

import os
import queue
import re
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


def flatten_prompt(prompt: str) -> str:
    """Collapse a prompt to a single line for command-line transport.

    Batch launchers run through cmd.exe split arguments on newlines, so a
    multi-line prompt arrives truncated at the first line.
    """
    return " ".join(line.strip() for line in prompt.splitlines() if line.strip())


_SIMPLE_SHIM = re.compile(r'^\s*"([^"]+\.exe)"\s+%\*\s*$', re.IGNORECASE)


def _extract_shim_target(path: Path) -> str | None:
    """Return the .exe a simple forwarding batch shim points at, else None.

    Running the target directly avoids cmd.exe argument mangling
    (newline truncation, %VAR% expansion) for launchers like
    ``@echo off\\n"C:\\...\\opencode.exe" %*``.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    target: str | None = None
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.lower() in ("@echo off", "echo off"):
            continue
        if line.startswith("::") or line.lower().startswith("rem "):
            continue
        match = _SIMPLE_SHIM.match(line)
        if match is None or target is not None:
            return None
        target = match.group(1).strip()
    if not target:
        return None
    candidate = Path(target)
    if not candidate.is_absolute() or not candidate.is_file():
        return None
    return str(candidate)


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
        resolved_path = Path(resolved).resolve()
        if not resolved_path.exists():
            raise CodeAgentError(f"未找到 opencode 可执行文件: {self.executable}")
        resolved = str(resolved_path)
        if resolved.lower().endswith((".bat", ".cmd")):
            self._check_batch_forwards_args(Path(resolved))
            direct = _extract_shim_target(Path(resolved))
            if direct is not None:
                return [direct]
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
        command.append(flatten_prompt(prompt))
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
                if proc.stdout is None:
                    return
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
