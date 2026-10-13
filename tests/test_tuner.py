from __future__ import annotations

import time
from dataclasses import replace

import pytest

from pidtool.ai.llm import Suggestion
from pidtool.ai.tuner import AutoTuner
from pidtool.core.metrics import DisturbanceMetrics, StepMetrics

LIMITS = {
    "kp": {"min": 0.0, "max": 100.0},
    "ki": {"min": 0.0, "max": 1000.0},
    "kd": {"min": 0.0, "max": 10.0},
}


def make_metrics(overshoot=5.0, settle=0.5, rise=0.1, sse=0.0, osc=False) -> StepMetrics:
    return StepMetrics(
        rise_time=rise,
        peak_time=0.2,
        overshoot_pct=overshoot,
        settling_time=settle,
        steady_error=sse,
        tail_ripple=0.01,
        oscillating=osc,
        samples=100,
    )


def make_disturbance(deviation=0.3, recovery=1.0, residual=0.001, osc=False) -> DisturbanceMetrics:
    return DisturbanceMetrics(
        max_deviation=deviation,
        max_deviation_time=100.0,
        recovery_time=recovery,
        residual_error=residual,
        tail_ripple=0.01,
        oscillating=osc,
        samples=500,
    )


def kinds(events):
    return [kind for kind, _ in events]


def test_iteration_flow_and_best_restore():
    events: list[tuple[str, dict]] = []
    suggestions = iter(
        [Suggestion(5.0, 40.0, 0.1, "try higher kp"), Suggestion(6.0, 30.0, 0.2, "try2")]
    )
    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=False)
    tuner.start(
        4.0,
        60.0,
        0.05,
        plan=["step"] * 3,
        loops=1,
        suggest=lambda h, l, sp: next(suggestions),
        limits=LIMITS,
        setpoint=2.0,
    )
    assert kinds(events) == ["status", "apply_gains", "run_experiment"]
    assert events[1][1]["kp"] == 4.0
    assert events[1][1]["ki"] == 60.0
    assert events[1][1]["kd"] == 0.05
    assert events[1][1]["round"] == 1
    assert events[1][1]["total"] == 3
    assert events[1][1]["kind"] == "step"

    tuner.on_trial(make_metrics(overshoot=8.0), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    tuner.tick()
    assert kinds(events)[-2:] == ["apply_gains", "run_experiment"]
    assert events[-1][0] == "run_experiment"
    assert events[-2][1]["kp"] == 5.0
    assert events[-2][1]["ki"] == 40.0
    assert events[-2][1]["kd"] == 0.1
    assert events[-2][1]["round"] == 2

    tuner.on_trial(make_metrics(overshoot=3.0, settle=0.4), {"kp": 5.0, "ki": 40.0, "kd": 0.1}, 2.0)
    tuner.tick()
    assert events[-2][1]["kp"] == 6.0

    tuner.on_trial(make_metrics(overshoot=15.0), {"kp": 6.0, "ki": 30.0, "kd": 0.2}, 2.0)
    assert not tuner.active
    assert kinds(events)[-3:] == ["apply_gains", "pause_motor", "finished"]
    assert events[-3][1]["kp"] == 5.0
    assert events[-3][1]["round"] == 4
    assert events[-3][1]["round"] > events[-3][1]["total"]
    finished = events[-1][1]
    assert finished["reason"] == "完成全部轮次"
    assert finished["iterations"] == 3
    assert finished["best_gains"] == {"kp": 5.0, "ki": 40.0, "kd": 0.1}

    iterations = [d for k, d in events if k == "iteration"]
    assert [d["iteration"] for d in iterations] == [1, 2, 3]
    assert iterations[1].get("best") is True
    assert "best" not in iterations[2]


def test_suggestion_clamped_to_limits():
    events: list[tuple[str, dict]] = []
    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=False)
    tuner.start(
        1.0,
        1.0,
        0.0,
        plan=["step"] * 2,
        loops=1,
        suggest=lambda h, l, sp: Suggestion(9999.0, -5.0, 1e9, ""),
        limits=LIMITS,
        setpoint=1.0,
    )
    tuner.on_trial(make_metrics(), {"kp": 1.0, "ki": 1.0, "kd": 0.0}, 1.0)
    tuner.tick()
    applies = [d for k, d in events if k == "apply_gains"]
    assert applies[-1]["kp"] == 100.0
    assert applies[-1]["ki"] == 0.0
    assert applies[-1]["kd"] == 10.0


