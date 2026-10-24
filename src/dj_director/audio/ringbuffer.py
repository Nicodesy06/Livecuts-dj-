"""Bounded single-producer/single-consumer ring buffer for mono float32 audio.

The producer (audio callback) never blocks for long and never allocates
beyond the fixed capacity. If the consumer falls behind, the oldest unread
samples are overwritten and the overrun is counted: latency stays bounded
instead of growing.
"""

from __future__ import annotations

import threading

import numpy as np


class AudioRingBuffer:
    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._buf = np.zeros(capacity, dtype=np.float32)
        self._cap = capacity
        self._write_total = 0       # frames ever written
        self._read_total = 0        # frames ever consumed
        self.overrun_frames = 0
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._cap

    @property
    def write_total(self) -> int:
        return self._write_total

    @property
    def read_total(self) -> int:
        return self._read_total

    def available(self) -> int:
        with self._lock:
            return self._write_total - self._read_total

    def fill_ratio(self) -> float:
        return self.available() / self._cap

    def write(self, data: np.ndarray) -> None:
        n = len(data)
        if n == 0:
            return
        with self._lock:
            if n > self._cap:
                # keep only the newest samples, but account for the skipped ones so
                # absolute sample indices (and the beat clock) stay correct
                skip = n - self._cap
                data = data[skip:]
                n = self._cap
                self._write_total += skip
            start = self._write_total % self._cap
            end = start + n
            if end <= self._cap:
                self._buf[start:end] = data
            else:
                k = self._cap - start
                self._buf[start:] = data[:k]
                self._buf[: n - k] = data[k:]
            self._write_total += n
            unread = self._write_total - self._read_total
            if unread > self._cap:
                lost = unread - self._cap
                self.overrun_frames += lost
                self._read_total += lost

    def read(self, n: int) -> tuple[np.ndarray, int]:
        """Consume up to ``n`` frames. Returns (samples, absolute index of first sample)."""
        with self._lock:
            n = min(n, self._write_total - self._read_total)
            first = self._read_total
            out = self._copy(first, n)
            self._read_total += n
            return out, first

    def peek_latest(self, n: int) -> np.ndarray:
        """Most recent ``n`` written frames without consuming (for meters)."""
        with self._lock:
            n = min(n, self._write_total, self._cap)
            return self._copy(self._write_total - n, n)

    def _copy(self, first: int, n: int) -> np.ndarray:
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        start = first % self._cap
        end = start + n
        if end <= self._cap:
            return self._buf[start:end].copy()
        k = self._cap - start
        return np.concatenate([self._buf[start:], self._buf[: n - k]])
