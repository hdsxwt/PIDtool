from __future__ import annotations

import numpy as np

from pidtool.core.metrics import analyze_disturbance, analyze_step, rmse


def _first_crossing(t: np.ndarray, y: np.ndarray, level: float) -> float | None:
    idx = np.flatnonzero(y >= level)
    return float(t[int(idx[0])]) if idx.size else None


def _first_order_step(t: np.ndarray, step_time: float, tau: float = 0.1) -> np.ndarray:
    y = np.zeros_like(t)
    mask = t >= step_time
    y[mask] = 1.0 - np.exp(-(t[mask] - step_time) / tau)
    return y


def test_first_order_step():
    tau = 0.1
    t = np.arange(0.0, 1.2, 0.001)
    y = _first_order_step(t, 0.2, tau)
    m = analyze_step(t, y, 1.0)
    assert m is not None
    assert abs(m.rise_time - tau * np.log(9.0)) < 0.01
    assert abs(m.settling_time - (0.2 + tau * np.log(50.0))) < 0.01
    assert m.overshoot_pct == 0.0
    assert abs(m.steady_error) < 1e-3
    assert not m.oscillating
    assert m.samples == t.size


def test_second_order_step():
    zeta, wn = 0.5, 10.0
    wd = wn * np.sqrt(1.0 - zeta**2)
    t = np.arange(0.0, 2.5, 0.0005)
    y = np.zeros_like(t)
    mask = t >= 0.5
    ts = t[mask] - 0.5
    y[mask] = 1.0 - np.exp(-zeta * wn * ts) * (
        np.cos(wd * ts) + (zeta / np.sqrt(1.0 - zeta**2)) * np.sin(wd * ts)
    )
    m = analyze_step(t, y, 1.0)
    assert m is not None
    expected_overshoot = np.exp(-zeta * np.pi / np.sqrt(1.0 - zeta**2)) * 100.0
    assert abs(m.overshoot_pct - expected_overshoot) < 1.0
    assert abs(m.peak_time - (0.5 + np.pi / wd)) < 0.005
    t10 = _first_crossing(t, y, 0.1)
    t90 = _first_crossing(t, y, 0.9)
    assert t10 is not None and t90 is not None
    assert abs(m.rise_time - (t90 - t10)) < 0.005
    assert 1.2 < m.settling_time < 1.5
    assert abs(m.steady_error) < 5e-3
    assert not m.oscillating


def test_sustained_oscillation_flagged():
    t = np.arange(0.0, 4.0, 0.001)
    y = np.zeros_like(t)
    mask = t >= 0.2
    ts = t[mask] - 0.2
    envelope = 1.0 - np.exp(-ts / 0.05)
    y[mask] = envelope * (1.0 + 0.2 * np.sin(2.0 * np.pi * 5.0 * ts + 0.3))
    m = analyze_step(t, y, 1.0)
    assert m is not None
    assert m.oscillating
    assert abs(m.tail_ripple - 0.2) < 0.02


def test_steady_error_detected():
    t = np.arange(0.0, 1.0, 0.001)
    y = np.zeros_like(t)
    mask = t >= 0.2
    y[mask] = 0.9 * (1.0 - np.exp(-(t[mask] - 0.2) / 0.05))
    m = analyze_step(t, y, 1.0)
    assert m is not None
    assert abs(m.steady_error - 0.1) < 5e-3


def test_noise_only_returns_none():
    rng = np.random.default_rng(0)
    t = np.arange(0.0, 1.0, 0.001)
    y = 1.0 + 0.002 * rng.standard_normal(t.size)
    assert analyze_step(t, y, 1.0) is None


def test_negative_step():
    tau = 0.1
    t = np.arange(0.0, 1.2, 0.001)
    y = np.zeros_like(t)
    mask = t >= 0.2
    y[mask] = -1.0 * (1.0 - np.exp(-(t[mask] - 0.2) / tau))
    m = analyze_step(t, y, -1.0)
    assert m is not None
    assert abs(m.rise_time - tau * np.log(9.0)) < 0.01
    assert abs(m.settling_time - (0.2 + tau * np.log(50.0))) < 0.01
    assert abs(m.steady_error) < 1e-3


def test_no_step_returns_none():
    t = np.arange(0.0, 1.0, 0.001)
    y = np.ones_like(t)
    assert analyze_step(t, y, 1.0) is None


def test_step_negligible_relative_to_scale_returns_none():
    t = np.arange(0.0, 1.0, 0.001)
    y = np.full_like(t, 1000.0)
    assert analyze_step(t, y, 1002.0) is None


