from __future__ import annotations

import time
from typing import Callable

from pidtool.core.experiments.specs import ExperimentScript


class ExperimentRunner:
    def __init__(
        self,
        on_event: Callable[[str, dict], None],
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._on_event = on_event
        self._clock = clock
        self._script: ExperimentScript | None = None
        self._index = 0
        self._state = "idle"
        self._deadline: float | None = None

    @property
    def active(self) -> bool:
        return self._script is not None

    @property
    def script_name(self) -> str:
        return self._script.name if self._script is not None else ""

    @property
    def step_index(self) -> int:
        return self._index

    def start(self, script: ExperimentScript) -> None:
        if self._script is not None:
            raise RuntimeError("已有实验在运行")
        self._script = script
        self._index = 0
        self._state = "idle"
        self._deadline = None
        self._on_event("started", {"name": script.name, "steps": len(script.steps)})
        self._enter_next()

    def abort(self) -> None:
        if self._script is None:
            return
        self._finish("aborted")

    def confirm(self) -> None:
        if self._script is None or self._state != "prompting":
            return
        self._on_event("prompt_done", {})
        self._advance()

    def tick(self) -> None:
        if self._script is None or self._state == "idle":
            return
        now = self._clock()
        if self._state == "waiting":
            if self._deadline is not None and now >= self._deadline:
                self._advance()
        elif self._state == "prompting":
            if self._deadline is not None:
                remaining = self._deadline - now
                if remaining <= 0.0:
                    self._on_event("prompt_done", {})
                    self._advance()
                else:
                    self._on_event("prompt_tick", {"remaining": remaining})

    def _advance(self) -> None:
        self._state = "idle"
        self._deadline = None
        self._index += 1
        self._enter_next()

    def _enter_next(self) -> None:
        while self._script is not None and self._index < len(self._script.steps):
            step = self._script.steps[self._index]
            if step.kind == "wait":
                self._state = "waiting"
                self._deadline = self._clock() + float(step.params.get("seconds", 0.0))
                self._on_event("wait", dict(step.params))
                return
            if step.kind == "prompt":
                self._state = "prompting"
                countdown = float(step.params.get("countdown_s", 0.0))
                self._deadline = self._clock() + countdown if countdown > 0.0 else None
                self._on_event(
                    "prompt",
                    {
                        "text": step.params.get("text", ""),
                        "manual": bool(step.params.get("manual", False)),
                        "countdown_s": countdown,
                    },
                )
                return
            self._on_event(step.kind, dict(step.params))
            self._index += 1
        self._finish("finished")

    def _finish(self, reason: str) -> None:
        name = self.script_name
        self._script = None
        self._state = "idle"
        self._index = 0
        self._deadline = None
        self._on_event(reason, {"name": name})
