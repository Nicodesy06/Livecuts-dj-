"""Post-set review: an experienced multicam editor looks back at the edit of a night.

Input: the decision log of a session (``decisions-*.jsonl``) and the stills saved
at every cut (``review.journal``). Output: an HTML report next to the log with

* the numbers of the edit (cuts per minute, shot lengths, time per camera);
* rule checks that need no AI (very short shots, A-B-A-B, jump cuts by the
  framing metadata);
* optionally the critique of a vision-language model (local Ollama or Claude
  API, the slow eye's provider): every cut is shown as a before / after pair
  with its musical context and the director's own reason, and judged
  good / acceptable / weak / bad with named problems and a comment;
* suggestions derived *deterministically* from the problems found - settings to
  look at, never applied automatically.

What it cannot do: see what happened between two cuts (only two stills per cut
are kept) or hear the music beyond the logged state. The critique is advice to
be checked against the recording.
"""

from __future__ import annotations

import html
import json
import logging
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

from dj_director.ui.i18n import tr, tr_reason

log = logging.getLogger(__name__)

VERDICTS = ("good", "acceptable", "weak", "bad")
PROBLEMS = ("jump_cut", "axis_jump", "too_short", "too_long", "weak_shot", "bad_framing", "wrong_moment",
            "repetitive")
BATCH = 6

REVIEW_SYSTEM = """You are a senior multicamera editor reviewing, after the show, the live edit of a DJ-set stream
made by an automatic director. Each numbered row of the image is one cut: on the left the last picture
of the shot that ended, on the right the first picture of the shot that started (both stamped with the
cut number). The text gives, for each cut, the time, the shots and their lengths, the music and the
director's own reason.
Judge each cut as an editor would:
- good: motivated, a clear change of scale / angle / subject, the new shot shows something worth seeing.
- acceptable: fine but unremarkable.
- weak: little reason, little change, or a dull new shot.
- bad: a near-identical shot (jump cut), a front-back jump without a bridge, a poor or obstructed
  picture, a cut against the music.
problems (zero or more): jump_cut, axis_jump, too_short (the previous shot ended too soon to be read),
too_long (the previous shot overstayed), weak_shot (the new shot shows little), bad_framing (poor
composition, subject cut or blocked), wrong_moment (against the music: e.g. leaving the crowd on the
drop), repetitive (same pattern again and again).
comment: at most 20 words, concrete. note: one sentence on this part of the edit.
Judge only what the pictures and the text show. Answer with the JSON object only."""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "cuts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "k": {"type": "integer"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "problems": {"type": "array", "items": {"type": "string", "enum": list(PROBLEMS)}},
                    "comment": {"type": "string"},
                },
                "required": ["k", "verdict", "problems", "comment"],
                "additionalProperties": False,
            },
        },
        "note": {"type": "string"},
    },
    "required": ["cuts", "note"],
    "additionalProperties": False,
}

VERDICT_LABEL = {"good": "good", "acceptable": "acceptable", "weak": "weak", "bad": "bad"}
PROBLEM_LABEL = {
    "jump_cut": "jump cut", "axis_jump": "front-back jump", "too_short": "previous shot too short",
    "too_long": "previous shot too long", "weak_shot": "dull new shot", "bad_framing": "poor framing",
    "wrong_moment": "against the music", "repetitive": "repetitive",
}


@dataclass
class Cut:
    n: int
    t: float                      # seconds from the start of the session
    scene: str
    previous: Optional[str]
    reason: str
    state: str
    manual: bool = False
    duration: float = 0.0         # how long the new shot stayed on air
    prev_duration: float = 0.0    # how long the previous shot had been on air
    before: str = ""              # still paths, relative to the log folder
    after: str = ""
    flags: list = field(default_factory=list)      # rule checks (no AI)
    verdict: str = ""
    problems: tuple = ()
    comment: str = ""


