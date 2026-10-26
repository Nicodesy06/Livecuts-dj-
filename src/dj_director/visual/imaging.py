"""Small image helpers on top of QtGui (already a dependency; works without a GUI).

Frames are kept as numpy arrays:
* ``gray``  uint8 (H, W)     analysis (motion, sharpness, exposure)
* ``rgb``   uint8 (H, W, 3)  the colour still used for the mosaic shown to the slow eye
"""

from __future__ import annotations

import base64
from typing import Optional, Sequence

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage


_warm = False


def warm_up() -> None:
    """Load the JPEG codec plugin once from the calling (main) thread.

    Qt loads image-format plugins lazily; letting two worker threads trigger the
    first load at the same time is avoidable, so services call this at start-up.
    """
    global _warm
    if _warm:
        return
    _warm = True
    tiny = np.zeros((8, 8, 3), np.uint8)
    img = QImage()
    img.loadFromData(encode(tiny, "JPG"))


def decode_data_uri(data: str) -> Optional[QImage]:
    """Decode the ``data:image/...;base64,...`` string returned by GetSourceScreenshot."""
    if not data:
        return None
    payload = data.split(",", 1)[1] if data.startswith("data:") else data
    try:
        raw = base64.b64decode(payload, validate=False)
    except (ValueError, TypeError):
        return None
    img = QImage()
    if not img.loadFromData(raw) or img.isNull():
        return None
    return img


def to_rgb(img: QImage) -> np.ndarray:
    q = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h, bpl = q.width(), q.height(), q.bytesPerLine()
    arr = np.frombuffer(q.constBits(), np.uint8, count=bpl * h).reshape(h, bpl)
    return arr[:, : w * 3].reshape(h, w, 3).copy()


def to_gray(rgb: np.ndarray) -> np.ndarray:
    return (rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114).astype(np.uint8)


def from_rgb(rgb: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()


def encode(rgb: np.ndarray, fmt: str = "JPG", quality: int = 85) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    from_rgb(rgb).save(buf, fmt, quality)
    buf.close()
    return bytes(ba.data())


def resize(rgb: np.ndarray, w: int, h: int) -> np.ndarray:
    """Nearest-neighbour resize (cheap; used for mosaic tiles only)."""
    sh, sw = rgb.shape[:2]
    ys = (np.arange(h) * sh / h).astype(int)
    xs = (np.arange(w) * sw / w).astype(int)
    return rgb[ys][:, xs]


# 3x5 bitmap digits: the tile number drawn in the corner, so the model can refer to
# cameras by number without depending on fonts (no QGuiApplication needed).
_DIGITS = {
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
    "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001001001001",
    "8": "111101111101111", "9": "111101111001111",
}


def _stamp(tile: np.ndarray, label: str, scale: int = 4) -> None:
    x0, y0 = 4, 4
    w = (len(label) * 4 - 1) * scale + 8
    h = 5 * scale + 8
    tile[y0:y0 + h, x0:x0 + w] = (0, 0, 0)
    for i, ch in enumerate(label):
        bits = _DIGITS.get(ch)
        if bits is None:
            continue
        for r in range(5):
            for c in range(3):
                if bits[r * 3 + c] == "1":
                    yy = y0 + 4 + r * scale
                    xx = x0 + 4 + (i * 4 + c) * scale
                    tile[yy:yy + scale, xx:xx + scale] = (255, 220, 0)


def mosaic(frames: Sequence[Optional[np.ndarray]], tile_w: int = 384, tile_h: int = 216) -> np.ndarray:
    """Grid of numbered tiles (1..N, row-major); missing frames are dark grey."""
    n = max(1, len(frames))
    cols = 1 if n == 1 else 2 if n <= 4 else 3
    rows = (n + cols - 1) // cols
    out = np.zeros((rows * tile_h + (rows - 1) * 4, cols * tile_w + (cols - 1) * 4, 3), np.uint8)
    out[:] = (24, 24, 24)
    for i, f in enumerate(frames):
        r, c = divmod(i, cols)
        tile = resize(f, tile_w, tile_h) if f is not None else np.full((tile_h, tile_w, 3), 48, np.uint8)
        tile = tile.copy()
        _stamp(tile, str(i + 1))
        y, x = r * (tile_h + 4), c * (tile_w + 4)
        out[y:y + tile_h, x:x + tile_w] = tile
    return out
