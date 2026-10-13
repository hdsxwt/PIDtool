from __future__ import annotations

from pidtool.ai.plan import make_action, normalize_action, normalize_rounds, round_summary


def test_make_action_defaults():
    step = make_action("step")
    assert step["kind"] == "step"
    assert step["target_sp"] == 2.0
    assert step["baseline_sp"] == 0.0
    assert step["measure_s"] == 3.0
    assert "disturb_s" not in step

    disturbance = make_action("disturbance")
    assert disturbance["kind"] == "disturbance"
    assert disturbance["disturb_s"] == 5.0
    assert disturbance["recover_s"] == 4.0
    assert "baseline_sp" not in disturbance

    assert make_action("bogus")["kind"] == "step"


def test_normalize_action_fills_and_coerces():
    action = normalize_action(
        {"kind": "step", "target_sp": "12.5", "measure_s": None, "junk": 1}
    )
    assert action["target_sp"] == 12.5
    assert action["measure_s"] == 3.0
    assert "junk" not in action


def test_normalize_rounds_filters_and_defaults():
    rounds = normalize_rounds(
        [
            {"name": "A", "actions": [{"kind": "step", "target_sp": 1.0}, {"kind": "disturbance"}]},
            {"name": "empty", "actions": []},
            "garbage",
        ]
    )
    assert len(rounds) == 1
    assert rounds[0]["name"] == "A"
    assert [action["kind"] for action in rounds[0]["actions"]] == ["step", "disturbance"]

    default = normalize_rounds([])
    assert len(default) == 1
    assert default[0]["actions"][0]["kind"] == "step"
    assert default[0]["name"] == "轮次 1"


def test_round_summary():
    assert round_summary({"actions": [{"kind": "step"}, {"kind": "disturbance"}]}) == "阶跃→扰动"
    assert round_summary({"actions": [{"kind": "step"}]}) == "阶跃"
    assert round_summary({}) == "—"
