from __future__ import annotations

VALID_KINDS = ("step", "disturbance")


def make_action(kind: str) -> dict:
    kind = kind if kind in VALID_KINDS else "step"
    if kind == "disturbance":
        return {
            "kind": "disturbance",
            "target_sp": 2.0,
            "settle_s": 2.0,
            "countdown_s": 3.0,
            "disturb_s": 5.0,
            "recover_s": 4.0,
        }
    return {
        "kind": "step",
        "baseline_sp": 0.0,
        "target_sp": 2.0,
        "settle_s": 2.0,
        "countdown_s": 3.0,
        "measure_s": 3.0,
    }


def normalize_action(raw: dict) -> dict:
    kind = str(raw.get("kind", "step"))
    action = make_action(kind)
    for key in tuple(action):
        if key == "kind" or key not in raw:
            continue
        try:
            action[key] = float(raw[key])
        except (TypeError, ValueError):
            continue
    return action


def normalize_rounds(raw_rounds) -> list[dict]:
    rounds: list[dict] = []
    for index, raw in enumerate(raw_rounds or []):
        if not isinstance(raw, dict):
            continue
        actions = [
            normalize_action(action)
            for action in raw.get("actions", [])
            if isinstance(action, dict)
        ]
        if not actions:
            continue
        name = str(raw.get("name") or f"轮次 {index + 1}")
        rounds.append({"name": name, "actions": actions})
    if not rounds:
        rounds = [{"name": "轮次 1", "actions": [make_action("step")]}]
    return rounds


def round_summary(round_spec: dict) -> str:
    labels = [
        "扰动" if str(action.get("kind")) == "disturbance" else "阶跃"
        for action in round_spec.get("actions", [])
    ]
    return "→".join(labels) if labels else "—"
