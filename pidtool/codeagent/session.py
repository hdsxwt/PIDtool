from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path

from pidtool.codeagent import git_tools
from pidtool.codeagent.agent import CodeAgent

PROMPT_HEADER = [
    "你是嵌入式 PID 控制工程师，请只在这个固件工程的有限范围内进行修改。",
    "约束：",
    "1. 只修改与 PID 算法/控制环相关的源文件（例如 pid.c/pid.h 或等价模块）；不要改动构建系统、启动代码与无关外设模块",
    "2. 不要执行烧录/下载命令，不要修改 git 历史",
    "3. 保持与现有工程一致的代码风格",
    "4. 完成后用简短列表说明修改了哪些文件、为什么",
]


def build_code_prompt(
    request: str,
    workdir: Path,
    gains: dict | None = None,
    metrics: dict | None = None,
) -> str:
    lines = list(PROMPT_HEADER)
    lines.append("")
    lines.append(f"固件工程目录: {workdir}")
    if gains:
        lines.append(
            f"当前 PID 参数: Kp={gains.get('kp', 0):g} Ki={gains.get('ki', 0):g} Kd={gains.get('kd', 0):g}"
        )
    if metrics:
        summary = _metrics_summary(metrics)
        if summary:
            lines.append(f"最近一次实验指标: {summary}")
    lines.append("")
    lines.append("修改需求:")
    lines.append(request.strip())
    return "\n".join(lines)


def _metrics_summary(metrics: dict) -> str:
    parts = []
    if metrics.get("overshoot_pct") is not None:
        parts.append(f"超调={metrics['overshoot_pct']:.2f}%")
    if metrics.get("rise_time") is not None:
        parts.append(f"上升={metrics['rise_time']:.4f}s")
    if metrics.get("settling_time") is not None:
        parts.append(f"调节={metrics['settling_time']:.4f}s")
    if metrics.get("steady_error") is not None:
        parts.append(f"稳态误差={metrics['steady_error']:+.5f}")
    if metrics.get("recovery_time") is not None:
        parts.append(f"恢复={metrics['recovery_time']:.3f}s")
    if metrics.get("oscillating") is not None:
        parts.append(f"振荡={'是' if metrics['oscillating'] else '否'}")
    return " ".join(parts)


@dataclass
class CodeEditOutcome:
    workdir: Path
    prompt: str
    exit_code: int
    timed_out: bool
    cancelled: bool
    duration_s: float
    output_tail: list[str] = field(default_factory=list)
    diff_stat: str = ""
    diff_text: str = ""
    changed_files: list[str] = field(default_factory=list)
    new_files: list[str] = field(default_factory=list)
    reverted_files: list[str] = field(default_factory=list)
    error: str = ""


class CodeEditSession:
    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._workdir: Path | None = None
        self._prompt = ""
        self._before_untracked: set[str] = set()
        self._outcome: CodeEditOutcome | None = None

    @property
    def active(self) -> bool:
        return self._thread is not None

    @property
    def outcome(self) -> CodeEditOutcome | None:
        return self._outcome

    @property
    def workdir(self) -> Path | None:
        return self._workdir

    @staticmethod
    def precheck(workdir: Path) -> list[str]:
        problems: list[str] = []
        if not Path(workdir).is_dir():
            return ["工作目录不存在"]
        if not git_tools.is_repo(workdir):
            return ["工作目录不是 git 仓库：无法启用 diff 审查与回滚保护"]
        tracked = git_tools.tracked_changes(workdir)
        if tracked:
            preview = "、".join(rel for _, rel in tracked[:5])
            problems.append(f"git 存在未提交的已跟踪改动（{preview}），请先提交或还原")
        return problems

    def start(self, agent: CodeAgent, workdir: Path, prompt: str) -> None:
        if self.active:
            raise RuntimeError("已有代码修改任务在运行")
        if self.outcome is not None:
            self.clear()
        self._workdir = Path(workdir)
        self._prompt = prompt
        self._before_untracked = git_tools.untracked_files(self._workdir)
        self._cancel.clear()
        self._queue.put(("started", {"workdir": str(workdir), "prompt": prompt}))

        def work() -> None:
            try:
                result = agent.run(
                    prompt,
                    self._workdir,
                    lambda line: self._queue.put(("output", line)),
                    self._cancel.is_set,
                )
                outcome = CodeEditOutcome(
                    workdir=self._workdir,
                    prompt=prompt,
                    exit_code=result.exit_code,
                    timed_out=result.timed_out,
                    cancelled=result.cancelled,
                    duration_s=result.duration_s,
                    output_tail=list(result.output_tail),
                    diff_stat=git_tools.diff_stat(self._workdir),
                    diff_text=git_tools.diff_text(self._workdir),
                    changed_files=git_tools.changed_files(self._workdir),
                    new_files=sorted(git_tools.untracked_files(self._workdir) - self._before_untracked),
                    error=result.error,
                )
            except Exception as exc:
                outcome = CodeEditOutcome(
                    workdir=self._workdir,
                    prompt=prompt,
                    exit_code=-1,
                    timed_out=False,
                    cancelled=False,
                    duration_s=0.0,
                    error=str(exc),
                )
            self._outcome = outcome
            self._thread = None
            self._queue.put(("done", outcome))

        self._thread = threading.Thread(target=work, name="codeedit-session", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def poll(self, limit: int = 500) -> list[tuple[str, object]]:
        events: list[tuple[str, object]] = []
        while len(events) < limit:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events

    def rollback(self) -> list[str]:
        if self._outcome is None or self._workdir is None:
            return []
        removed = git_tools.revert(self._workdir, self._before_untracked)
        self._outcome.reverted_files = removed
        return removed

    def clear(self) -> None:
        if self.active:
            return
        self._outcome = None
