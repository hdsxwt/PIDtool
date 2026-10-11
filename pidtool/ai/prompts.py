from __future__ import annotations

SYSTEM_PROMPT = (
    "你是电机速度环 PID 调参专家。用户会给出若干轮阶跃响应实验结果，"
    "每轮包含 Kp/Ki/Kd 参数与响应指标（评分越低越好）。"
    "你的任务是根据历史表现提出下一组参数：在保证稳定（无持续振荡）的前提下，"
    "减小超调、缩短上升与调节时间、消除稳态误差。"
    "若某轮之后指标变差，应避免沿该方向继续变化。"
    "只输出 JSON，格式为 "
    '{"kp": 数字, "ki": 数字, "kd": 数字, "reasoning": "一句话理由"}，'
    "不要输出其他内容。"
)


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


def build_user_prompt(history: list[dict], limits: dict, setpoint: float) -> str:
    lines = [
        f"目标设定值: {setpoint:g}",
        "参数安全范围: "
        f"Kp∈[{limits.get('kp', {}).get('min')}, {limits.get('kp', {}).get('max')}], "
        f"Ki∈[{limits.get('ki', {}).get('min')}, {limits.get('ki', {}).get('max')}], "
        f"Kd∈[{limits.get('kd', {}).get('min')}, {limits.get('kd', {}).get('max')}]",
        "",
        "历史实验（评分越低越好）:",
    ]
    for entry in history:
        marker = "（当前最优）" if entry.get("best") else ""
        lines.append(
            f"第{entry.get('iteration', '?')}轮: "
            f"Kp={_fmt(entry.get('kp'), '.4g')} Ki={_fmt(entry.get('ki'), '.4g')} Kd={_fmt(entry.get('kd'), '.4g')} | "
            f"超调={_fmt(entry.get('overshoot_pct'), '.2f')}% "
            f"上升={_fmt(entry.get('rise_time'), '.4f')}s "
            f"调节={_fmt(entry.get('settling_time'), '.4f')}s "
            f"稳态误差={_fmt(entry.get('steady_error'), '+.5f')} "
            f"振荡={'是' if entry.get('oscillating') else '否'} "
            f"评分={_fmt(entry.get('score'), '.2f')}{marker}"
        )
    lines.append("")
    lines.append("请给出下一组 Kp/Ki/Kd，只输出 JSON。")
    return "\n".join(lines)
