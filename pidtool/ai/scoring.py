from __future__ import annotations

from pidtool.core.metrics import DisturbanceMetrics, StepMetrics


def score_metrics(
    kind: str,
    metrics: StepMetrics | DisturbanceMetrics | None,
    setpoint: float = 1.0,
) -> float:
    if kind == "disturbance":
        return _score_disturbance(metrics, setpoint)
    return _score_step(metrics)


def aggregate_round_score(scores: list[float]) -> float:
    values = [float(value) for value in scores]
    if not values:
        return float("inf")
    if any(value == float("inf") for value in values):
        return float("inf")
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return mean + variance


def _score_step(metrics: StepMetrics | None) -> float:
    if metrics is None:
        return float("inf")
    score = float(metrics.overshoot_pct)
    if metrics.settling_time == metrics.settling_time:
        score += metrics.settling_time * 10.0
    else:
        score += 100.0
    if metrics.rise_time == metrics.rise_time:
        score += metrics.rise_time * 5.0
    else:
        score += 50.0
    score += abs(metrics.steady_error) * 500.0
    if metrics.oscillating:
        score += 100.0
    return score


def _score_disturbance(metrics: DisturbanceMetrics | None, setpoint: float) -> float:
    if metrics is None:
        return float("inf")
    scale = max(abs(float(setpoint)), 0.1)
    score = 100.0 * metrics.max_deviation / scale
    if metrics.recovery_time == metrics.recovery_time:
        score += metrics.recovery_time * 10.0
    else:
        score += 100.0
    score += abs(metrics.residual_error) / scale * 500.0
    if metrics.oscillating:
        score += 100.0
    return float(score)
