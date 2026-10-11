from __future__ import annotations

import importlib.util
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

SOF = b"\xaa\x55"
MAX_BODY = 251

MSG_TELEMETRY = 0x01
MSG_GAINS = 0x20
MSG_TEXT = 0x30
MSG_ACK = 0x7F

CMD_SET_GAINS = 0x10
CMD_SET_SETPOINT = 0x11
CMD_SET_MODE = 0x12
CMD_START_TELEMETRY = 0x13
CMD_STOP_TELEMETRY = 0x14
CMD_GET_GAINS = 0x15

MODE_IDLE = 0
MODE_RUN = 1

ACK_OK = 0

TELEMETRY_STRUCT = struct.Struct("<I6f")
GAINS_STRUCT = struct.Struct("<3f")
ACK_STRUCT = struct.Struct("<BB")
FLOAT_STRUCT = struct.Struct("<f")
PERIOD_STRUCT = struct.Struct("<H")
MODE_STRUCT = struct.Struct("<B")


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def encode_frame(msg_type: int, payload: bytes = b"") -> bytes:
    body = bytes((msg_type,)) + payload
    if len(body) > MAX_BODY:
        raise ValueError(f"frame body too large: {len(body)}")
    crc = crc16_ccitt(body)
    return SOF + bytes((len(body),)) + body + crc.to_bytes(2, "little")


class FrameDecoder:
    __slots__ = ("_buf", "frames", "crc_errors", "garbage_bytes")

    def __init__(self) -> None:
        self._buf = bytearray()
        self.frames = 0
        self.crc_errors = 0
        self.garbage_bytes = 0

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self._buf.extend(data)
        out: list[tuple[int, bytes]] = []
        buf = self._buf
        pos = 0
        while True:
            n = len(buf)
            start = buf.find(SOF, pos)
            if start < 0:
                keep = 1 if n and buf[n - 1] == SOF[0] else 0
                skipped = n - keep - pos
                if skipped > 0:
                    self.garbage_bytes += skipped
                if keep:
                    del buf[: n - keep]
                else:
                    buf.clear()
                break
            if start > pos:
                self.garbage_bytes += start - pos
            if n - start < 3:
                del buf[:start]
                break
            body_len = buf[start + 2]
            if body_len == 0 or body_len > MAX_BODY:
                self.garbage_bytes += 1
                pos = start + 1
                continue
            frame_len = 3 + body_len + 2
            if n - start < frame_len:
                del buf[:start]
                break
            body = bytes(buf[start + 3 : start + 3 + body_len])
            crc_rx = int.from_bytes(buf[start + 3 + body_len : start + frame_len], "little")
            if crc16_ccitt(body) == crc_rx:
                self.frames += 1
                out.append((body[0], body[1:]))
                pos = start + frame_len
            else:
                self.crc_errors += 1
                pos = start + 1
        return out


@dataclass(frozen=True, slots=True)
class Sample:
    t: float
    tick_us: int
    setpoint: float
    feedback: float
    u: float
    up: float
    ui: float
    ud: float


@dataclass(frozen=True, slots=True)
class LinkEvent:
    t: float
    kind: str
    data: dict


class ProtocolAdapter:
    name = "base"

    def encode_set_gains(self, kp: float, ki: float, kd: float) -> bytes:
        raise NotImplementedError

    def encode_set_setpoint(self, setpoint: float) -> bytes:
        raise NotImplementedError

    def encode_set_mode(self, mode: int) -> bytes:
        raise NotImplementedError

    def encode_start_telemetry(self, period_ms: int = 0) -> bytes:
        raise NotImplementedError

    def encode_stop_telemetry(self) -> bytes:
        raise NotImplementedError

    def encode_get_gains(self) -> bytes:
        raise NotImplementedError

    def feed(self, data: bytes) -> tuple[list[Sample], list[LinkEvent]]:
        raise NotImplementedError


class DefaultProtocolAdapter(ProtocolAdapter):
    name = "default"

    def __init__(self) -> None:
        self.decoder = FrameDecoder()

    def encode_set_gains(self, kp: float, ki: float, kd: float) -> bytes:
        return encode_frame(CMD_SET_GAINS, GAINS_STRUCT.pack(kp, ki, kd))

    def encode_set_setpoint(self, setpoint: float) -> bytes:
        return encode_frame(CMD_SET_SETPOINT, FLOAT_STRUCT.pack(setpoint))

    def encode_set_mode(self, mode: int) -> bytes:
        return encode_frame(CMD_SET_MODE, MODE_STRUCT.pack(mode))

    def encode_start_telemetry(self, period_ms: int = 0) -> bytes:
        return encode_frame(CMD_START_TELEMETRY, PERIOD_STRUCT.pack(int(period_ms)))

    def encode_stop_telemetry(self) -> bytes:
        return encode_frame(CMD_STOP_TELEMETRY)

    def encode_get_gains(self) -> bytes:
        return encode_frame(CMD_GET_GAINS)

    def feed(self, data: bytes) -> tuple[list[Sample], list[LinkEvent]]:
        samples: list[Sample] = []
        events: list[LinkEvent] = []
        now = time.perf_counter()
        for msg_type, payload in self.decoder.feed(data):
            if msg_type == MSG_TELEMETRY and len(payload) == TELEMETRY_STRUCT.size:
                tick_us, sp, fb, u, up, ui, ud = TELEMETRY_STRUCT.unpack(payload)
                samples.append(Sample(now, tick_us, sp, fb, u, up, ui, ud))
            elif msg_type == MSG_GAINS and len(payload) == GAINS_STRUCT.size:
                kp, ki, kd = GAINS_STRUCT.unpack(payload)
                events.append(LinkEvent(now, "gains", {"kp": kp, "ki": ki, "kd": kd}))
            elif msg_type == MSG_ACK and len(payload) == ACK_STRUCT.size:
                acked, status = ACK_STRUCT.unpack(payload)
                events.append(LinkEvent(now, "ack", {"acked": acked, "status": status}))
            elif msg_type == MSG_TEXT:
                events.append(LinkEvent(now, "text", {"text": payload.decode("utf-8", "replace")}))
        return samples, events


def load_adapter(spec: str) -> ProtocolAdapter:
    spec = (spec or "default").strip()
    if spec == "default":
        return DefaultProtocolAdapter()
    path = Path(spec)
    cls_name = "Adapter"
    if not path.is_file():
        path_part, _, maybe_cls = spec.rpartition(":")
        path = Path(path_part)
        if maybe_cls:
            cls_name = maybe_cls
    if not path.is_file():
        raise FileNotFoundError(f"适配器模块不存在: {path}")
    module_name = f"pidtool_adapter_{path.stem}"
    mod_spec = importlib.util.spec_from_file_location(module_name, path)
    if mod_spec is None or mod_spec.loader is None:
        raise ImportError(f"无法加载适配器模块: {path}")
    module = importlib.util.module_from_spec(mod_spec)
    sys.modules[module_name] = module
    mod_spec.loader.exec_module(module)
    cls = getattr(module, cls_name, None)
    if cls is None:
        raise AttributeError(f"适配器类不存在: {cls_name}")
    return cls()