def test_stops_after_two_oscillating_trials():
    events: list[tuple[str, dict]] = []
    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=False)
    tuner.start(
        4.0,
        60.0,
        0.05,
        plan=["step"] * 10,
        loops=1,
        suggest=lambda h, l, sp: Suggestion(5.0, 50.0, 0.1, ""),
        limits=LIMITS,
        setpoint=2.0,
    )
    tuner.on_trial(make_metrics(osc=True), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    tuner.tick()
    tuner.on_trial(make_metrics(osc=True), {"kp": 5.0, "ki": 50.0, "kd": 0.1}, 2.0)
    assert not tuner.active
    assert "振荡" in events[-1][1]["reason"]


def test_three_worse_trials_stop_early():
    events: list[tuple[str, dict]] = []
    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=False)
    tuner.start(
        4.0,
        60.0,
        0.05,
        plan=["step"] * 10,
        loops=1,
        suggest=lambda h, l, sp: Suggestion(5.0, 50.0, 0.1, ""),
        limits=LIMITS,
        setpoint=2.0,
    )
    tuner.on_trial(make_metrics(overshoot=2.0), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    for i in range(3):
        tuner.tick()
        tuner.on_trial(make_metrics(overshoot=10.0 + i), {"kp": 5.0, "ki": 50.0, "kd": 0.1}, 2.0)
    assert not tuner.active
    assert "退化" in events[-1][1]["reason"]


def test_abort_finishes():
    events: list[tuple[str, dict]] = []
    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=False)
    tuner.start(1.0, 1.0, 0.0, plan=["step"] * 5, loops=1, suggest=lambda h, l, sp: None, limits=LIMITS, setpoint=1.0)
    tuner.abort()
    assert not tuner.active
    assert events[-1][0] == "finished"
    assert events[-1][1]["reason"] == "用户中止"
    assert events[-1][1]["best_gains"] is None


def test_llm_error_finishes():
    events: list[tuple[str, dict]] = []
    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=False)

    def boom(history, limits, setpoint):
        raise RuntimeError("network down")

    tuner.start(4.0, 60.0, 0.05, plan=["step"] * 5, loops=1, suggest=boom, limits=LIMITS, setpoint=2.0)
    tuner.on_trial(make_metrics(), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    tuner.tick()
    assert not tuner.active
    assert "AI 调用失败" in events[-1][1]["reason"]


def test_async_request():
    events: list[tuple[str, dict]] = []

    def slow_suggest(history, limits, setpoint):
        time.sleep(0.05)
        return Suggestion(5.0, 40.0, 0.1, "")

    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=True)
    tuner.start(4.0, 60.0, 0.05, plan=["step"] * 5, loops=1, suggest=slow_suggest, limits=LIMITS, setpoint=2.0)
    tuner.on_trial(make_metrics(), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    deadline = time.time() + 3.0
    while time.time() < deadline and kinds(events).count("run_experiment") < 2:
        tuner.tick()
        time.sleep(0.01)
    assert kinds(events).count("run_experiment") == 2
    assert tuner.active
    tuner.abort()


def test_waveform_recorded_and_sent():
    events: list[tuple[str, dict]] = []
    captured: dict = {}

    def suggest(history, limits, setpoint):
        captured["history"] = history
        return Suggestion(5.0, 40.0, 0.1, "")

    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=False)
    tuner.start(4.0, 60.0, 0.05, plan=["step"] * 5, loops=1, suggest=suggest, limits=LIMITS, setpoint=2.0)
    waveform = {
        "t": [0.0, 0.01],
        "setpoint": [2.0, 2.0],
        "feedback": [1.0, 1.1],
        "u": [1.0, 2.0],
        "up": [1.0, 1.5],
        "ui": [0.0, 0.4],
        "ud": [0.0, 0.1],
    }
    tuner.on_trial(
        make_metrics(), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0, waveform=waveform
    )
    tuner.tick()
    tuner.on_trial(make_metrics(), {"kp": 5.0, "ki": 40.0, "kd": 0.1}, 2.0)
    tuner.tick()
    assert captured["history"][0]["waveform"]["t"] == [0.0, 0.01]
    assert "waveform" not in captured["history"][1]
    tuner.abort()


