from __future__ import annotations

SYSTEM_PROMPT = (
    "你是电机速度环 PID 调参专家。用户按“轮”进行测试：每一轮包含若干依次执行的测试动作"
    "（阶跃或扰动，各动作有自己的目标值），一轮结束后统一给出各动作指标与轮评分，"
    "再请求下一组参数。轮评分 = 各动作评分的均值 + 方差（惩罚动作间表现不一致），"
    "评分越低越好；部分动作会附带降采样波形采样（t,sp,fb,u,up,ui,ud 逐行 CSV），"
    "可用于分析响应形状、超调形态与输出饱和。不同指标的评分已折算到可比量纲。"
    "你的任务是根据历史轮次提出下一组参数：在保证稳定（无持续振荡）的前提下，"
    "减小超调、缩短上升/调节/恢复时间、消除稳态误差与残余偏差。"
    "若某轮之后评分变差，应避免沿该方向继续变化。"
    "只输出 JSON，格式为 "
    '{"kp": 数字, "ki": 数字, "kd": 数字, "reasoning": "一句话理由"}，'
    "不要输出其他内容。"
)

WAVEFORM_COLUMNS = ("t", "setpoint", "feedback", "u", "up", "ui", "ud")


def _fmt(value, spec: str) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number or number in (float("inf"), float("-inf")):
        return "—"
    return format(number, spec)


def build_user_prompt(history: list[dict], limits: dict) -> str:
    lines = [
        "参数安全范围: "
        f"Kp∈[{limits.get('kp', {}).get('min')}, {limits.get('kp', {}).get('max')}], "
        f"Ki∈[{limits.get('ki', {}).get('min')}, {limits.get('ki', {}).get('max')}], "
        f"Kd∈[{limits.get('kd', {}).get('min')}, {limits.get('kd', {}).get('max')}]",
        "",
        "历史轮次（轮评分 = 动作评分均值 + 方差，越低越好）:",
    ]
    for entry in history:
        marker = "（当前最优）" if entry.get("best") else ""
        lines.append(
            f"第{entry.get('iteration', '?')}轮"
            f"（循环{entry.get('cycle', '?')} 第{entry.get('round_global', '?')}轮）: "
            f"Kp={_fmt(entry.get('kp'), '.4g')} Ki={_fmt(entry.get('ki'), '.4g')} "
            f"Kd={_fmt(entry.get('kd'), '.4g')} | "
            f"轮评分={_fmt(entry.get('score'), '.2f')}{marker}"
        )
        for index, action in enumerate(entry.get("actions", []), 1):
            if not isinstance(action, dict):
                continue
            kind = str(action.get("kind", "step"))
            target = _fmt(action.get("setpoint"), '.4g')
            if kind == "disturbance":
                lines.append(
                    f"  动作{index}（扰动 目标={target}）: "
                    f"最大偏差={_fmt(action.get('max_deviation'), '.4g')} "
                    f"恢复={_fmt(action.get('recovery_time'), '.4f')}s "
                    f"残余偏差={_fmt(action.get('residual_error'), '+.5f')} "
                    f"振荡={'是' if action.get('oscillating') else '否'} "
                    f"评分={_fmt(action.get('score'), '.2f')}"
                )
            else:
                lines.append(
                    f"  动作{index}（阶跃 目标={target}）: "
                    f"超调={_fmt(action.get('overshoot_pct'), '.2f')}% "
                    f"上升={_fmt(action.get('rise_time'), '.4f')}s "
                    f"调节={_fmt(action.get('settling_time'), '.4f')}s "
                    f"稳态误差={_fmt(action.get('steady_error'), '+.5f')} "
                    f"振荡={'是' if action.get('oscillating') else '否'} "
                    f"评分={_fmt(action.get('score'), '.2f')}"
                )
            waveform = action.get("waveform")
            if waveform:
                lines.append("    降采样波形 (每行: t,sp,fb,u,up,ui,ud):")
                rows = zip(*(waveform.get(column, []) for column in WAVEFORM_COLUMNS))
                for row in rows:
                    lines.append("    " + ",".join(_fmt(value, ".4g") for value in row))
    lines.append("")
    lines.append("请给出下一组 Kp/Ki/Kd，只输出 JSON。")
    return "\n".join(lines)
