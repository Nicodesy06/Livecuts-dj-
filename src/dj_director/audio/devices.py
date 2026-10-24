"""Recognise audio devices by name so the UI can suggest the right source.

Windows names every USB *input* endpoint "Microphone (...)" and every output
"Speakers (...)", even for line-level interfaces. A Behringer UCA202/UCA222
(or UFO202), for instance, appears as "Microfono (USB Audio CODEC)" and
"Altoparlanti (USB Audio CODEC)": the "microphone" is really the RCA line
input, which is exactly what DJ Director should listen to.

This module only *classifies* names; it never opens devices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LINE_INTERFACE = "line_interface"   # USB audio interface / line input: preferred
DJ_HARDWARE = "dj_hardware"         # DJ mixer / controller with its own sound card
BUILTIN = "builtin"                 # laptop mic, webcam, headset: room sound, avoid
VIRTUAL = "virtual"                 # virtual cables, stereo mix
UNKNOWN = "unknown"

_RULES: list[tuple[str, str, str]] = [
    # (regex on lower-case name, class, friendly product name)
    (r"usb audio codec", LINE_INTERFACE, "Behringer UCA202 / UCA222 / UFO202 (USB Audio CODEC)"),
    (r"behringer|u-phoria|umc\d", LINE_INTERFACE, "Behringer audio interface"),
    (r"focusrite|scarlett", LINE_INTERFACE, "Focusrite Scarlett"),
    (r"audient|motu|presonus|steinberg|\bur\d{2}|zoom|tascam|m-audio|ssl \d", LINE_INTERFACE,
     "USB audio interface"),
    (r"pioneer|ddj|djm|xdj|rekordbox", DJ_HARDWARE, "Pioneer DJ hardware"),
    (r"traktor|audio [2-9]|native instruments|kontrol", DJ_HARDWARE, "Native Instruments DJ hardware"),
    (r"denon|rane|numark|hercules|reloop|allen|xone|roland|serato|mixars", DJ_HARDWARE, "DJ hardware"),
    (r"cable|vb-audio|voicemeeter|stereo mix|missaggio stereo|virtual", VIRTUAL, "virtual device"),
    (r"array|webcam|camera|headset|hands-free|bluetooth|airpods|realtek|intel|"
     r"high definition audio|smart sound|conexant|built-in|integrat", BUILTIN, "built-in / headset microphone"),
]

_HINTS = {
    LINE_INTERFACE: "Line input of an audio interface: connect the mixer REC/booth/master out here.",
    DJ_HARDWARE: "DJ hardware sound card: choose the channels carrying the master (often 1+2).",
    BUILTIN: "Probably a room microphone: it hears the speakers and the crowd, not the clean mix.",
    VIRTUAL: "Virtual device: works if your DJ software outputs to it.",
    UNKNOWN: "",
}


@dataclass(frozen=True)
class DeviceClass:
    kind: str
    product: str
    hint: str
    loopback: bool

    @property
    def preference(self) -> int:
        """Higher = better default choice for capturing the DJ mix."""
        base = {LINE_INTERFACE: 40, DJ_HARDWARE: 35, VIRTUAL: 15, UNKNOWN: 10, BUILTIN: 0}[self.kind]
        return base - (5 if self.loopback else 0)


def classify(name: str) -> DeviceClass:
    low = name.lower()
    loop = "loopback" in low
    for pattern, kind, product in _RULES:
        if re.search(pattern, low):
            return DeviceClass(kind, product, _HINTS[kind], loop)
    return DeviceClass(UNKNOWN, "", _HINTS[UNKNOWN], loop)


def friendly_label(name: str, host_api: str, channels: int) -> str:
    c = classify(name)
    prefix = "★ " if c.kind in (LINE_INTERFACE, DJ_HARDWARE) else ""
    product = f" — {c.product}" if c.product and c.kind != BUILTIN else ""
    return f"{prefix}{name}{product}  [{host_api}, {channels} ch]"
