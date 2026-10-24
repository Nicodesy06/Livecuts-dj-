import numpy as np

from dj_director.audio.ringbuffer import AudioRingBuffer


def test_write_read_roundtrip():
    rb = AudioRingBuffer(100)
    rb.write(np.arange(30, dtype=np.float32))
    out, first = rb.read(20)
    assert first == 0 and np.array_equal(out, np.arange(20))
    out, first = rb.read(100)
    assert first == 20 and np.array_equal(out, np.arange(20, 30))
    assert rb.available() == 0


def test_wraparound():
    rb = AudioRingBuffer(10)
    rb.write(np.arange(8, dtype=np.float32))
    rb.read(8)
    rb.write(np.arange(8, 14, dtype=np.float32))
    out, first = rb.read(10)
    assert first == 8 and np.array_equal(out, np.arange(8, 14))


def test_overrun_drops_oldest_and_counts():
    rb = AudioRingBuffer(10)
    rb.write(np.arange(25, dtype=np.float32))
    assert rb.overrun_frames == 15
    out, first = rb.read(100)
    assert first == 15 and np.array_equal(out, np.arange(15, 25))   # bounded latency


def test_peek_latest_does_not_consume():
    rb = AudioRingBuffer(16)
    rb.write(np.arange(12, dtype=np.float32))
    assert np.array_equal(rb.peek_latest(4), np.arange(8, 12))
    assert rb.available() == 12
