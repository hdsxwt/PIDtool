from __future__ import annotations

import time

from pidtool.link.mock_link import MockLink
from pidtool.link.protocol import (
    CMD_GET_GAINS,
    CMD_SET_GAINS,
    CMD_SET_MODE,
    CMD_SET_SETPOINT,
    CMD_START_TELEMETRY,
    CMD_STOP_TELEMETRY,
    MODE_IDLE,
    MODE_RUN,
    DefaultProtocolAdapter,
    encode_frame,
)


def test_command_handling_updates_state_and_acks():
    link = MockLink(DefaultProtocolAdapter(), control_hz=1000.0)
    adapter = link.adapter

    link.send(adapter.encode_set_gains(1.5, 2.5, 0.25))
    assert (link.kp, link.ki, link.kd) == (1.5, 2.5, 0.25)

    link.send(adapter.encode_set_setpoint(3.25))
    assert abs(link.setpoint - 3.25) < 1e-6

    link._integral = 1.0
    link.send(adapter.encode_set_mode(MODE_RUN))
    assert link.mode == MODE_RUN
    assert link._integral == 1.0
    link.send(adapter.encode_set_mode(MODE_IDLE))
    assert link.mode == MODE_IDLE
    assert link._integral == 0.0

    link.send(adapter.encode_start_telemetry(20))
    assert link.telemetry_enabled
    assert link.telemetry_period_ms == 20

    link.send(adapter.encode_stop_telemetry())
    assert not link.telemetry_enabled

    link.kp, link.ki, link.kd = 7.0, 8.0, 9.0
    link.send(adapter.encode_get_gains())
    events = link.drain_events()
    gains_events = [event for event in events if event.kind == "gains"]
    assert len(gains_events) == 1
    assert gains_events[0].data == {"kp": 7.0, "ki": 8.0, "kd": 9.0}

    acks = [event for event in events if event.kind == "ack"]
    assert [event.data["acked"] for event in acks] == [
        CMD_SET_GAINS,
        CMD_SET_SETPOINT,
        CMD_SET_MODE,
        CMD_SET_MODE,
        CMD_START_TELEMETRY,
        CMD_STOP_TELEMETRY,
    ]
    assert all(event.data["status"] == 0 for event in acks)


def test_malformed_and_unknown_commands_ignored():
    link = MockLink(DefaultProtocolAdapter(), control_hz=1000.0)
    for msg_type, payload in (
        (CMD_SET_GAINS, b"\x01"),
        (CMD_SET_SETPOINT, b"\x01\x02"),
        (CMD_SET_MODE, b""),
        (CMD_START_TELEMETRY, b"\x01"),
        (0x7E, b"x"),
    ):
        link.send(encode_frame(msg_type, payload))
    assert (link.kp, link.ki, link.kd) == (4.0, 60.0, 0.05)
    assert abs(link.setpoint - 1.0) < 1e-9
    assert link.mode == MODE_RUN
    assert link.telemetry_period_ms == 5
    assert link.drain_events() == []


def test_step_skips_telemetry_when_disabled_or_off_tick():
    link = MockLink(DefaultProtocolAdapter(), control_hz=1000.0)
    link.telemetry_enabled = False
    link._step()
    assert link._tick == 1
    assert link.buffer.total == 0

    link.telemetry_enabled = True
    link.telemetry_period_ms = 10
    link._step()
    assert link.buffer.total == 0
    link._tick = 9
    link._step()
    assert link.buffer.total == 1
    assert link.buffer.tail(1)["t"].size == 1


def test_step_zero_period_emits_every_tick():
    link = MockLink(DefaultProtocolAdapter(), control_hz=1000.0)
    link.telemetry_period_ms = 0
    link._step()
    link._step()
    assert link.buffer.total == 2
    assert link.stats.frames == 2


def test_step_idle_mode_zeroes_integral_and_output():
    link = MockLink(DefaultProtocolAdapter(), control_hz=1000.0)
    link.telemetry_period_ms = 0
    link.mode = MODE_IDLE
    link._integral = 2.0
    link._step()
    assert link._integral == 0.0
    tail = link.buffer.tail(1)
    assert tail["u"][0] == 0.0
    assert tail["up"][0] == 0.0
    assert link.stats.frames == 1


def test_run_loop_catches_up_after_slow_step():
    link = MockLink(DefaultProtocolAdapter(), control_hz=1000.0)
    link._open = True
    calls: list[int] = []

    def slow_step() -> None:
        calls.append(1)
        if len(calls) == 1:
            time.sleep(0.6)
        if len(calls) >= 2:
            link._open = False

    link._step = slow_step
    link._run()
    assert len(calls) >= 2


def test_open_runs_closed_loop_and_close_stops():
    link = MockLink(DefaultProtocolAdapter(), control_hz=2000.0)
    link.telemetry_period_ms = 0
    link.open()
    deadline = time.time() + 2.0
    while time.time() < deadline and link.buffer.total < 20:
        time.sleep(0.01)
    assert link.buffer.total >= 20
    span = link.buffer.span()
    assert span[1] > span[0]
    link.close()
    assert not link.is_open
    time.sleep(0.05)
    total = link.buffer.total
    time.sleep(0.1)
    assert link.buffer.total == total


def test_anti_windup_limits_integral():
    link = MockLink(DefaultProtocolAdapter(), control_hz=1000.0)
    link.telemetry_enabled = False
    link.setpoint = 100.0
    for _ in range(500):
        link._step()
    assert abs(link._integral) <= link.integral_limit + 1e-9
    assert abs(link._omega2) <= link.u_limit + 1e-9


def test_command_decoder_accepts_split_frames():
    link = MockLink(DefaultProtocolAdapter(), control_hz=1000.0)
    frame = link.adapter.encode_set_setpoint(2.5)
    link.send(frame[:3])
    assert abs(link.setpoint - 1.0) < 1e-9
    link.send(frame[3:])
    assert abs(link.setpoint - 2.5) < 1e-6
