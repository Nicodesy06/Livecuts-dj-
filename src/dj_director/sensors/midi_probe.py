"""MIDI probe: does the DJ hardware send anything to this PC while you play?

Pioneer XDJ-RX3 note: according to its manual, mixer/deck MIDI messages are
sent in SOFTWARE CONTROL mode or with MIXER MODE = MIDI CONTROL, not while
playing standalone from USB. The probe settles it on the real unit in a
minute: it listens on every MIDI input and counts what arrives.

Windows only (winmm through ctypes, no extra dependency); elsewhere it
reports that no MIDI inputs are available.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

MIM_DATA = 0x3C3
CALLBACK_FUNCTION = 0x30000

KINDS = {0x80: "note off", 0x90: "note on", 0xA0: "aftertouch", 0xB0: "control change",
         0xC0: "program change", 0xD0: "channel pressure", 0xE0: "pitch bend"}


def describe(status: int, d1: int, d2: int) -> str:
    kind = KINDS.get(status & 0xF0)
    if kind is None:
        return f"system 0x{status:02X}"
    ch = (status & 0x0F) + 1
    if kind == "control change":
        return f"ch{ch} CC {d1} = {d2}"
    if kind in ("note on", "note off"):
        return f"ch{ch} {kind} {d1} vel {d2}"
    return f"ch{ch} {kind}"


@dataclass
class ProbeResult:
    devices: list[str]
    seconds: float
    counts: dict[str, int] = field(default_factory=dict)
    samples: dict[str, list[str]] = field(default_factory=dict)
    error: str = ""

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def summary(self) -> str:
        if self.error:
            return self.error
        if not self.devices:
            return "No MIDI input found on this PC."
        if not self.total:
            return (f"No MIDI messages in {self.seconds:.0f} s from: {', '.join(self.devices)}. "
                    "In standalone mode the unit does not send MIDI: the director relies on cameras and audio.")
        parts = [f"{d}: {n} messages (e.g. {', '.join(self.samples.get(d, [])[:3])})"
                 for d, n in self.counts.items() if n]
        return "MIDI received. " + "; ".join(parts)


def list_inputs() -> list[str]:
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class MIDIINCAPSW(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                    ("szPname", wintypes.WCHAR * 32), ("dwSupport", wintypes.DWORD)]

    winmm = ctypes.windll.winmm
    names = []
    for i in range(winmm.midiInGetNumDevs()):
        caps = MIDIINCAPSW()
        if winmm.midiInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            names.append(caps.szPname)
    return names


def probe(seconds: float = 30.0, progress: Callable[[float, int], None] = lambda left, n: None,
          stop: threading.Event | None = None) -> ProbeResult:
    """Listen on every MIDI input for ``seconds``; never raises."""
    devices = list_inputs()
    res = ProbeResult(devices, seconds)
    if not devices:
        return res
    import ctypes
    from ctypes import wintypes

    winmm = ctypes.windll.winmm
    lock = threading.Lock()
    PROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.UINT, ctypes.c_size_t, ctypes.c_size_t,
                              ctypes.c_size_t)

    def on_msg(handle, msg, instance, p1, p2):
        if msg != MIM_DATA:
            return
        name = devices[instance] if instance < len(devices) else "?"
        status, d1, d2 = p1 & 0xFF, (p1 >> 8) & 0x7F, (p1 >> 16) & 0x7F
        if status >= 0xF8:                 # clock / active sensing: not a gesture
            return
        with lock:
            res.counts[name] = res.counts.get(name, 0) + 1
            s = res.samples.setdefault(name, [])
            if len(s) < 8:
                s.append(describe(status, d1, d2))

    callback = PROC(on_msg)
    handles = []
    try:
        for i in range(len(devices)):
            h = wintypes.HANDLE()
            if winmm.midiInOpen(ctypes.byref(h), i, callback, i, CALLBACK_FUNCTION) == 0:
                winmm.midiInStart(h)
                handles.append(h)
        if not handles:
            res.error = "MIDI inputs found but none could be opened (in use by another program?)."
            return res
        t_end = time.monotonic() + seconds
        stop = stop or threading.Event()
        while not stop.is_set() and time.monotonic() < t_end:
            progress(t_end - time.monotonic(), res.total)
            stop.wait(0.25)
        res.seconds = seconds - max(0.0, t_end - time.monotonic())
    finally:
        for h in handles:
            winmm.midiInStop(h)
            winmm.midiInClose(h)
    return res