def test_small_but_significant_step_is_valid():
    t = np.arange(0.0, 2.0, 0.001)
    y = np.full_like(t, 1000.0)
    mask = t >= 0.3
    y[mask] = 1010.0 - 10.0 * np.exp(-(t[mask] - 0.3) / 0.05)
    m = analyze_step(t, y, 1010.0)
    assert m is not None
    assert abs(m.rise_time - 0.05 * np.log(9.0)) < 0.01


def test_step_with_ringing_baseline_is_valid():
    t = np.arange(0.0, 4.0, 0.005)
    y = 400.0 + 200.0 * np.sin(2.0 * np.pi * 1.4 * t)
    mask = t >= 1.0
    y[mask] = 600.0 - 200.0 * np.exp(-(t[mask] - 1.0) / 0.08)

    m = analyze_step(t, y, 600.0, step_time=1.0)
    assert m is not None
    assert abs(m.rise_time - 0.08 * np.log(9.0)) < 0.01
    assert m.overshoot_pct == 0.0
    assert abs(m.steady_error) < 1e-6
    assert abs(m.settling_time - 0.08 * np.log(50.0)) < 0.02
    assert not m.oscillating


def test_rmse():
    y = np.array([1.0, 2.0, 3.0])
    assert abs(rmse(y, 1.0) - np.sqrt((0.0 + 1.0 + 4.0) / 3.0)) < 1e-12


def test_disturbance_recovery():
    t = np.arange(0.0, 8.0, 0.001)
    y = np.ones_like(t)
    during = (t >= 2.0) & (t < 3.0)
    y[during] = 1.0 - 0.3 * (t[during] - 2.0)
    after = t >= 3.0
    y[after] = 1.0 - 0.3 * np.exp(-(t[after] - 3.0) / 0.5)
    m = analyze_disturbance(t, y, 1.0, 2.0, 3.0, band=0.02)
    assert m is not None
    assert abs(m.max_deviation - 0.3) < 1e-3
    assert abs(m.max_deviation_time - 3.0) < 0.01
    assert abs(m.recovery_time - 0.5 * np.log(15.0)) < 0.02
    assert abs(m.residual_error) < 1e-3


def test_disturbance_no_recovery():
    t = np.arange(0.0, 6.0, 0.001)
    y = np.ones_like(t)
    y[t >= 2.0] = 0.7
    m = analyze_disturbance(t, y, 1.0, 2.0, 3.0, band=0.02)
    assert m is not None
    assert abs(m.max_deviation - 0.3) < 1e-6
    assert m.recovery_time != m.recovery_time
    assert abs(m.residual_error - 0.3) < 1e-3


def test_disturbance_invalid_window():
    t = np.arange(0.0, 4.0, 0.001)
    y = np.ones_like(t)
    assert analyze_disturbance(t, y, 1.0, 3.0, 2.0, band=0.02) is None


def test_disturbance_oscillation_flagged():
    t = np.arange(0.0, 6.0, 0.001)
    y = np.ones_like(t)
    after = t >= 3.0
    y[after] = 1.0 + 0.2 * np.sin(2.0 * np.pi * 5.0 * (t[after] - 3.0) + 0.3)
    m = analyze_disturbance(t, y, 1.0, 2.0, 3.0, band=0.02)
    assert m is not None
    assert m.oscillating
    assert abs(m.tail_ripple - 0.2) < 0.03


def test_step_too_few_samples_returns_none():
    t = np.arange(0.0, 0.009, 0.001)
    y = np.zeros_like(t)
    assert analyze_step(t, y, 1.0) is None


def test_step_mismatched_lengths_uses_shortest():
    t = np.arange(0.0, 2.0, 0.001)
    y = np.zeros(500)
    y[100:] = 1.0 - np.exp(-np.arange(400, dtype=float) / 50.0)
    m = analyze_step(t, y, 1.0)
    assert m is not None
    assert m.samples == 500


def test_step_nan_head_does_not_crash():
    t = np.arange(0.0, 2.0, 0.001)
    y = np.ones_like(t)
    y[0] = float("nan")
    m = analyze_step(t, y, 1.0)
    assert m is not None
    assert m.settling_time == 0.0


def test_disturbance_too_few_samples_returns_none():
    t = np.arange(0.0, 0.009, 0.001)
    y = np.ones_like(t)
    assert analyze_disturbance(t, y, 1.0, 0.002, 0.004, 0.02) is None


def test_disturbance_window_outside_data_returns_none():
    t = np.arange(0.0, 4.0, 0.001)
    y = np.ones_like(t)
    assert analyze_disturbance(t, y, 1.0, 10.0, 11.0, 0.02) is None


def test_rmse_array_reference():
    y = np.array([1.0, 2.0, 3.0])
    ref = np.array([1.0, 2.0, 4.0])
    assert abs(rmse(y, ref) - 1.0 / np.sqrt(3.0)) < 1e-12
