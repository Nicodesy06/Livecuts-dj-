"""Lifecycle of every Program switch we ask OBS for (P0 reliability).

Each request has a unique correlation id and moves through explicit states:

    REQUESTED -> CONFIRMED                     OBS accepted it (response) / Program changed
              -> FAILED      (timeout, error)  -> CONFIRMED (late) if OBS applies it anyway
              -> CANCELLED   (never sent: superseded by a newer cut, manual change, stop)

The tracker is pure bookkeeping (no I/O, no threads): the controller feeds it
from the director thread and the UI reads :meth:`snapshot`.

Confirmed on-air time (the "real" exposure) is accumulated by
:class:`AirtimeLedger` from Program changes actually reported by OBS, ours or
manual, so statistics never count a cut that did not happen.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional


class SwitchStatus(str, Enum):
    REQUESTED = "REQUESTED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class SwitchRecord:
    cid: int
    scene: str
    previous: Optional[str]
    t_request: float
    status: SwitchStatus = SwitchStatus.REQUESTED
    t_status: float = 0.0
    detail: str = ""             # error / cancel reason / "late"
    round_trip_ms: float = 0.0


class SwitchTracker:
    def __init__(self, keep: int = 64) -> None:
        self._recs: dict[int, SwitchRecord] = {}
        self._order: deque[int] = deque(maxlen=keep)

    # ------------------------------------------------------------------ transitions
    def request(self, cid: int, scene: str, previous: Optional[str], t: float) -> SwitchRecord:
        rec = SwitchRecord(cid, scene, previous, t, SwitchStatus.REQUESTED, t)
        self._put(rec)
        return rec

    def confirmed(self, cid: int, t: float, rtt_ms: float = 0.0) -> Optional[SwitchRecord]:
        rec = self._recs.get(cid)
        if rec is None or rec.status != SwitchStatus.REQUESTED:
            return None
        rec = replace(rec, status=SwitchStatus.CONFIRMED, t_status=t, round_trip_ms=rtt_ms)
        self._put(rec)
        return rec

    def failed(self, cid: int, t: float, error: str) -> Optional[SwitchRecord]:
        rec = self._recs.get(cid)
        if rec is None or rec.status != SwitchStatus.REQUESTED:
            return None
        rec = replace(rec, status=SwitchStatus.FAILED, t_status=t, detail=error)
        self._put(rec)
        return rec

    def cancelled(self, cid: int, t: float, reason: str) -> Optional[SwitchRecord]:
        rec = self._recs.get(cid)
        if rec is None or rec.status != SwitchStatus.REQUESTED:
            return None
        rec = replace(rec, status=SwitchStatus.CANCELLED, t_status=t, detail=reason)
        self._put(rec)
        return rec

    def late_confirmation(self, scene: str, t: float, window: float = 10.0) -> Optional[SwitchRecord]:
        """OBS put ``scene`` in Program after we gave the request up (timeout): it happened after all."""
        for cid in reversed(self._order):
            rec = self._recs[cid]
            if t - rec.t_request > window:
                break
            if rec.scene == scene and rec.status == SwitchStatus.FAILED and "timed out" in rec.detail:
                rec = replace(rec, status=SwitchStatus.CONFIRMED, t_status=t, detail="late (after timeout)")
                self._put(rec)
                return rec
        return None

    # ------------------------------------------------------------------ queries
    def get(self, cid: int) -> Optional[SwitchRecord]:
        return self._recs.get(cid)

    def pending(self) -> list[SwitchRecord]:
        return [self._recs[c] for c in self._order if self._recs[c].status == SwitchStatus.REQUESTED]

    def recent(self, n: int = 10) -> list[SwitchRecord]:
        return [self._recs[c] for c in list(self._order)[-n:]]

    def last_problem(self) -> Optional[SwitchRecord]:
        for cid in reversed(self._order):
            rec = self._recs[cid]
            if rec.status in (SwitchStatus.FAILED, SwitchStatus.CANCELLED):
                return rec
        return None

    def expire(self, t: float, timeout: float) -> list[SwitchRecord]:
        """Requests with no answer after ``timeout`` s become FAILED (lost response / disconnect)."""
        out = []
        for rec in self.pending():
            if t - rec.t_request > timeout:
                r = self.failed(rec.cid, t, "no answer from OBS (timed out)")
                if r is not None:
                    out.append(r)
        return out

    def _put(self, rec: SwitchRecord) -> None:
        if rec.cid not in self._recs:
            self._order.append(rec.cid)
            if len(self._recs) > self._order.maxlen:
                live = set(self._order)
                for k in [k for k in self._recs if k not in live]:
                    del self._recs[k]
        self._recs[rec.cid] = rec


class AirtimeLedger:
    """Time each scene was really in Program (from OBS reports, or decisions in Shadow)."""

    def __init__(self, keep_s: float = 3600.0) -> None:
        self.keep_s = keep_s
        self.segments: deque[list] = deque()       # [scene, start, end|None]

    def on_program(self, scene: Optional[str], t: float) -> None:
        if self.segments and self.segments[-1][2] is None:
            if self.segments[-1][0] == scene:
                return
            self.segments[-1][2] = t
        if scene:
            self.segments.append([scene, t, None])
        while self.segments and self.segments[0][2] is not None and t - self.segments[0][2] > self.keep_s:
            self.segments.popleft()

    def close(self, t: float) -> None:
        if self.segments and self.segments[-1][2] is None:
            self.segments[-1][2] = t

    def current(self) -> Optional[str]:
        return self.segments[-1][0] if self.segments and self.segments[-1][2] is None else None

    def shares(self, now: float, window: float = 300.0) -> dict[str, float]:
        """Seconds per scene inside the last ``window`` seconds."""
        t0 = now - window
        acc: dict[str, float] = {}
        for scene, a, b in self.segments:
            b = now if b is None else b
            if b <= t0:
                continue
            acc[scene] = acc.get(scene, 0.0) + max(0.0, min(b, now) - max(a, t0))
        return acc
