from __future__ import annotations

from pidtool.core.metrics import StepMetrics


def score_metrics(metrics: StepMetrics | None) -> float:
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
