from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class StepMetrics:
    rise_time: float
    peak_time: float
    overshoot_pct: float
    settling_time: float
    steady_error: float
    tail_ripple: float
    oscillating: bool
    samples: int


def analyze_step(
    t: np.ndarray,
    y: np.ndarray,
    setpoint: float,
    settle_band: float = 0.02,
    tail_fraction: float = 0.1,
    step_time: float | None = None,
) -> StepMetrics | None:
    t = np.asarray(t, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n = min(t.size, y.size)
    if n < 10:
        return None
    t = t[:n]
    y = y[:n]

    post = np.ones(n, dtype=bool)
    if step_time is not None:
        step_time = float(step_time)
        post = t >= step_time
        if not post.any():
            post = np.ones(n, dtype=bool)
        pre = y[t < step_time]
        if pre.size >= 3:
            lo = float(np.percentile(pre, 10.0))
            hi = float(np.percentile(pre, 90.0))
            y0 = 0.5 * (lo + hi)
        else:
            y0 = float(np.mean(y[: max(3, n // 20)]))
    else:
        y0 = float(np.mean(y[: max(3, n // 20)]))
    delta = float(setpoint) - y0
    scale = max(abs(float(setpoint)), abs(y0), 1e-9)
    if abs(delta) < max(0.005 * scale, 1e-9):
        return None

    z = (y - y0) / delta
    t0 = step_time if step_time is not None else float(t[0])

    def first_index(level: float) -> int | None:
        hit = np.flatnonzero((z >= level) & post)
        return int(hit[0]) if hit.size else None

    i10 = first_index(0.1)
    i90 = first_index(0.9)
    if i10 is not None and i90 is not None:
        rise = float(t[i90] - t[i10])
    else:
        rise = float("nan")

    imax = int(np.flatnonzero(post)[np.argmax(z[post])])
    zmax = float(z[imax])
    if zmax > 1.0:
        overshoot = (zmax - 1.0) * 100.0
        peak_time = float(t[imax] - t0)
    else:
        overshoot = 0.0
        peak_time = float("nan")

    tail_start = max(0, n - max(3, int(round(n * tail_fraction))))
    steady = float(np.mean(y[tail_start:]))
    steady_error = float(setpoint) - steady

    tol = settle_band * abs(delta)
    outside = np.flatnonzero(np.abs(y - setpoint) > tol)
    if outside.size == 0:
        settling = 0.0
    elif int(outside[-1]) < n - 1:
        settling = float(t[int(outside[-1]) + 1] - t0)
    else:
        settling = float("nan")

    tail = y[tail_start:]
    ripple = float((tail.max() - tail.min()) / 2.0)
    err_tail = z[tail_start:] - 1.0
    crossings = int(np.count_nonzero(np.diff(np.signbit(err_tail))))
    oscillating = bool(crossings >= 4 and ripple > tol)

    return StepMetrics(
        rise_time=rise,
        peak_time=peak_time,
        overshoot_pct=overshoot,
        settling_time=settling,
        steady_error=steady_error,
        tail_ripple=ripple,
        oscillating=oscillating,
        samples=n,
    )


@dataclass(frozen=True, slots=True)
class DisturbanceMetrics:
    max_deviation: float
    max_deviation_time: float
    recovery_time: float
    residual_error: float
    tail_ripple: float
    oscillating: bool
    samples: int


def analyze_disturbance(
    t: np.ndarray,
    y: np.ndarray,
    setpoint: float,
    disturb_start: float,
    disturb_end: float,
    band: float,
    tail_fraction: float = 0.1,
) -> DisturbanceMetrics | None:
    t = np.asarray(t, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n = min(t.size, y.size)
    if n < 10 or disturb_end <= disturb_start:
        return None
    t = t[:n]
    y = y[:n]
    error = y - setpoint

    mask = (t >= disturb_start) & (t <= disturb_end)
    if not mask.any():
        return None
    idxs = np.flatnonzero(mask)
    deviations = np.abs(error[idxs])
    peak = int(np.argmax(deviations))
    max_deviation = float(deviations[peak])
    max_deviation_time = float(t[idxs[peak]])

    after = np.flatnonzero(t > disturb_end)
    recovery = float("nan")
    if after.size:
        outside = np.flatnonzero(np.abs(error[after]) > band)
        if outside.size == 0:
            recovery = 0.0
        elif int(outside[-1]) < after.size - 1:
            pos = after[int(outside[-1]) + 1]
            recovery = float(t[pos] - disturb_end)

    tail_start = max(0, n - max(3, int(round(n * tail_fraction))))
    residual = float(setpoint - np.mean(y[tail_start:]))

    tail = y[tail_start:]
    ripple = float((tail.max() - tail.min()) / 2.0)
    err_tail = y[tail_start:] - setpoint
    crossings = int(np.count_nonzero(np.diff(np.signbit(err_tail))))
    oscillating = bool(crossings >= 4 and ripple > band)

    return DisturbanceMetrics(
        max_deviation,
        max_deviation_time,
        recovery,
        residual,
        ripple,
        oscillating,
        n,
    )


def rmse(y: np.ndarray, reference: float | np.ndarray) -> float:
    y = np.asarray(y, dtype=np.float64)
    ref = np.asarray(reference, dtype=np.float64)
    return float(np.sqrt(np.mean((y - ref) ** 2)))
