from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from pidtool.link.protocol import (
    ACK_OK,
    CMD_GET_GAINS,
    CMD_SET_GAINS,
    CMD_SET_MODE,
    CMD_SET_SETPOINT,
    CMD_START_TELEMETRY,
    CMD_STOP_TELEMETRY,
    MODE_RUN,
    MSG_ACK,
    MSG_GAINS,
    MSG_TEXT,
    MSG_TELEMETRY,
    TELEMETRY_STRUCT,
    crc16_ccitt,
    encode_frame,
)

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_DIR = ROOT / "docs" / "firmware"
TEST_SOURCE = Path(__file__).resolve().parent / "c_reference_test.c"
GCC = shutil.which("gcc") or shutil.which("cc")

pytestmark = pytest.mark.skipif(GCC is None, reason="gcc/cc not available")


@pytest.fixture(scope="module")
def c_exe(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("c_reference")
    exe = out_dir / ("c_reference_test.exe" if os.name == "nt" else "c_reference_test")
    proc = subprocess.run(
        [
            GCC,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-O1",
            "-I",
            str(FIRMWARE_DIR),
            str(TEST_SOURCE),
            str(FIRMWARE_DIR / "pid_protocol.c"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(f"gcc build failed:\n{proc.stdout}\n{proc.stderr}")
    return exe


def _run(exe: Path, args: list[str], stdin_text: str = "") -> str:
    proc = subprocess.run(
        [str(exe), *args], input=stdin_text, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _lines(out: str, prefix: str) -> list[str]:
    marker = prefix + ":"
    return [line[len(marker):] for line in out.splitlines() if line.startswith(marker)]


def test_crc_parity(c_exe):
    out = _run(c_exe, ["encode"])
    expected = f"{crc16_ccitt(b'123456789'):04X}"
    assert _lines(out, "CRC")[0] == expected


def test_frame_bytes_match_python_codec(c_exe):
    out = _run(c_exe, ["encode"])
    frames = [value.upper() for value in _lines(out, "FRAME")]
    expected = [
        encode_frame(
            MSG_TELEMETRY, TELEMETRY_STRUCT.pack(0x01020304, 1.0, 0.5, 2.0, 1.0, 0.5, 0.25)
        ),
        encode_frame(MSG_GAINS, struct.pack("<3f", 1.5, 2.5, 0.25)),
        encode_frame(CMD_SET_GAINS, struct.pack("<3f", 1.5, 2.5, 0.25)),
        encode_frame(CMD_SET_SETPOINT, struct.pack("<f", 3.25)),
        encode_frame(CMD_SET_MODE, struct.pack("<B", MODE_RUN)),
        encode_frame(CMD_START_TELEMETRY, struct.pack("<H", 5)),
        encode_frame(CMD_STOP_TELEMETRY),
        encode_frame(CMD_GET_GAINS),
        encode_frame(MSG_ACK, struct.pack("<BB", CMD_SET_GAINS, ACK_OK)),
        encode_frame(MSG_TEXT, b"hi"),
    ]
    assert frames == [frame.hex().upper() for frame in expected]


def test_send_helper_matches_encoder(c_exe):
    out = _run(c_exe, ["encode"])
    send_line = _lines(out, "SEND")[0].upper()
    expected = encode_frame(
        MSG_TELEMETRY, TELEMETRY_STRUCT.pack(123456, 2.0, 1.9, 3.0, 2.0, 0.9, 0.1)
    )
    assert send_line == expected.hex().upper()


def test_parser_dispatches_commands(c_exe):
    stream = (
        b"\x00\x11\x99"
        + encode_frame(CMD_SET_GAINS, struct.pack("<3f", 1.5, 2.5, 0.25))
        + b"\xaa"
        + encode_frame(CMD_SET_SETPOINT, struct.pack("<f", 3.25))
        + encode_frame(CMD_SET_MODE, struct.pack("<B", MODE_RUN))
        + encode_frame(CMD_START_TELEMETRY, struct.pack("<H", 5))
        + encode_frame(CMD_GET_GAINS)
        + encode_frame(CMD_STOP_TELEMETRY)
    )
    out = _run(c_exe, ["parse"], stdin_text=stream.hex() + "\n")
    commands = [line for line in out.splitlines() if line.startswith("CMD:")]
    assert commands == [
        "CMD:10:1.500000:2.500000:0.250000",
        "CMD:11:3.250000",
        "CMD:12:1",
        "CMD:13:5",
        "CMD:15",
        "CMD:14",
    ]
    assert "PARSED:6:0" in out


def test_parser_rejects_corrupted_frame(c_exe):
    frame = bytearray(encode_frame(CMD_SET_SETPOINT, struct.pack("<f", 1.0)))
    frame[-1] ^= 0x01
    stream = bytes(frame) + encode_frame(CMD_STOP_TELEMETRY)
    out = _run(c_exe, ["parse"], stdin_text=stream.hex())
    commands = [line for line in out.splitlines() if line.startswith("CMD:")]
    assert commands == ["CMD:14"]
    assert "PARSED:1:1" in out
