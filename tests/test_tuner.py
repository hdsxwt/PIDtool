from __future__ import annotations

import time

from pidtool.ai.llm import Suggestion
from pidtool.ai.plan import make_action
from pidtool.ai.scoring import aggregate_round_score, score_metrics
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


def step_round(target=2.0, name="轮次 1") -> dict:
    action = make_action("step")
    action["target_sp"] = target
    return {"name": name, "actions": [action]}


def mixed_round(name="混合轮") -> dict:
    return {"name": name, "actions": [make_action("step"), make_action("disturbance")]}


def kinds(events):
    return [kind for kind, _ in events]


def start_tuner(events, rounds, cycles=1, suggest=None):
    tuner = AutoTuner(lambda kind, data: events.append((kind, data)), async_mode=False)
    tuner.start(
        4.0,
        60.0,
        0.05,
        rounds=rounds,
        cycles=cycles,
        suggest=suggest or (lambda history, limits: Suggestion(5.0, 40.0, 0.1, "")),
        limits=LIMITS,
    )
    return tuner


def test_single_action_round_flow_and_best_restore():
    events: list[tuple[str, dict]] = []
    suggestions = iter([Suggestion(5.0, 40.0, 0.1, "a"), Suggestion(6.0, 30.0, 0.2, "b")])
    tuner = start_tuner(
        events, rounds=[step_round()] * 3, cycles=1, suggest=lambda h, l: next(suggestions)
    )
    assert kinds(events) == ["status", "apply_gains", "run_experiment"]
    first = events[1][1]
    assert first["kp"] == 4.0
    assert first["round_global"] == 1
    assert first["round_total"] == 3
    assert first["restore"] is False
    assert first["action_total"] == 1
    payload = events[2][1]
    assert payload["action"]["kind"] == "step"
    assert payload["action_index"] == 1

    tuner.on_trial(make_metrics(overshoot=8.0), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    iterations = [d for k, d in events if k == "iteration"]
    assert len(iterations) == 1
    assert iterations[0]["iteration"] == 1
    assert abs(iterations[0]["score"] - score_metrics("step", make_metrics(overshoot=8.0), 2.0)) < 1e-9
    assert len(iterations[0]["actions"]) == 1
    tuner.tick()
    assert kinds(events)[-2:] == ["apply_gains", "run_experiment"]
    assert events[-2][1]["kp"] == 5.0
    assert events[-2][1]["restore"] is False
    assert events[-2][1]["round_global"] == 2

    tuner.on_trial(make_metrics(overshoot=3.0, settle=0.4), {"kp": 5.0, "ki": 40.0, "kd": 0.1}, 2.0)
    tuner.tick()
    assert events[-2][1]["kp"] == 6.0

    tuner.on_trial(make_metrics(overshoot=15.0), {"kp": 6.0, "ki": 30.0, "kd": 0.2}, 2.0)
    assert not tuner.active
    assert kinds(events)[-3:] == ["apply_gains", "pause_motor", "finished"]
    restore = events[-3][1]
    assert restore["kp"] == 5.0
    assert restore["restore"] is True
    assert restore["action_kinds"] == []
    finished = events[-1][1]
    assert finished["reason"] == "完成全部轮次"
    assert finished["rounds"] == 3
    assert finished["best_gains"] == {"kp": 5.0, "ki": 40.0, "kd": 0.1}
    all_iterations = [d for k, d in events if k == "iteration"]
    assert len(all_iterations) == 3
    assert all_iterations[1].get("best") is True


def test_multi_action_round_requests_ai_once():
    events: list[tuple[str, dict]] = []
    captured: list[list[dict]] = []

    def suggest(history, limits):
        captured.append(history)
        return Suggestion(5.0, 40.0, 0.1, "")

    tuner = start_tuner(events, rounds=[mixed_round()], cycles=2, suggest=suggest)
    runs = [d for k, d in events if k == "run_experiment"]
    assert len(runs) == 1
    assert runs[0]["action_index"] == 1
    assert runs[0]["action_total"] == 2
    assert runs[0]["action"]["kind"] == "step"
    assert runs[0]["round_global"] == 1
    assert runs[0]["round_total"] == 2

    tuner.on_trial(make_metrics(), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    tuner.tick()
    runs = [d for k, d in events if k == "run_experiment"]
    assert len(runs) == 2
    assert runs[1]["action"]["kind"] == "disturbance"
    assert runs[1]["action_index"] == 2
    assert "iteration" not in kinds(events)
    assert "pause_motor" not in kinds(events)
    assert captured == []

    tuner.on_trial(make_disturbance(), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    assert "pause_motor" in kinds(events)
    iterations = [d for k, d in events if k == "iteration"]
    assert len(iterations) == 1
    assert [a["kind"] for a in iterations[0]["actions"]] == ["step", "disturbance"]
    assert [a["setpoint"] for a in iterations[0]["actions"]] == [2.0, 2.0]

    tuner.tick()
    assert len(captured) == 1
    assert len(captured[0]) == 1
    assert len(captured[0][0]["actions"]) == 2
    runs = [d for k, d in events if k == "run_experiment"]
    assert [d["action"]["kind"] for d in runs] == ["step", "disturbance", "step"]

    tuner.on_trial(make_metrics(), {"kp": 5.0, "ki": 40.0, "kd": 0.1}, 2.0)
    tuner.on_trial(make_disturbance(deviation=0.2), {"kp": 5.0, "ki": 40.0, "kd": 0.1}, 2.0)
    assert not tuner.active
    assert len(captured) == 1
    finished = events[-1][1]
    assert finished["reason"] == "完成全部轮次"
    assert finished["rounds"] == 2


def test_round_score_is_mean_plus_variance():
    events: list[tuple[str, dict]] = []
    tuner = start_tuner(events, rounds=[mixed_round()], cycles=1)
    tuner.on_trial(make_metrics(overshoot=4.0), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    tuner.on_trial(make_disturbance(), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    iteration = [d for k, d in events if k == "iteration"][0]
    expected = aggregate_round_score(
        [
            score_metrics("step", make_metrics(overshoot=4.0), 2.0),
            score_metrics("disturbance", make_disturbance(), 2.0),
        ]
    )
    assert abs(iteration["score"] - expected) < 1e-9


def test_suggestion_clamped_to_limits():
    events: list[tuple[str, dict]] = []
    tuner = start_tuner(
        events,
        rounds=[step_round()] * 2,
        suggest=lambda h, l: Suggestion(9999.0, -5.0, 1e9, ""),
    )
    tuner.on_trial(make_metrics(), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    tuner.tick()
    applies = [d for k, d in events if k == "apply_gains"]
    assert applies[-1]["kp"] == 100.0
    assert applies[-1]["ki"] == 0.0
    assert applies[-1]["kd"] == 10.0


def test_oscillation_stops_after_two_rounds():
    events: list[tuple[str, dict]] = []
    tuner = start_tuner(events, rounds=[step_round()] * 4, cycles=1)
    tuner.on_trial(make_metrics(osc=True), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    tuner.tick()
    tuner.on_trial(make_metrics(osc=True), {"kp": 5.0, "ki": 40.0, "kd": 0.1}, 2.0)
    assert not tuner.active
    assert "振荡" in events[-1][1]["reason"]


def test_three_worse_rounds_stop_early():
    events: list[tuple[str, dict]] = []
    tuner = start_tuner(events, rounds=[step_round()] * 6, cycles=1)
    tuner.on_trial(make_metrics(overshoot=2.0), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    for index in range(3):
        tuner.tick()
        tuner.on_trial(
            make_metrics(overshoot=10.0 + index), {"kp": 5.0, "ki": 40.0, "kd": 0.1}, 2.0
        )
    assert not tuner.active
    assert "退化" in events[-1][1]["reason"]


def test_invalid_action_stops_immediately():
    events: list[tuple[str, dict]] = []
    tuner = start_tuner(events, rounds=[mixed_round()], cycles=2)
    tuner.on_trial(None, {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    assert not tuner.active
    assert "数据无效" in events[-1][1]["reason"]
    assert "iteration" not in kinds(events)
    runs = [d for k, d in events if k == "run_experiment"]
    assert len(runs) == 1


def test_abort_finishes():
    events: list[tuple[str, dict]] = []
    tuner = start_tuner(events, rounds=[step_round()] * 3, cycles=1)
    tuner.abort()
    assert not tuner.active
    assert events[-1][0] == "finished"
    assert events[-1][1]["reason"] == "用户中止"
    assert events[-1][1]["best_gains"] is None


def test_llm_error_finishes():
    events: list[tuple[str, dict]] = []
    tuner = start_tuner(events, rounds=[step_round()] * 3, cycles=1)
    tuner._suggest = lambda h, l: (_ for _ in ()).throw(RuntimeError("network down"))
    tuner.on_trial(make_metrics(), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    tuner.tick()
    assert not tuner.active
    assert "AI 调用失败" in events[-1][1]["reason"]


def test_async_request():
    events: list[tuple[str, dict]] = []

    def slow_suggest(history, limits):
        time.sleep(0.05)
        return Suggestion(5.0, 40.0, 0.1, "")

    tuner = start_tuner(events, rounds=[step_round()] * 3, cycles=1, suggest=slow_suggest)
    tuner.on_trial(make_metrics(), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    deadline = time.time() + 3.0
    while time.time() < deadline and kinds(events).count("run_experiment") < 2:
        tuner.tick()
        time.sleep(0.01)
    assert kinds(events).count("run_experiment") == 2
    assert tuner.active
    tuner.abort()


def test_waveform_recorded_per_action():
    events: list[tuple[str, dict]] = []
    captured: list[list[dict]] = []
    tuner = start_tuner(
        events,
        rounds=[mixed_round()],
        cycles=2,
        suggest=lambda h, l: (captured.append(h), Suggestion(5.0, 40.0, 0.1, ""))[1],
    )
    waveform = {
        "t": [0.0, 0.01],
        "setpoint": [2.0, 2.0],
        "feedback": [1.0, 1.1],
        "u": [1.0, 2.0],
        "up": [1.0, 1.5],
        "ui": [0.0, 0.4],
        "ud": [0.0, 0.1],
    }
    tuner.on_trial(make_metrics(), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0, waveform=waveform)
    tuner.on_trial(make_disturbance(), {"kp": 4.0, "ki": 60.0, "kd": 0.05}, 2.0)
    tuner.tick()
    actions = captured[0][0]["actions"]
    assert actions[0]["waveform"]["t"] == [0.0, 0.01]
    assert "waveform" not in actions[1]
    tuner.abort()


def test_pause_motor_ordering():
    events: list[tuple[str, dict]] = []
    tuner = start_tuner(events, rounds=[step_round()] * 2, cycles=1)
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


def test_normalization_inside_start():
    events: list[tuple[str, dict]] = []
    tuner = start_tuner(events, rounds=[{"actions": []}], cycles=0)
    assert "共 1 轮" in events[0][1]["text"]
    assert tuner.active
    tuner.abort()


def test_restart_while_active_raises():
    events: list[tuple[str, dict]] = []
    tuner = start_tuner(events, rounds=[step_round()], cycles=1)
    try:
        try:
            tuner.start(
                1.0,
                1.0,
                0.0,
                rounds=[step_round()],
                cycles=1,
                suggest=lambda h, l: Suggestion(1.0, 1.0, 0.0, ""),
                limits=LIMITS,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError")
    finally:
        tuner.abort()


def test_inactive_on_trial_is_noop():
    events: list[tuple[str, dict]] = []
    tuner = start_tuner(events, rounds=[step_round()], cycles=1)
    tuner.abort()
    before = len(events)
    tuner.on_trial(make_metrics(), {"kp": 1.0, "ki": 1.0, "kd": 0.0}, 2.0)
    tuner.tick()
    assert len(events) == before
