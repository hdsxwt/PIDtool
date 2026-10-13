from __future__ import annotations

import numpy as np

from pidtool.core.ringbuffer import SampleBuffer
from pidtool.link.protocol import Sample


def _sample(t: float, value: float = 0.0) -> Sample:
    return Sample(t, int(t * 1e6), value, value, value, value, value, value)


def _fill(buffer: SampleBuffer, count: int, dt: float = 0.1) -> None:
    for i in range(count):
        buffer.append([_sample(i * dt)])


def test_tail_span_selects_window():
    buffer = SampleBuffer(capacity=100)
    _fill(buffer, 50)
    data = buffer.tail_span(1.0)
    ts = data["t"]
    assert ts.size >= 10
    assert ts[-1] == 4.9
    assert ts[0] >= 3.9 - 1e-9
    assert ts[0] <= 4.0


def test_tail_span_wrap_around():
    buffer = SampleBuffer(capacity=10)
    _fill(buffer, 25)
    data = buffer.tail_span(0.35)
    assert list(np.round(data["t"], 6)) == [2.1, 2.2, 2.3, 2.4]


def test_tail_span_full_and_empty():
    buffer = SampleBuffer(capacity=10)
    assert buffer.tail_span(5.0)["t"].size == 0
    _fill(buffer, 3)
    data = buffer.tail_span(100.0)
    assert list(np.round(data["t"], 6)) == [0.0, 0.1, 0.2]


def test_tail_span_zero_returns_latest():
    buffer = SampleBuffer(capacity=10)
    _fill(buffer, 5)
    data = buffer.tail_span(0.0)
    assert list(np.round(data["t"], 6)) == [0.4]


def test_tail_span_matches_tail_for_full_history():
    buffer = SampleBuffer(capacity=100)
    _fill(buffer, 40)
    span = buffer.tail_span(1000.0)
    full = buffer.tail(40)
    for name in full:
        assert np.array_equal(span[name], full[name])


def test_tail_span_values_are_copies():
    buffer = SampleBuffer(capacity=10)
    _fill(buffer, 5, dt=1.0)
    data = buffer.tail_span(100.0)
    data["t"][0] = 123.0
    fresh = buffer.tail_span(100.0)
    assert fresh["t"][0] == 0.0


def test_empty_buffer_shapes_and_clear():
    buffer = SampleBuffer(capacity=8)
    assert buffer.total == 0
    assert buffer.span() == (0.0, 0.0)
    empty = buffer.tail(5)
    assert empty["t"].size == 0
    assert empty["tick_us"].size == 0
    for name in ("setpoint", "feedback", "u", "up", "ui", "ud"):
        assert empty[name].size == 0

    buffer.append([])
    assert buffer.total == 0

    _fill(buffer, 3)
    assert buffer.total == 3
    buffer.clear()
    assert buffer.total == 0
    assert buffer.span() == (0.0, 0.0)


def test_tail_zero_and_negative_counts():
    buffer = SampleBuffer(capacity=8)
    _fill(buffer, 3)
    assert buffer.tail(0)["t"].size == 0
    assert buffer.tail(-4)["t"].size == 0


def test_tail_returns_fields_in_order():
    buffer = SampleBuffer(capacity=8)
    buffer.append([Sample(1.0, 1000000, 1.5, 1.4, 2.0, 1.0, 0.5, 0.25)])
    data = buffer.tail(1)
    assert data["t"][0] == 1.0
    assert data["tick_us"][0] == 1000000
    assert data["setpoint"][0] == 1.5
    assert data["feedback"][0] == 1.4
    assert data["u"][0] == 2.0
    assert data["up"][0] == 1.0
    assert data["ui"][0] == 0.5
    assert data["ud"][0] == 0.25


def test_write_wraps_and_keeps_recent_values():
    buffer = SampleBuffer(capacity=4)
    _fill(buffer, 6)
    data = buffer.tail(4)
    assert list(np.round(data["t"], 6)) == [0.2, 0.3, 0.4, 0.5]
    assert buffer.total == 6
    assert buffer.span() == (0.2, 0.5)
