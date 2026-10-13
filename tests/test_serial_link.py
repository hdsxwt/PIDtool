from __future__ import annotations

import time

import pytest

from pidtool.link.protocol import (
    ACK_STRUCT,
    CMD_SET_GAINS,
    MSG_ACK,
    MSG_TELEMETRY,
    TELEMETRY_STRUCT,
    DefaultProtocolAdapter,
    Sample,
    encode_frame,
)
from pidtool.link.serial_link import LinkBase, SerialLink


def _sample(t: float) -> Sample:
    return Sample(t, int(t * 1e6), 1.0, 0.5, 0.1, 0.05, 0.02, 0.01)


class FakeSerial:
    def __init__(self, port: str, baud: int, timeout: float = 0.05) -> None:
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.in_waiting = 0
        self.pending: list[bytes] = []
        self._buffer = bytearray()
        self.written = bytearray()
        self.closed = False
        self.raise_read = False
        self.raise_close = False
        FakeSerial.instances.append(self)

    instances: list["FakeSerial"] = []

    def read(self, size: int) -> bytes:
        if self.raise_read:
            raise OSError("read boom")
        if not self._buffer and self.pending:
            self._buffer.extend(self.pending.pop(0))
        if self._buffer:
            out = bytes(self._buffer[:size])
            del self._buffer[:size]
            self.in_waiting = len(self._buffer)
            return out
        self.in_waiting = 0
        time.sleep(0.005)
        return b""

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    def close(self) -> None:
        if self.raise_close:
            raise OSError("close boom")
        self.closed = True


@pytest.fixture()
def fake_serial(monkeypatch):
    FakeSerial.instances = []
    monkeypatch.setattr("pidtool.link.serial_link.serial.Serial", FakeSerial)
    return FakeSerial


def test_linkbase_abstract_methods():
    link = LinkBase(DefaultProtocolAdapter())
    with pytest.raises(NotImplementedError):
        link.open()
    with pytest.raises(NotImplementedError):
        link.close()
    with pytest.raises(NotImplementedError):
        link.send(b"")


def test_ingest_updates_buffer_stats_and_events():
    link = LinkBase(DefaultProtocolAdapter())
    frame = encode_frame(MSG_TELEMETRY, TELEMETRY_STRUCT.pack(5, 1.0, 0.9, 0.2, 0.1, 0.02, 0.01))
    link._ingest(frame)
    assert link.buffer.total == 1
    assert link.stats.rx_bytes == len(frame)
    assert link.stats.frames == 1
    assert link.stats.crc_errors == 0

    ack = encode_frame(MSG_ACK, ACK_STRUCT.pack(CMD_SET_GAINS, 0))
    link._ingest(ack)
    events = link.drain_events()
    assert [event.kind for event in events] == ["ack"]

    corrupt = bytearray(frame)
    corrupt[-1] ^= 0xFF
    link._ingest(bytes(corrupt))
    assert link.stats.crc_errors == 1


def test_drain_events_respects_limit():
    link = LinkBase(DefaultProtocolAdapter())
    frame = encode_frame(MSG_ACK, ACK_STRUCT.pack(CMD_SET_GAINS, 0))
    for _ in range(5):
        link._ingest(frame)
    assert len(link.drain_events(limit=2)) == 2
    assert len(link.drain_events()) == 3
    assert link.drain_events() == []


def test_estimate_rate_cases():
    link = LinkBase(DefaultProtocolAdapter())
    assert link.estimate_rate() == 0.0

    link.buffer.append([_sample(1.0)])
    assert link.estimate_rate() == 0.0

    link.buffer.clear()
    link.buffer.append([_sample(i * 0.01) for i in range(300)])
    rate = link.estimate_rate(span=1.0)
    assert 90.0 < rate < 110.0

    link.buffer.clear()
    link.buffer.append([_sample(1.0), _sample(1.0), _sample(1.0)])
    assert link.estimate_rate() == 0.0

    link.buffer.clear()
    link.buffer.append([_sample(100.0), _sample(100.5), _sample(101.0)])
    assert link.estimate_rate(span=0.2) == 0.0


def test_serial_link_open_send_receive_close(fake_serial):
    adapter = DefaultProtocolAdapter()
    link = SerialLink("COM_TEST", 115200, adapter)
    assert not link.is_open
    link.open()
    assert link.is_open
    fake = FakeSerial.instances[-1]
    assert fake.port == "COM_TEST" and fake.baud == 115200

    frame = encode_frame(MSG_TELEMETRY, TELEMETRY_STRUCT.pack(9, 1.0, 0.9, 0.2, 0.1, 0.02, 0.01))
    fake.pending.append(frame)
    deadline = time.time() + 2.0
    while time.time() < deadline and link.buffer.total < 1:
        time.sleep(0.01)
    assert link.buffer.total == 1
    assert link.stats.frames == 1

    command = adapter.encode_set_gains(1.0, 2.0, 3.0)
    link.send(command)
    assert bytes(fake.written) == command
    assert link.stats.tx_bytes == len(command)

    link.close()
    assert not link.is_open
    assert fake.closed
    assert link._ser is None


def test_serial_link_send_requires_open(fake_serial):
    link = SerialLink("COM_TEST", 115200, DefaultProtocolAdapter())
    with pytest.raises(RuntimeError):
        link.send(b"x")
    link.open()
    link.close()
    with pytest.raises(RuntimeError):
        link.send(b"x")


def test_serial_read_error_marks_link_closed(fake_serial):
    link = SerialLink("COM_TEST", 115200, DefaultProtocolAdapter())
    link.open()
    fake = FakeSerial.instances[-1]
    fake.raise_read = True
    deadline = time.time() + 2.0
    while time.time() < deadline and link.is_open:
        time.sleep(0.01)
    assert not link.is_open
    assert link.stats.errors == 1
    assert "boom" in link.stats.last_error
    link.close()


def test_serial_close_swallows_errors(fake_serial):
    link = SerialLink("COM_TEST", 115200, DefaultProtocolAdapter())
    link.open()
    fake = FakeSerial.instances[-1]
    fake.raise_close = True
    link.close()
    assert link._ser is None
    assert not link.is_open
