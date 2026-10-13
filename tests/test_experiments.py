from __future__ import annotations

import pytest

from pidtool.core.experiments import (
    ExperimentRunner,
    ExperimentScript,
    ExperimentStep,
    autotune_disturbance_script,
    autotune_step_script,
    disturbance_script,
    step_response_script,
)
from pidtool.core.experiments.specs import MARK_DISTURB_END, MARK_DISTURB_START


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make_runner(events, clock):
    return ExperimentRunner(lambda kind, data: events.append((kind, data)), clock=clock)


def test_step_script_event_sequence():
    clock = FakeClock()
    events: list[tuple[str, dict]] = []
    runner = make_runner(events, clock)
    script = ExperimentScript(
        "t",
        (
            ExperimentStep("command", {"cmd": "mode"}),
            ExperimentStep("wait", {"seconds": 1.0}),
            ExperimentStep("mark", {"label": "m"}),
            ExperimentStep("prompt", {"text": "hi", "countdown_s": 2.0, "manual": False}),
            ExperimentStep("collect_end", {"kind": "step"}),
        ),
    )
    runner.start(script)
    assert [k for k, _ in events] == ["started", "command", "wait"]
    assert runner.active

    clock.advance(0.5)
    runner.tick()
    assert [k for k, _ in events] == ["started", "command", "wait"]

    clock.advance(0.5)
    runner.tick()
    assert [k for k, _ in events][-2:] == ["mark", "prompt"]

    clock.advance(1.0)
    runner.tick()
    assert events[-1][0] == "prompt_tick"
    assert runner.active

    clock.advance(1.1)
    runner.tick()
    kinds = [k for k, _ in events]
    assert "prompt_done" in kinds
    assert "collect_end" in kinds
    assert kinds[-1] == "finished"
    assert not runner.active


def test_manual_prompt_requires_confirm():
    clock = FakeClock()
    events: list[tuple[str, dict]] = []
    runner = make_runner(events, clock)
    runner.start(
        ExperimentScript(
            "t",
            (
                ExperimentStep("prompt", {"text": "manual", "countdown_s": 0.0, "manual": True}),
                ExperimentStep("collect_end", {}),
            ),
        )
    )
    clock.advance(10.0)
    runner.tick()
    runner.tick()
    assert runner.active
    assert [k for k, _ in events] == ["started", "prompt"]
    runner.confirm()
    assert [k for k, _ in events] == [
        "started",
        "prompt",
        "prompt_done",
        "collect_end",
        "finished",
    ]
    assert not runner.active


def test_early_confirm_skips_countdown():
    clock = FakeClock()
    events: list[tuple[str, dict]] = []
    runner = make_runner(events, clock)
    runner.start(
        ExperimentScript(
            "t",
            (
                ExperimentStep("prompt", {"text": "c", "countdown_s": 5.0, "manual": False}),
                ExperimentStep("collect_end", {}),
            ),
        )
    )
    clock.advance(0.5)
    runner.tick()
    runner.confirm()
    kinds = [k for k, _ in events]
    assert kinds[-1] == "finished"
    assert not runner.active


def test_abort_stops_runner():
    clock = FakeClock()
    events: list[tuple[str, dict]] = []
    runner = make_runner(events, clock)
    runner.start(ExperimentScript("t", (ExperimentStep("wait", {"seconds": 5.0}),)))
    runner.abort()
    assert not runner.active
    assert events[-1][0] == "aborted"
    clock.advance(10.0)
    runner.tick()


def test_start_while_active_raises():
    clock = FakeClock()
    events: list[tuple[str, dict]] = []
    runner = make_runner(events, clock)
    script = ExperimentScript("t", (ExperimentStep("wait", {"seconds": 1.0}),))
    runner.start(script)
    with pytest.raises(RuntimeError):
        runner.start(script)
    runner.abort()


def test_builtin_scripts_structure():
    step = step_response_script(2.0)
    assert step.name == "阶跃响应"
    kinds = [s.kind for s in step.steps]
    assert kinds.index("prompt") < kinds.index("mark") < kinds.index("collect_end")
    assert kinds.index("collect_start") < kinds.index("prompt")

    dist = disturbance_script()
    assert dist.name == "扰动抑制"
    labels = [s.params.get("label") for s in dist.steps if s.kind == "mark"]
    assert labels == [MARK_DISTURB_START, MARK_DISTURB_END]
    manual = [s for s in dist.steps if s.kind == "prompt" and s.params.get("manual")]
    assert len(manual) == 1


def test_autotune_step_script_structure():
    script = autotune_step_script(2.0, baseline_sp=0.0)
    assert script.name == "自动调参阶跃"
    setpoints = [
        s.params.get("value")
        for s in script.steps
        if s.kind == "command" and s.params.get("cmd") == "setpoint"
    ]
    assert setpoints == [0.0, 2.0]
    kinds = [s.kind for s in script.steps]
    assert kinds.index("collect_start") < kinds.index("prompt") < kinds.index("mark")


def test_autotune_disturbance_script_structure():
    script = autotune_disturbance_script(2.0, disturb_s=3.0, recover_s=2.0)
    assert script.name == "自动调参扰动"
    setpoints = [
        s.params.get("value")
        for s in script.steps
        if s.kind == "command" and s.params.get("cmd") == "setpoint"
    ]
    assert setpoints == [2.0]
    kinds = [s.kind for s in script.steps]
    assert kinds.index("collect_start") < kinds.index("prompt") < kinds.index("mark")
    labels = [s.params.get("label") for s in script.steps if s.kind == "mark"]
    assert labels == [MARK_DISTURB_START, MARK_DISTURB_END]
    manual = [s for s in script.steps if s.kind == "prompt" and s.params.get("manual")]
    assert len(manual) == 1
    collect_end = [s for s in script.steps if s.kind == "collect_end"][0]
    assert collect_end.params["kind"] == "disturbance"
