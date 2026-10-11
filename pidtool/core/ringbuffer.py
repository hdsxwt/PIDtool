from __future__ import annotations

import threading

import numpy as np

from pidtool.link.protocol import Sample

FLOAT_FIELDS = ("setpoint", "feedback", "u", "up", "ui", "ud")


class SampleBuffer:
    def __init__(self, capacity: int = 1_000_000) -> None:
        self._capacity = int(capacity)
        cap = self._capacity
        self._arrays: dict[str, np.ndarray] = {
            "t": np.zeros(cap, dtype=np.float64),
            "tick_us": np.zeros(cap, dtype=np.int64),
        }
        for name in FLOAT_FIELDS:
            self._arrays[name] = np.zeros(cap, dtype=np.float32)
        self._count = 0
        self._lock = threading.Lock()

    @property
    def total(self) -> int:
        with self._lock:
            return self._count

    def append(self, samples: list[Sample]) -> None:
        n = len(samples)
        if n == 0:
            return
        cap = self._capacity
        with self._lock:
            idx = (np.arange(n, dtype=np.int64) + (self._count % cap)) % cap
            self._arrays["t"][idx] = [s.t for s in samples]
            self._arrays["tick_us"][idx] = [s.tick_us for s in samples]
            for name in FLOAT_FIELDS:
                self._arrays[name][idx] = [getattr(s, name) for s in samples]
            self._count += n

    def clear(self) -> None:
        with self._lock:
            self._count = 0

    def span(self) -> tuple[float, float]:
        with self._lock:
            size = min(self._count, self._capacity)
            if size == 0:
                return (0.0, 0.0)
            first = (self._count - size) % self._capacity
            last = (self._count - 1) % self._capacity
            return (float(self._arrays["t"][first]), float(self._arrays["t"][last]))

    def tail(self, n: int) -> dict[str, np.ndarray]:
        with self._lock:
            size = min(self._count, self._capacity)
            n = max(0, min(int(n), size))
            if n == 0:
                return {name: arr[:0].copy() for name, arr in self._arrays.items()}
            start = (self._count - n) % self._capacity
            idx = (np.arange(n, dtype=np.int64) + start) % self._capacity
            return {name: arr[idx].copy() for name, arr in self._arrays.items()}