def test_mixed_plan_sequence_and_finish():
    events: list[tuple[str, dict]] = []
    suggestions = iter(
        [
            Suggestion(5.0, 40.0, 0.1, "a"),
            Suggestion(4.0, 60.0, 0.1, "b"),
            Suggestion(3.0, 80.0, 0.2, "c"),
        ]
    )
    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=False)
    tuner.start(
        4.0,
        60.0,
        0.05,
        plan=["step", "disturbance"],
        loops=2,
        suggest=lambda h, l, sp: next(suggestions),
        limits=LIMITS,
        setpoint=2.0,
        disturb_s=3.0,
        recover_s=2.5,
    )
    assert [d["kind"] for k, d in events if k == "run_experiment"] == ["step"]

    tuner.on_trial(make_metrics(), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0, kind="step")
    tuner.tick()
    payloads = [d for k, d in events if k == "run_experiment"]
    assert [d["kind"] for d in payloads] == ["step", "disturbance"]
    assert payloads[-1]["disturb_s"] == 3.0
    assert payloads[-1]["recover_s"] == 2.5

    tuner.on_trial(
        make_disturbance(), {"kp": 5.0, "ki": 40.0, "kd": 0.1}, 2.0, kind="disturbance"
    )
    tuner.tick()
    assert [d["kind"] for k, d in events if k == "run_experiment"] == [
        "step",
        "disturbance",
        "step",
    ]

    tuner.on_trial(
        make_metrics(overshoot=2.0), {"kp": 4.0, "ki": 60.0, "kd": 0.1}, 2.0, kind="step"
    )
    tuner.tick()
    tuner.on_trial(
        make_disturbance(deviation=0.2, recovery=0.5),
        {"kp": 3.0, "ki": 80.0, "kd": 0.2},
        2.0,
        kind="disturbance",
    )
    assert not tuner.active
    finished = events[-1][1]
    assert finished["reason"] == "完成全部轮次"
    assert finished["iterations"] == 4
    assert finished["best_gains"] is not None
    apply_payloads = [d for k, d in events if k == "apply_gains"]
    assert [d["round"] for d in apply_payloads] == [1, 2, 3, 4, 5]
    assert [d["kind"] for d in apply_payloads] == [
        "step",
        "disturbance",
        "step",
        "disturbance",
        "disturbance",
    ]
    assert apply_payloads[-1]["kp"] == 4.0
    iterations = [d for k, d in events if k == "iteration"]
    assert [d["kind"] for d in iterations] == ["step", "disturbance", "step", "disturbance"]
    assert "max_deviation" in iterations[1]
    assert "overshoot_pct" in iterations[2]


def test_unified_scoring_is_comparable():
    from pidtool.ai.scoring import score_metrics

    step_score = score_metrics("step", make_metrics(overshoot=10.0, settle=0.5, rise=0.1), 2.0)
    dist_score = score_metrics("disturbance", make_disturbance(deviation=0.2, recovery=0.5), 2.0)
    assert 0.0 < step_score < 100.0
    assert 0.0 < dist_score < 100.0
    assert score_metrics("disturbance", None, 2.0) == float("inf")


def test_pause_motor_events():
    events: list[tuple[str, dict]] = []
    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=False)
    tuner.start(
        4.0,
        60.0,
        0.05,
        plan=["step"] * 2,
        loops=1,
        suggest=lambda h, l, sp: Suggestion(5.0, 40.0, 0.1, ""),
        limits=LIMITS,
        setpoint=2.0,
    )
    assert kinds(events) == ["status", "apply_gains", "run_experiment"]

    tuner.on_trial(make_metrics(), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    event_kinds = kinds(events)
    pause_index = event_kinds.index("pause_motor")
    request_status_index = max(
        index for index, name in enumerate(event_kinds) if name == "status"
    )
    assert pause_index < request_status_index

    tuner.tick()
    tuner.on_trial(make_metrics(), {"kp": 5.0, "ki": 40.0, "kd": 0.1}, 2.0)
    assert not tuner.active
    assert kinds(events)[-2:] == ["pause_motor", "finished"]


def test_best_score_property():
    events: list[tuple[str, dict]] = []
    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=False)
    assert tuner.best_score is None
    tuner.start(
        1.0,
        1.0,
        0.0,
        plan=["step"] * 2,
        loops=1,
        suggest=lambda h, l, sp: Suggestion(2.0, 2.0, 0.0, ""),
        limits=LIMITS,
        setpoint=1.0,
    )
    tuner.on_trial(make_metrics(overshoot=4.0, settle=0.2, rise=0.05), {"kp": 1.0, "ki": 1.0, "kd": 0.0}, 1.0)
    assert tuner.best_score is not None
    assert tuner.best_score > 0.0
    tuner.abort()


