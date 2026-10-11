from __future__ import annotations

import queue
import threading

import serial

from pidtool.core.ringbuffer import SampleBuffer
from pidtool.link.protocol import LinkEvent, ProtocolAdapter


class LinkStats:
    def __init__(self) -> None:
        self.rx_bytes = 0
        self.tx_bytes = 0
        self.frames = 0
        self.crc_errors = 0
        self.garbage_bytes = 0
        self.errors = 0
        self.last_error = ""


class LinkBase:
    def __init__(self, adapter: ProtocolAdapter) -> None:
        self.adapter = adapter
        self.buffer = SampleBuffer()
        self.events: queue.Queue[LinkEvent] = queue.Queue()
        self.stats = LinkStats()
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def send(self, data: bytes) -> None:
        raise NotImplementedError

    def _ingest(self, data: bytes) -> None:
        self.stats.rx_bytes += len(data)
        samples, events = self.adapter.feed(data)
        if samples:
            self.buffer.append(samples)
        for event in events:
            self.events.put(event)
        decoder = getattr(self.adapter, "decoder", None)
        if decoder is not None:
            self.stats.frames = decoder.frames
            self.stats.crc_errors = decoder.crc_errors
            self.stats.garbage_bytes = decoder.garbage_bytes

    def drain_events(self, limit: int = 200) -> list[LinkEvent]:
        out: list[LinkEvent] = []
        while len(out) < limit:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                break
        return out

    def estimate_rate(self, span: float = 2.0) -> float:
        tail = self.buffer.tail(8192)
        ts = tail["t"]
        if ts.size < 2:
            return 0.0
        cutoff = float(ts[-1]) - span
        kept = ts[ts >= cutoff]
        if kept.size < 2:
            return 0.0
        dt = float(kept[-1] - kept[0])
        if dt <= 0.0:
            return 0.0
        return (kept.size - 1) / dt


class SerialLink(LinkBase):
    def __init__(self, port: str, baud: int, adapter: ProtocolAdapter) -> None:
        super().__init__(adapter)
        self.port = port
        self.baud = int(baud)
        self._ser: serial.Serial | None = None
        self._thread: threading.Thread | None = None

    def open(self) -> None:
        self._ser = serial.Serial(self.port, self.baud, timeout=0.05)
        self._open = True
        self._thread = threading.Thread(target=self._run, name="serial-reader", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        ser = self._ser
        assert ser is not None
        while self._open:
            try:
                waiting = ser.in_waiting
                data = ser.read(waiting if waiting else 1)
            except Exception as exc:
                self.stats.errors += 1
                self.stats.last_error = str(exc)
                self._open = False
                break
            if data:
                self._ingest(data)

    def send(self, data: bytes) -> None:
        if not self._open or self._ser is None:
            raise RuntimeError("串口未连接")
        self._ser.write(data)
        self.stats.tx_bytes += len(data)

    def close(self) -> None:
        self._open = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