@dataclass
class Review:
    path: Path
    cuts: list
    duration: float
    metrics: dict = field(default_factory=dict)
    model: str = ""
    ai_error: str = ""
    notes: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)

    @property
    def folder(self) -> Path:
        return self.path.parent

    def reviewed(self) -> list:
        return [c for c in self.cuts if c.verdict]


# ----------------------------------------------------------------------------- loading
def load_session(path: Path) -> Review:
    """Cuts of a session from its decision log (director cuts and manual changes)."""
    path = Path(path)
    records = []
    for line in path.read_text("utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    if not records:
        return Review(path, [], 0.0)
    times = [r["t"] for r in records if isinstance(r.get("t"), (int, float))]
    t0, t1 = (min(times), max(times)) if times else (0.0, 0.0)
    frames = {r.get("correlation_id"): r for r in records if r.get("kind") == "ShotFrames"}
    cuts: list[Cut] = []
    for r in records:
        if r.get("kind") != "DirectorDecision" or r.get("action") not in ("SWITCH", "OVERRIDE"):
            continue
        scene = r.get("candidate_camera") or r.get("current_camera")
        if not scene:
            continue
        prev = cuts[-1].scene if cuts else r.get("current_camera")
        if prev == scene and r.get("action") == "SWITCH":
            continue
        fr = frames.get(r.get("correlation_id")) if r.get("correlation_id") else None
        cuts.append(Cut(len(cuts) + 1, r["t"] - t0, scene, prev if prev != scene else None, r.get("reason", ""),
                        str(r.get("music_state", "")), r.get("action") == "OVERRIDE",
                        before=(fr or {}).get("before", ""), after=(fr or {}).get("after", "")))
    for a, b in zip(cuts, cuts[1:]):
        a.duration = b.t - a.t
        b.prev_duration = a.duration
    if cuts:
        cuts[-1].duration = max(0.0, (t1 - t0) - cuts[-1].t)
        if cuts[0].previous:
            cuts[0].prev_duration = cuts[0].t      # on air since the session started
    return Review(path, cuts, t1 - t0)


# ----------------------------------------------------------------------------- numbers and rules
def measure(review: Review, cameras: Sequence = ()) -> None:
    cuts = review.cuts
    minutes = max(review.duration / 60.0, 1e-6)
    per_cam: dict[str, float] = defaultdict(float)
    for c in cuts:
        per_cam[c.scene] += c.duration
    total = sum(per_cam.values()) or 1.0
    durs = [c.duration for c in cuts[:-1]] or [c.duration for c in cuts]
    review.metrics = {
        "cuts": len(cuts), "manual": sum(c.manual for c in cuts), "minutes": review.duration / 60.0,
        "cuts_per_minute": (len(cuts) - 1) / minutes if len(cuts) > 1 else 0.0,
        "median_shot_s": statistics.median(durs) if durs else 0.0,
        "longest_shot_s": max(durs) if durs else 0.0,
        "share": {k: v / total for k, v in sorted(per_cam.items(), key=lambda kv: -kv[1])},
    }
    by_name = {}
    if cameras:
        from dj_director.director.cameras import Camera
        by_name = {c.scene_name: Camera.from_config(c) for c in cameras}
    from dj_director.director.continuity import assess
    seq = [c.scene for c in cuts]
    for i, c in enumerate(cuts):
        if c.manual:
            continue
        if 0 < c.prev_duration < 3.0:
            c.flags.append("very short previous shot")
        if i >= 3 and seq[i] == seq[i - 2] and seq[i - 1] == seq[i - 3]:
            c.flags.append("A-B-A-B")
        if c.previous in by_name and c.scene in by_name:
            a = assess(by_name[c.previous], by_name[c.scene], seq[:i])
            if a.jump_cut:
                c.flags.append("jump cut (framing metadata)")
            if "crosses the axis front-back" in a.notes:
                c.flags.append("front-back jump (framing metadata)")


# ----------------------------------------------------------------------------- AI critique
def _load_rgb(folder: Path, rel: str) -> Optional[np.ndarray]:
    if not rel:
        return None
    from PySide6.QtGui import QImage

    from dj_director.visual.imaging import to_rgb
    img = QImage(str(folder / rel))
    return None if img.isNull() else to_rgb(img)


def pair_sheet(pairs: Sequence[tuple], tile_w: int = 384, tile_h: int = 216) -> np.ndarray:
    """One row per cut: before | after, both stamped with the cut's number in the batch."""
    from dj_director.visual.imaging import _stamp, resize
    out = np.full((len(pairs) * (tile_h + 4) - 4, 2 * tile_w + 4, 3), 24, np.uint8)
    for r, (k, before, after) in enumerate(pairs):
        for c, img in enumerate((before, after)):
            tile = resize(img, tile_w, tile_h).copy() if img is not None else np.full((tile_h, tile_w, 3), 48, np.uint8)
            _stamp(tile, str(k))
            y, x = r * (tile_h + 4), c * (tile_w + 4)
            out[y:y + tile_h, x:x + tile_w] = tile
    return out


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def batch_prompt(batch: Sequence[Cut], language: str) -> str:
    lines = []
    for k, c in enumerate(batch, 1):
        why = c.reason.split("; ")[0] if c.reason else "-"
        txt = (f"Cut {k} at {_mmss(c.t)}: {c.previous or '-'} (on air {c.prev_duration:.0f} s) -> {c.scene} "
               f"(then on air {c.duration:.0f} s). Music: {c.state.replace('_', ' ').lower() or 'unknown'}. "
               f"Director's reason: {why}.")
        if c.flags:
            txt += " Automatic checks: " + ", ".join(c.flags) + "."
        lines.append(txt)
    lang = "Italian" if language == "it" else "English"
    lines.append(f"Write 'comment' and 'note' in {lang}.")
    return "\n".join(lines)


def parse_batch(data: dict, batch: Sequence[Cut]) -> str:
    if not isinstance(data, dict):
        from dj_director.visual.providers import ProviderError
        raise ProviderError("unexpected answer")
    for item in data.get("cuts", []) or []:
        if not isinstance(item, dict):
            continue
        k = item.get("k")
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= len(batch):
            continue
        v = item.get("verdict")
        c = batch[k - 1]
        c.verdict = v if v in VERDICTS else ""
        c.problems = tuple(p for p in (item.get("problems") or []) if p in PROBLEMS)
        c.comment = str(item.get("comment", ""))[:200]
    return str(data.get("note", ""))[:300]


def critique(review: Review, provider, language: str = "it", timeout: float = 90.0,
             progress: Optional[Callable[[int, int], None]] = None) -> None:
    """Ask the model to judge every director cut that has stills (in batches); errors stop the AI part only."""
    from dj_director.visual.imaging import encode
    from dj_director.visual.providers import ProviderError
    todo = [c for c in review.cuts if not c.manual and (c.before or c.after) and c.previous]
    review.model = getattr(provider, "model", "")
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    for i, batch in enumerate(batches):
        if progress:
            progress(i, len(batches))
        pairs = [(k, _load_rgb(review.folder, c.before), _load_rgb(review.folder, c.after))
                 for k, c in enumerate(batch, 1)]
        image = encode(pair_sheet(pairs), "JPG", 85)
        try:
            data = provider.judge(image, REVIEW_SYSTEM, batch_prompt(batch, language), timeout,
                                  schema=REVIEW_SCHEMA)
            note = parse_batch(data, batch)
        except ProviderError as e:
            review.ai_error = str(e)
            log.warning("review stopped: %s", e)
            break
        if note:
            review.notes.append(f"{_mmss(batch[0].t)}-{_mmss(batch[-1].t)}: {note}")
    if progress:
        progress(len(batches), len(batches))


# ----------------------------------------------------------------------------- suggestions
def suggest(review: Review) -> list[str]:
    """Settings to look at, derived from the problems found (deterministic, never applied automatically)."""
    out: list[str] = []
    rev = [c for c in review.cuts if not c.manual]
    n = max(len(rev), 1)
    probs: Counter = Counter(p for c in rev for p in c.problems)
    flags: Counter = Counter(f.split(" (")[0] for c in rev for f in c.flags)
    by_scene: dict[str, Counter] = defaultdict(Counter)
    for c in rev:
        for p in c.problems:
            by_scene[p][c.scene] += 1

    def often(count: int) -> bool:
        return count >= 3 or count >= 0.15 * n

    def worst(problem: str) -> str:
        return ", ".join(s for s, _ in by_scene[problem].most_common(2))

    if often(probs["jump_cut"] + flags["jump cut"]):
        out.append(tr("Cuts between near-identical shots: set shot size and angle of every camera (Cameras page) "
                      "and give crops of the same camera different scales."))
    if often(probs["axis_jump"] + flags["front-back jump"]):
        out.append(tr("Front-back jumps: set the angle of the cameras, so the director passes through a wide or "
                      "overhead shot."))
    if often(probs["too_short"] + flags["very short previous shot"]):
        out.append(tr("Shots end before they can be read: raise the preferred lengths (Cameras page) or try the "
                      "Minimal preset."))
    if often(probs["too_long"]) or review.metrics.get("cuts_per_minute", 3) < 1.0:
        out.append(tr("Shots overstay: lower the preferred lengths or try the Dynamic preset."))
    for p in ("bad_framing", "weak_shot"):
        if often(probs[p]):
            out.append(tr("Check the framing of {0} (position, zoom, obstacles) or lower its weight.").format(
                worst(p)))
    if often(probs["wrong_moment"]):
        out.append(tr("Cuts against the music: repeat the latency calibration (Director page) and check that "
                      "the drop scene suits drops."))
    if often(probs["repetitive"] + flags["A-B-A-B"]):
        out.append(tr("Repetitive edit: add a camera or a crop, or lower the cooldowns."))
    share = review.metrics.get("share", {})
    if share and max(share.values()) > 0.85 and len(share) > 1:
        out.append(tr("One camera holds more than 85% of the time: check the weights and the coverage targets."))
    if not out:
        out.append(tr("No recurring problem found."))
    return out


# ----------------------------------------------------------------------------- report
CSS = """
:root{--bg:#ffffff;--fg:#1d1d22;--muted:#6b6b76;--line:#e3e3ea;--card:#f6f6f9;
--good:#1f8a4c;--ok:#5b6472;--weak:#b7791f;--bad:#c53030}
@media (prefers-color-scheme:dark){:root{--bg:#121217;--fg:#ececf1;--muted:#9a9aa8;--line:#2a2a33;
--card:#1b1b22;--good:#4ade80;--ok:#a1a1aa;--weak:#fbbf24;--bad:#f87171}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 8px}
.muted{color:var(--muted)}.cards{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:140px}
.card b{display:block;font-size:20px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}
img{width:160px;border-radius:4px;display:block}
.v-good{color:var(--good)}.v-acceptable{color:var(--ok)}.v-weak{color:var(--weak)}.v-bad{color:var(--bad)}
.v{font-weight:600}.wrap{overflow-x:auto}
"""


def write_html(review: Review, out: Optional[Path] = None) -> Path:
    out = Path(out) if out else review.path.with_name(review.path.stem + "-review.html")
    e = html.escape
    m = review.metrics
    verdicts = Counter(c.verdict for c in review.reviewed())
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "
             f"content='width=device-width,initial-scale=1'><title>{e(tr('Set review'))}</title>"
             f"<style>{CSS}</style></head><body><main>",
             f"<h1>{e(tr('Set review'))}</h1><p class='muted'>{e(review.path.name)} · "
             + (e(tr("critique by {0}").format(review.model)) if review.model else e(tr("no AI critique")))
             + "</p>"]
    cards = [(tr("Duration"), f"{m.get('minutes', 0):.0f} min"), (tr("Cuts"), str(m.get("cuts", 0))),
             (tr("Cuts per minute"), f"{m.get('cuts_per_minute', 0):.1f}"),
             (tr("Median shot"), f"{m.get('median_shot_s', 0):.0f} s")]
    for v in VERDICTS:
        if verdicts.get(v):
            cards.append((tr(VERDICT_LABEL[v]), str(verdicts[v])))
    parts.append("<div class='cards'>" + "".join(f"<div class='card'><span class='muted'>{e(k)}</span><b>{e(v)}</b>"
                                                 f"</div>" for k, v in cards) + "</div>")
    if review.ai_error:
        parts.append(f"<p class='v-bad'>{e(tr('AI critique stopped: {0}').format(review.ai_error))}</p>")
    parts.append(f"<h2>{e(tr('What to look at'))}</h2><ul>"
                 + "".join(f"<li>{e(s)}</li>" for s in review.suggestions) + "</ul>")
    if m.get("share"):
        parts.append(f"<h2>{e(tr('Time on air per camera'))}</h2><p>"
                     + " · ".join(f"{e(k)} {v * 100:.0f}%" for k, v in m["share"].items()) + "</p>")
    if review.notes:
        parts.append(f"<h2>{e(tr('Notes of the editor'))}</h2><ul>"
                     + "".join(f"<li>{e(n)}</li>" for n in review.notes) + "</ul>")
    parts.append(f"<h2>{e(tr('Cuts'))}</h2><div class='wrap'><table><tr><th>#</th><th>{e(tr('Time'))}</th>"
                 f"<th>{e(tr('Before'))}</th><th>{e(tr('After'))}</th><th>{e(tr('Cut'))}</th>"
                 f"<th>{e(tr('Verdict'))}</th></tr>")
    for c in review.cuts:
        imgs = [f"<img loading='lazy' src='{e(p)}' alt=''>" if p else "" for p in (c.before, c.after)]
        what = (f"<b>{e(c.previous or '-')} → {e(c.scene)}</b> <span class='muted'>"
                f"({c.prev_duration:.0f} s → {c.duration:.0f} s)</span><br>"
                + (e(tr("manual change")) if c.manual else e(tr_reason(c.reason))))
        if c.flags:
            what += "<br><span class='v-weak'>" + e(", ".join(tr(f) for f in c.flags)) + "</span>"
        verdict = ""
        if c.verdict:
            verdict = f"<span class='v v-{c.verdict}'>{e(tr(VERDICT_LABEL[c.verdict]))}</span>"
            if c.problems:
                verdict += "<br>" + e(", ".join(tr(PROBLEM_LABEL[p]) for p in c.problems))
            if c.comment:
                verdict += f"<br><span class='muted'>{e(c.comment)}</span>"
        parts.append(f"<tr><td>{c.n}</td><td>{_mmss(c.t)}</td><td>{imgs[0]}</td><td>{imgs[1]}</td>"
                     f"<td>{what}</td><td>{verdict}</td></tr>")
    parts.append("</table></div><p class='muted'>"
                 + e(tr("The critique sees two stills per cut and the logged music state: check it against the "
                        "recording before changing settings."))
                 + "</p></main></body></html>")
    out.write_text("".join(parts), "utf-8")
    return out


# ----------------------------------------------------------------------------- one call
def make_provider(vision_cfg, api_key: str = ""):
    """The slow eye's provider for the review (None when the slow eye is off)."""
    from dj_director.visual.providers import ClaudeProvider, OllamaProvider
    if vision_cfg.judge_provider == "local":
        return OllamaProvider(vision_cfg.ollama_url, vision_cfg.local_model)
    if vision_cfg.judge_provider == "cloud":
        return ClaudeProvider(api_key, vision_cfg.cloud_model)
    return None


def review_session(path: Path, provider=None, cameras: Sequence = (), language: str = "it",
                   progress: Optional[Callable[[int, int], None]] = None, out: Optional[Path] = None
                   ) -> tuple[Review, Path]:
    review = load_session(path)
    measure(review, cameras)
    if provider is not None and review.cuts:
        critique(review, provider, language, progress=progress)
    review.suggestions = suggest(review)
    return review, write_html(review, out)
