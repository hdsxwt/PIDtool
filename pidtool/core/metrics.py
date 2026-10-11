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
) -> StepMetrics | None:
    t = np.asarray(t, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n = min(t.size, y.size)
    if n < 10:
        return None
    t = t[:n]
    y = y[:n]

    head = max(3, n // 20)
    y0 = float(np.mean(y[:head]))
    delta = float(setpoint) - y0
    if abs(delta) < 1e-9:
        return None

    z = (y - y0) / delta
    t0 = float(t[0])

    def first_index(level: float) -> int | None:
        hit = np.flatnonzero(z >= level)
        return int(hit[0]) if hit.size else None

    i10 = first_index(0.1)
    i90 = first_index(0.9)
    if i10 is not None and i90 is not None:
        rise = float(t[i90] - t[i10])
    else:
        rise = float("nan")

    imax = int(np.argmax(z))
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


def rmse(y: np.ndarray, reference: float | np.ndarray) -> float:
    y = np.asarray(y, dtype=np.float64)
    ref = np.asarray(reference, dtype=np.float64)
    return float(np.sqrt(np.mean((y - ref) ** 2)))