def test_start_while_active_raises():
    tuner = AutoTuner(lambda k, d: None, async_mode=False)
    kwargs = dict(
        plan=["step"],
        loops=1,
        suggest=lambda h, l, sp: Suggestion(1.0, 1.0, 0.0, ""),
        limits=LIMITS,
        setpoint=1.0,
    )
    tuner.start(1.0, 1.0, 0.0, **kwargs)
    with pytest.raises(RuntimeError):
        tuner.start(1.0, 1.0, 0.0, **kwargs)
    tuner.abort()


def test_invalid_plan_and_loops_are_sanitized():
    events: list[tuple[str, dict]] = []
    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=False)
    tuner.start(
        1.0,
        1.0,
        0.0,
        plan=["nonsense", "step", "bogus"],
        loops=0,
        suggest=lambda h, l, sp: Suggestion(1.0, 1.0, 0.0, ""),
        limits=LIMITS,
        setpoint=1.0,
    )
    run_payloads = [d for k, d in events if k == "run_experiment"]
    assert run_payloads[0]["kind"] == "step"
    apply_payload = [d for k, d in events if k == "apply_gains"][0]
    assert apply_payload["total"] == 1
    tuner.abort()

    events.clear()
    tuner.start(
        1.0,
        1.0,
        0.0,
        plan=["bogus"],
        loops=1,
        suggest=lambda h, l, sp: Suggestion(1.0, 1.0, 0.0, ""),
        limits=LIMITS,
        setpoint=1.0,
    )
    assert any(k == "run_experiment" and d["kind"] == "step" for k, d in events)
    tuner.abort()


def test_on_trial_ignored_when_inactive():
    events: list[tuple[str, dict]] = []
    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=False)
    tuner.on_trial(make_metrics(), {"kp": 1.0, "ki": 1.0, "kd": 0.0}, 1.0)
    assert events == []
    tuner.tick()


def test_invalid_metrics_finish_tuning():
    events: list[tuple[str, dict]] = []
    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=False)
    tuner.start(
        1.0,
        1.0,
        0.0,
        plan=["step"] * 5,
        loops=1,
        suggest=lambda h, l, sp: Suggestion(1.0, 1.0, 0.0, ""),
        limits=LIMITS,
        setpoint=1.0,
    )
    tuner.on_trial(None, {"kp": 1.0, "ki": 1.0, "kd": 0.0}, 1.0)
    assert not tuner.active
    assert events[-1][0] == "finished"
    assert events[-1][1]["reason"] == "实验数据无效，停止调参"


def test_nan_suggestion_clamped_to_minimum():
    events: list[tuple[str, dict]] = []
    tuner = AutoTuner(lambda k, d: events.append((k, d)), async_mode=False)
    tuner.start(
        1.0,
        1.0,
        0.0,
        plan=["step"] * 2,
        loops=1,
        suggest=lambda h, l, sp: Suggestion(float("nan"), float("nan"), float("nan"), ""),
        limits=LIMITS,
        setpoint=1.0,
    )
    tuner.on_trial(make_metrics(), {"kp": 1.0, "ki": 1.0, "kd": 0.0}, 1.0)
    tuner.tick()
    apply_payload = [d for k, d in events if k == "apply_gains"][-1]
    assert apply_payload["kp"] == LIMITS["kp"]["min"]
    assert apply_payload["ki"] == LIMITS["ki"]["min"]
    assert apply_payload["kd"] == LIMITS["kd"]["min"]
    tuner.abort()


def test_scoring_penalties_and_none():
    from pidtool.ai.scoring import score_metrics

    assert score_metrics("step", None) == float("inf")
    assert score_metrics("disturbance", None, 1.0) == float("inf")
    assert score_metrics("unknown-kind", None) == float("inf")

    nan = float("nan")
    step = StepMetrics(
        rise_time=nan,
        peak_time=nan,
        overshoot_pct=0.0,
        settling_time=nan,
        steady_error=0.0,
        tail_ripple=0.0,
        oscillating=False,
        samples=10,
    )
    assert score_metrics("step", step) == 150.0
    step_osc = replace(step, oscillating=True)
    assert score_metrics("step", step_osc) == 250.0

    dist = DisturbanceMetrics(
        max_deviation=0.1,
        max_deviation_time=1.0,
        recovery_time=nan,
        residual_error=0.0,
        tail_ripple=0.0,
        oscillating=False,
        samples=10,
    )
    assert score_metrics("disturbance", dist, 1.0) == 110.0
    dist_osc = replace(dist, oscillating=True)
    assert score_metrics("disturbance", dist_osc, 1.0) == 210.0
