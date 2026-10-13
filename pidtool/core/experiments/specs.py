from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MARK_STEP = "阶跃起点"
MARK_DISTURB_START = "扰动开始"
MARK_DISTURB_END = "扰动结束"


@dataclass(frozen=True, slots=True)
class ExperimentStep:
    kind: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExperimentScript:
    name: str
    steps: tuple[ExperimentStep, ...]


def step_response_script(
    target_sp: float,
    settle_s: float = 2.0,
    countdown_s: float = 3.0,
    measure_s: float = 4.0,
) -> ExperimentScript:
    return ExperimentScript(
        "阶跃响应",
        (
            ExperimentStep("command", {"cmd": "mode", "mode": "run"}),
            ExperimentStep("wait", {"seconds": settle_s}),
            ExperimentStep("collect_start", {"lookback": 0.5}),
            ExperimentStep(
                "prompt",
                {
                    "text": f"{countdown_s:.0f} 秒后下发阶跃到 {target_sp:g}",
                    "countdown_s": countdown_s,
                    "manual": False,
                },
            ),
            ExperimentStep("mark", {"label": MARK_STEP}),
            ExperimentStep("command", {"cmd": "setpoint", "value": target_sp}),
            ExperimentStep("wait", {"seconds": measure_s}),
            ExperimentStep("collect_end", {"kind": "step"}),
        ),
    )


def autotune_step_script(
    target_sp: float,
    baseline_sp: float = 0.0,
    settle_s: float = 2.0,
    countdown_s: float = 3.0,
    measure_s: float = 4.0,
) -> ExperimentScript:
    return ExperimentScript(
        "自动调参阶跃",
        (
            ExperimentStep("command", {"cmd": "mode", "mode": "run"}),
            ExperimentStep("command", {"cmd": "setpoint", "value": baseline_sp}),
            ExperimentStep("wait", {"seconds": settle_s}),
            ExperimentStep("collect_start", {"lookback": 0.5}),
            ExperimentStep(
                "prompt",
                {
                    "text": f"{countdown_s:.0f} 秒后下发阶跃 {baseline_sp:g} → {target_sp:g}",
                    "countdown_s": countdown_s,
                    "manual": False,
                },
            ),
            ExperimentStep("mark", {"label": MARK_STEP}),
            ExperimentStep("command", {"cmd": "setpoint", "value": target_sp}),
            ExperimentStep("wait", {"seconds": measure_s}),
            ExperimentStep("collect_end", {"kind": "step"}),
        ),
    )


def disturbance_script(
    disturb_s: float = 5.0,
    settle_s: float = 2.0,
    countdown_s: float = 3.0,
    recover_s: float = 4.0,
) -> ExperimentScript:
    return ExperimentScript(
        "扰动抑制",
        (
            ExperimentStep("command", {"cmd": "mode", "mode": "run"}),
            ExperimentStep("wait", {"seconds": settle_s}),
            ExperimentStep("collect_start", {"lookback": 0.5}),
            ExperimentStep(
                "prompt",
                {
                    "text": f"{countdown_s:.0f} 秒后开始用手给电机施加阻力，坚持 {disturb_s:g} 秒",
                    "countdown_s": countdown_s,
                    "manual": False,
                },
            ),
            ExperimentStep("mark", {"label": MARK_DISTURB_START}),
            ExperimentStep("wait", {"seconds": disturb_s}),
            ExperimentStep(
                "prompt",
                {"text": "请停止施加阻力，然后点击确认", "countdown_s": 0.0, "manual": True},
            ),
            ExperimentStep("mark", {"label": MARK_DISTURB_END}),
            ExperimentStep("wait", {"seconds": recover_s}),
            ExperimentStep("collect_end", {"kind": "disturbance"}),
        ),
    )


def autotune_disturbance_script(
    target_sp: float,
    settle_s: float = 2.0,
    countdown_s: float = 3.0,
    disturb_s: float = 5.0,
    recover_s: float = 4.0,
) -> ExperimentScript:
    return ExperimentScript(
        "自动调参扰动",
        (
            ExperimentStep("command", {"cmd": "mode", "mode": "run"}),
            ExperimentStep("command", {"cmd": "setpoint", "value": target_sp}),
            ExperimentStep("wait", {"seconds": settle_s}),
            ExperimentStep("collect_start", {"lookback": 0.5}),
            ExperimentStep(
                "prompt",
                {
                    "text": f"{countdown_s:.0f} 秒后开始用手给电机施加阻力，坚持 {disturb_s:g} 秒",
                    "countdown_s": countdown_s,
                    "manual": False,
                },
            ),
            ExperimentStep("mark", {"label": MARK_DISTURB_START}),
            ExperimentStep("wait", {"seconds": disturb_s}),
            ExperimentStep(
                "prompt",
                {"text": "请停止施加阻力，然后点击确认", "countdown_s": 0.0, "manual": True},
            ),
            ExperimentStep("mark", {"label": MARK_DISTURB_END}),
            ExperimentStep("wait", {"seconds": recover_s}),
            ExperimentStep("collect_end", {"kind": "disturbance"}),
        ),
    )
