from __future__ import annotations

import random
import struct

from pidtool.link.protocol import (
    ACK_OK,
    ACK_STRUCT,
    CMD_GET_GAINS,
    CMD_SET_GAINS,
    CMD_SET_MODE,
    CMD_SET_SETPOINT,
    CMD_START_TELEMETRY,
    CMD_STOP_TELEMETRY,
    MODE_RUN,
    MSG_ACK,
    MSG_TELEMETRY,
    TELEMETRY_STRUCT,
    DefaultProtocolAdapter,
    FrameDecoder,
    crc16_ccitt,
    encode_frame,
    load_adapter,
)


def test_crc_known_vector():
    assert crc16_ccitt(b"123456789") == 0x29B1


def _reference_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def test_crc_matches_bitwise_reference():
    rng = random.Random(42)
    for length in (0, 1, 2, 5, 29, 64, 200, 251):
        payload = bytes(rng.randrange(256) for _ in range(length))
        assert crc16_ccitt(payload) == _reference_crc16(payload)
    assert crc16_ccitt(b"") == 0xFFFF


def test_telemetry_roundtrip():
    adapter = DefaultProtocolAdapter()
    frame = encode_frame(MSG_TELEMETRY, TELEMETRY_STRUCT.pack(1234, 1.0, 0.9, 2.0, 1.0, 0.5, 0.1))
    samples, events = adapter.feed(frame)
    assert len(samples) == 1
    assert events == []
    sample = samples[0]
    assert sample.tick_us == 1234
    assert sample.setpoint == 1.0
    assert abs(sample.feedback - 0.9) < 1e-6
    assert abs(sample.u - 2.0) < 1e-6
    assert abs(sample.up - 1.0) < 1e-6
    assert abs(sample.ui - 0.5) < 1e-6
    assert abs(sample.ud - 0.1) < 1e-6


def test_parser_partial_frames():
    decoder = FrameDecoder()
    frame = encode_frame(MSG_TELEMETRY, TELEMETRY_STRUCT.pack(1, 0.5, 0.4, 1.0, 0.8, 0.2, 0.0))
    assert decoder.feed(b"\x00\x11" + frame[:5]) == []
    out = decoder.feed(frame[5:] + b"\x99\x88")
    assert len(out) == 1
    assert out[0][0] == MSG_TELEMETRY
    assert decoder.crc_errors == 0
    assert decoder.frames == 1


def test_crc_error_rejected():
    decoder = FrameDecoder()
    frame = bytearray(encode_frame(MSG_TELEMETRY, TELEMETRY_STRUCT.pack(1, 0, 0, 0, 0, 0, 0)))
    frame[10] ^= 0x01
    assert decoder.feed(bytes(frame)) == []
    assert decoder.crc_errors == 1


def test_resync_after_bad_length():
    decoder = FrameDecoder()
    frame = encode_frame(MSG_TELEMETRY, TELEMETRY_STRUCT.pack(1, 0, 0, 0, 0, 0, 0))
    out = decoder.feed(b"\xaa\x55\x00" + frame)
    assert len(out) == 1
    assert decoder.garbage_bytes >= 1


def test_command_encodings():
    adapter = DefaultProtocolAdapter()
    decoder = FrameDecoder()

    frames = decoder.feed(adapter.encode_set_gains(1.5, 2.5, 0.25))
    assert frames == [(CMD_SET_GAINS, struct.pack("<3f", 1.5, 2.5, 0.25))]

    frames = decoder.feed(adapter.encode_set_setpoint(3.25))
    assert frames[0][0] == CMD_SET_SETPOINT
    assert struct.unpack("<f", frames[0][1])[0] == 3.25

    frames = decoder.feed(adapter.encode_set_mode(MODE_RUN))
    assert frames == [(CMD_SET_MODE, b"\x01")]

    frames = decoder.feed(adapter.encode_start_telemetry(5))
    assert frames == [(CMD_START_TELEMETRY, struct.pack("<H", 5))]

    frames = decoder.feed(adapter.encode_stop_telemetry())
    assert frames == [(CMD_STOP_TELEMETRY, b"")]

    frames = decoder.feed(adapter.encode_get_gains())
    assert frames == [(CMD_GET_GAINS, b"")]


def test_ack_event():
    adapter = DefaultProtocolAdapter()
    _, events = adapter.feed(encode_frame(MSG_ACK, ACK_STRUCT.pack(CMD_SET_GAINS, ACK_OK)))
    assert len(events) == 1
    assert events[0].kind == "ack"
    assert events[0].data == {"acked": CMD_SET_GAINS, "status": ACK_OK}


def test_load_default_adapter():
    adapter = load_adapter("default")
    assert isinstance(adapter, DefaultProtocolAdapter)


def test_load_custom_adapter(tmp_path):
    module_path = tmp_path / "my_adapter.py"
    module_path.write_text(
        "from pidtool.link.protocol import DefaultProtocolAdapter\n"
        "\n"
        "class Adapter(DefaultProtocolAdapter):\n"
        "    name = \"custom\"\n",
        encoding="utf-8",
    )
    adapter = load_adapter(f"{module_path}:Adapter")
    assert adapter.name == "custom"
