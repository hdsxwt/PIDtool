from __future__ import annotations

import random
import struct
import threading
import time

from pidtool.link.protocol import (
    ACK_OK,
    ACK_STRUCT,
    CMD_GET_GAINS,
    CMD_SET_GAINS,
    CMD_SET_MODE,
    CMD_SET_SETPOINT,
    CMD_START_TELEMETRY,
    CMD_STOP_TELEMETRY,
    GAINS_STRUCT,
    MODE_IDLE,
    MODE_RUN,
    MSG_ACK,
    MSG_GAINS,
    MSG_TELEMETRY,
    TELEMETRY_STRUCT,
    FrameDecoder,
    ProtocolAdapter,
    encode_frame,
)
from pidtool.link.serial_link import LinkBase


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class MockLink(LinkBase):
    def __init__(
        self,
        adapter: ProtocolAdapter,
        control_hz: float = 1000.0,
        tau1: float = 0.08,
        tau2: float = 0.02,
        noise: float = 0.002,
    ) -> None:
        super().__init__(adapter)
        self.control_hz = float(control_hz)
        self.dt = 1.0 / self.control_hz
        self.tau1 = float(tau1)
        self.tau2 = float(tau2)
        self.noise = float(noise)
        self.kp = 4.0
        self.ki = 60.0
        self.kd = 0.05
        self.setpoint = 1.0
        self.mode = MODE_RUN
        self.telemetry_enabled = True
        self.telemetry_period_ms = 5
        self.u_limit = 5.0
        self.integral_limit = 4.0
        self.load = 0.0
        self._omega1 = 0.0
        self._omega2 = 0.0
        self._feedback = 0.0
        self._integral = 0.0
        self._deriv = 0.0
        self._err_prev = 0.0
        self._tick = 0
        self._start = 0.0
        self._thread: threading.Thread | None = None
        self._cmd_decoder = FrameDecoder()
        self._rng = random.Random(1234)  # nosec B311 - simulated sensor noise only

    def open(self) -> None:
        self._start = time.perf_counter()
        self._open = True
        self._thread = threading.Thread(target=self._run, name="mock-link", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._open = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def send(self, data: bytes) -> None:
        self.stats.tx_bytes += len(data)
        for msg_type, payload in self._cmd_decoder.feed(data):
            self._handle_command(msg_type, payload)

    def _reply_ack(self, acked: int) -> None:
        self._ingest(encode_frame(MSG_ACK, ACK_STRUCT.pack(acked, ACK_OK)))

    def _handle_command(self, msg_type: int, payload: bytes) -> None:
        if msg_type == CMD_SET_GAINS and len(payload) == GAINS_STRUCT.size:
            self.kp, self.ki, self.kd = GAINS_STRUCT.unpack(payload)
            self._reply_ack(msg_type)
        elif msg_type == CMD_SET_SETPOINT and len(payload) == 4:
            (self.setpoint,) = struct.unpack("<f", payload)
            self._reply_ack(msg_type)
        elif msg_type == CMD_SET_MODE and len(payload) == 1:
            self.mode = payload[0]
            if self.mode == MODE_IDLE:
                self._integral = 0.0
            self._reply_ack(msg_type)
        elif msg_type == CMD_START_TELEMETRY and len(payload) == 2:
            (self.telemetry_period_ms,) = struct.unpack("<H", payload)
            self.telemetry_enabled = True
            self._reply_ack(msg_type)
        elif msg_type == CMD_STOP_TELEMETRY:
            self.telemetry_enabled = False
            self._reply_ack(msg_type)
        elif msg_type == CMD_GET_GAINS:
            self._ingest(encode_frame(MSG_GAINS, GAINS_STRUCT.pack(self.kp, self.ki, self.kd)))

    def _run(self) -> None:
        next_t = time.perf_counter()
        while self._open:
            delay = next_t - time.perf_counter()
            if delay > 0.0:
                time.sleep(delay)
                continue
            while self._open and next_t <= time.perf_counter():
                self._step()
                next_t += self.dt
                if next_t < time.perf_counter() - 0.5:
                    next_t = time.perf_counter()

    def _step(self) -> None:
        dt = self.dt
        err = self.setpoint - self._feedback
        if self.mode == MODE_RUN:
            up = self.kp * err
            raw_deriv = (err - self._err_prev) / dt
            self._deriv += 0.2 * (raw_deriv - self._deriv)
            candidate = up + self._integral + self.kd * self._deriv
            saturated = candidate > self.u_limit or candidate < -self.u_limit
            drives_out = (candidate > self.u_limit and err < 0.0) or (
                candidate < -self.u_limit and err > 0.0
            )
            if not saturated or drives_out:
                self._integral = _clamp(
                    self._integral + self.ki * err * dt, -self.integral_limit, self.integral_limit
                )
                candidate = up + self._integral + self.kd * self._deriv
            u = _clamp(candidate, -self.u_limit, self.u_limit)
        else:
            up = 0.0
            u = 0.0
            self._integral = 0.0
        self._err_prev = err
        half_load = self.load * 0.5
        self._omega1 += (dt / self.tau2) * (u - half_load - self._omega1)
        self._omega2 += (dt / self.tau1) * (self._omega1 - half_load - self._omega2)
        self._feedback = self._omega2 + self.noise * self._rng.gauss(0.0, 1.0)
        self._tick += 1
        if not self.telemetry_enabled:
            return
        if self.telemetry_period_ms:
            period_ticks = max(1, int(round(self.telemetry_period_ms / (dt * 1000.0))))
        else:
            period_ticks = 1
        if self._tick % period_ticks:
            return
        tick_us = int((time.perf_counter() - self._start) * 1e6) & 0xFFFFFFFF
        payload = TELEMETRY_STRUCT.pack(
            tick_us,
            self.setpoint,
            self._feedback,
            u,
            up,
            self._integral,
            self.kd * self._deriv,
        )
        self._ingest(encode_frame(MSG_TELEMETRY, payload))
