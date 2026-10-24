"""Audio capture backends.

All backends share one contract: they push mono float32 blocks into a
:class:`AudioRingBuffer` from their own thread and record a time anchor so
that sample indices can be mapped onto the monotonic clock. Nothing heavy
happens in the callbacks: channel selection, mono mixdown, a peak value and
a memcpy into the ring buffer.

Backends:
* ``SoundDeviceBackend`` - any PortAudio input (audio interface, USB mixer,
  line-in). On Windows, WASAPI devices are preferred.
* ``LoopbackBackend`` - WASAPI loopback of an output device via the optional
  PyAudioWPatch package (Windows only).
* ``FileBackend`` - WAV/FLAC/OGG file played in real time (testing, demos).
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from dj_director.audio.ringbuffer import AudioRingBuffer

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    host_api: str
    max_input_channels: int
    default_sample_rate: float
    kind: str = "input"         # "input" | "loopback"

    @property
    def label(self) -> str:
        from dj_director.audio.devices import friendly_label
        return friendly_label(self.name, self.host_api, self.max_input_channels)


class TimeAnchor:
    """Maps absolute sample index -> monotonic time of acquisition (ADC).

    Callbacks run *late* by a variable amount (scheduling jitter), never early,
    so the smallest observed ``callback_time - frames/sr`` is the best estimate
    of the true offset. We track that minimum, letting it drift upward slowly
    to follow clock-rate differences between the sound card and the CPU.
    """

    def __init__(self, sample_rate: int, input_latency: float = 0.0) -> None:
        self.sr = sample_rate
        self.input_latency = input_latency
        self._offset: Optional[float] = None
        self._lock = threading.Lock()
        self.last_callback_t = 0.0

    def update(self, callback_t: float, frames_total: int) -> None:
        off = callback_t - frames_total / self.sr
        with self._lock:
            self.last_callback_t = callback_t
            if self._offset is None or off < self._offset:
                self._offset = off
            else:
                self._offset += (off - self._offset) * 0.002

    def time_of(self, frame_index: float) -> float:
        with self._lock:
            off = self._offset if self._offset is not None else time.monotonic()
        return off + frame_index / self.sr - self.input_latency


class AudioBackend:
    name = "base"

    def __init__(self, ring: AudioRingBuffer, channels: list[int]) -> None:
        self.ring = ring
        self.channels = list(channels) or [0]
        self.sample_rate = 0
        self.device_name = ""
        self.device_channels = 0
        self.anchor: Optional[TimeAnchor] = None
        self.peak = 0.0             # max |x| since last read_peak()
        self.status_errors = 0
        self.input_latency = 0.0
        self.running = False
        self.error = ""
        self.zero_frames = 0        # consecutive frames that were exactly 0.0
        self.frames_pushed = 0

    @property
    def digital_silence(self) -> bool:
        """Samples exactly zero for > 2 s: typically Windows microphone privacy
        blocking desktop apps, or a muted/disabled endpoint (real silence from an
        analogue input always carries some noise)."""
        return self.sample_rate > 0 and self.zero_frames > 2 * self.sample_rate

    # -- helpers for callbacks
    def _push(self, block: np.ndarray, callback_t: float) -> None:
        if block.ndim == 2:
            sel = [c for c in self.channels if c < block.shape[1]] or [0]
            mono = block[:, sel].mean(axis=1) if len(sel) > 1 else block[:, sel[0]]
        else:
            mono = block
        mono = np.ascontiguousarray(mono, dtype=np.float32)
        if len(mono):
            p = float(np.max(np.abs(mono)))
            if p > self.peak:
                self.peak = p
            self.zero_frames = self.zero_frames + len(mono) if p == 0.0 else 0
            self.frames_pushed += len(mono)
        self.ring.write(mono)
        if self.anchor is not None:
            self.anchor.update(callback_t, self.ring.write_total)

    def alive(self) -> bool:
        """False when the underlying stream has died (device unplugged, driver reset)."""
        return self.running

    def read_peak(self) -> float:
        p, self.peak = self.peak, 0.0
        return p

    def start(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


# --------------------------------------------------------------------------- PortAudio
def list_input_devices() -> list[DeviceInfo]:
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        apis = sd.query_hostapis()
    except Exception as e:  # PortAudio missing or broken
        log.warning("cannot enumerate audio devices: %s", e)
        return []
    out = []
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            out.append(DeviceInfo(i, d["name"], apis[d["hostapi"]]["name"],
                                  int(d["max_input_channels"]), float(d["default_samplerate"])))
    if sys.platform == "win32":
        # WASAPI first: lowest latency in shared mode and exposes all channels.
        out.sort(key=lambda d: (0 if "WASAPI" in d.host_api else 1, d.name))
    return out


def find_device(name: str, host_api: str = "") -> Optional[DeviceInfo]:
    for d in list_input_devices():
        if d.name == name and (not host_api or d.host_api == host_api):
            return d
    return None


class SoundDeviceBackend(AudioBackend):
    name = "input"

    def __init__(self, ring: AudioRingBuffer, channels: list[int], device: DeviceInfo,
                 sample_rate: int = 0, block_size: int = 512) -> None:
        super().__init__(ring, channels)
        self.device = device
        self.requested_sr = sample_rate
        self.block_size = block_size
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd
        # Open every channel the device offers (max 8) and pick ours in the callback:
        # WASAPI shared mode is picky about channel counts that differ from the mix format.
        n_ch = max(1, min(self.device.max_input_channels, 8))
        self.channels = [c for c in self.channels if c < n_ch] or [0]

        def callback(indata, frames, time_info, status):  # PortAudio thread
            if status:
                self.status_errors += 1
            self._push(indata, time.monotonic())

        extra = None
        if "WASAPI" in self.device.host_api:
            try:   # let Windows convert if the requested rate differs from the mix format
                extra = sd.WasapiSettings(auto_convert=True)
            except TypeError:
                extra = None
        rates = [int(self.requested_sr)] if self.requested_sr else []
        rates += [int(self.device.default_sample_rate or 48000), 48000, 44100]
        last_err = None
        for sr in dict.fromkeys(rates):
            try:
                self._stream = sd.InputStream(device=self.device.index, channels=n_ch, samplerate=sr,
                                              blocksize=self.block_size, dtype="float32",
                                              latency="low", callback=callback, extra_settings=extra)
                break
            except Exception as e:      # unsupported rate / format: try the next one
                last_err = e
                self._stream = None
        if self._stream is None:
            raise RuntimeError(f"cannot open {self.device.name}: {last_err}")
        self.input_latency = float(self._stream.latency or 0.0)
        self.sample_rate = int(self._stream.samplerate)
        self.device_name = self.device.name
        self.device_channels = n_ch
        self.anchor = TimeAnchor(self.sample_rate, self.input_latency)
        self._stream.start()
        self.running = True

    def alive(self) -> bool:
        s = self._stream
        return self.running and s is not None and bool(s.active)

    def stop(self) -> None:
        self.running = False
        s, self._stream = self._stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception as e:
                log.debug("stream close error: %s", e)


# --------------------------------------------------------------------------- WASAPI loopback
def loopback_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import pyaudiowpatch  # noqa: F401
        return True
    except ImportError:
        return False


def list_loopback_devices() -> list[DeviceInfo]:
    if not loopback_available():
        return []
    import pyaudiowpatch as pyaudio
    out = []
    p = pyaudio.PyAudio()
    try:
        for d in p.get_loopback_device_info_generator():
            out.append(DeviceInfo(int(d["index"]), d["name"], "WASAPI loopback",
                                  int(d["maxInputChannels"]), float(d["defaultSampleRate"]),
                                  kind="loopback"))
    finally:
        p.terminate()
    return out


class LoopbackBackend(AudioBackend):
    """WASAPI loopback through PyAudioWPatch (a PyAudio fork with loopback support).

    Captures what Windows plays on an output device. Useful when the DJ
    software runs on the same PC. NOTE: not verifiable on non-Windows hosts.
    """

    name = "loopback"

    def __init__(self, ring: AudioRingBuffer, channels: list[int], device: DeviceInfo,
                 block_size: int = 512) -> None:
        super().__init__(ring, channels)
        self.device = device
        self.block_size = block_size
        self._pa = None
        self._stream = None
        self._lock = threading.Lock()
        self._last_push = 0.0
        self._filler: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()

    def _push_locked(self, block: np.ndarray, t: float) -> None:
        with self._lock:
            self._push(block, t)
            self._last_push = t

    def _fill_silence(self) -> None:
        """WASAPI loopback delivers *no packets* while Windows plays silence. Feed
        zeros for the missing time so sample indices keep tracking the wall clock
        (the beat clock depends on it) and the analysis sees real silence."""
        while not self._stop_evt.wait(0.05):
            self._fill_once(time.monotonic())

    def _fill_once(self, now: float) -> int:
        with self._lock:
            gap = now - self._last_push
            if not (self._last_push and gap > 0.15 and self.sample_rate):
                return 0
            n = min(int(gap * self.sample_rate), self.ring.capacity)
            self._push(np.zeros(n, dtype=np.float32), now)
            self._last_push = now
            self.zero_frames = 0      # loopback silence is real silence, not a privacy block
            return n

    def start(self) -> None:
        import pyaudiowpatch as pyaudio
        self._pa = pyaudio.PyAudio()
        sr = int(self.device.default_sample_rate)
        n_ch = max(1, self.device.max_input_channels)

        def callback(in_data, frame_count, time_info, status):
            if status:
                self.status_errors += 1
            block = np.frombuffer(in_data, dtype=np.float32).reshape(-1, n_ch)
            self._push_locked(block, time.monotonic())
            return (None, pyaudio.paContinue)

        self._stream = self._pa.open(format=pyaudio.paFloat32, channels=n_ch, rate=sr, input=True,
                                     input_device_index=self.device.index,
                                     frames_per_buffer=self.block_size, stream_callback=callback)
        self.input_latency = float(self._stream.get_input_latency() or 0.0)
        self.sample_rate = sr
        self.device_name = self.device.name
        self.device_channels = n_ch
        self.anchor = TimeAnchor(sr, self.input_latency)
        self._last_push = time.monotonic()
        self._stop_evt.clear()
        self._stream.start_stream()
        self._filler = threading.Thread(target=self._fill_silence, name="loopback-filler", daemon=True)
        self._filler.start()
        self.running = True

    @property
    def digital_silence(self) -> bool:
        return False

    def alive(self) -> bool:
        # the silence filler keeps timestamps fresh, so ask the stream itself
        s = self._stream
        try:
            return self.running and s is not None and s.is_active()
        except Exception:
            return False

    def stop(self) -> None:
        self.running = False
        self._stop_evt.set()
        if self._filler is not None:
            self._filler.join(timeout=1)
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        except Exception as e:
            log.debug("loopback close error: %s", e)
        finally:
            self._stream = None
            if self._pa is not None:
                self._pa.terminate()
                self._pa = None


# --------------------------------------------------------------------------- file
class FileBackend(AudioBackend):
    """Plays an audio file into the ring buffer at real-time pace."""

    name = "file"

    def __init__(self, ring: AudioRingBuffer, channels: list[int], path: str,
                 loop: bool = True, block_size: int = 1024, speed: float = 1.0) -> None:
        super().__init__(ring, channels)
        self.path = path
        self.loop = loop
        self.block_size = block_size
        self.speed = speed
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.finished = False

    @property
    def digital_silence(self) -> bool:
        return False              # digital zeros in a file are legitimate silence

    def start(self) -> None:
        import soundfile as sf
        info = sf.info(self.path)
        self.sample_rate = int(info.samplerate)
        self.device_channels = int(info.channels)
        self.device_name = self.path
        self.anchor = TimeAnchor(self.sample_rate, 0.0)
        self._stop.clear()
        self.finished = False
        self._thread = threading.Thread(target=self._run, name="file-audio", daemon=True)
        self.running = True
        self._thread.start()

    def _run(self) -> None:
        import soundfile as sf
        try:
            with sf.SoundFile(self.path) as f:
                t0 = time.monotonic()
                frames_done = 0
                while not self._stop.is_set():
                    block = f.read(self.block_size, dtype="float32", always_2d=True)
                    if len(block) == 0:
                        if not self.loop:
                            break
                        f.seek(0)
                        continue
                    frames_done += len(block)
                    due = t0 + frames_done / self.sample_rate / self.speed
                    delay = due - time.monotonic()
                    if delay > 0:
                        self._stop.wait(delay)
                    self._push(block, time.monotonic())
        except Exception as e:
            self.error = str(e)
            log.error("file backend failed: %s", e)
        finally:
            self.finished = True
            self.running = False

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.running = False
