import pytest

from dj_director.core.events import BeatInfo, MusicFeatures, MusicSnapshot, MusicState
from dj_director.director.calibration import CalibrationRunner

P = 0.5   # 120 BPM
FEAT = MusicFeatures(-14, -16, -20, -25, 4, 0.1, 0, 0, 0, 1, 1500)


def snap(t, conf=0.9):
    k = int(t // P)
    beat = BeatInfo(120, conf, P, k * P, (k + 1) * P, k, -1, 0.0)
    return MusicSnapshot(t, MusicState.STABLE_GROOVE, 0.8, 0, MusicState.STABLE_GROOVE, FEAT, beat)


@pytest.mark.parametrize("offset", [0.0, 0.08, -0.05, 0.3])
def test_cuts_every_n_beats_sent_offset_early(offset):
    cal = CalibrationRunner("A", "B", every_beats=8)
    fired = []
    t = 0.0
    while t < 20:
        scene = cal.tick(t, snap(t), offset)
        if scene:
            fired.append((round(t, 3), scene))
        t = round(t + 0.01, 3)
    times = [f[0] for f in fired]
    assert len(fired) >= 4
    for x in times:
        k = (x + offset) / (8 * P)               # command time + offset = grid beat
        assert abs(k - round(k)) < 0.03
    assert [s for _, s in fired][:4] == ["B", "A", "B", "A"]


def test_no_cuts_without_confident_beat():
    cal = CalibrationRunner("A", "B")
    assert all(cal.tick(t / 10, snap(t / 10, conf=0.2), 0.04) is None for t in range(300))
    assert "steady beat" in cal.message


def test_same_camera_rejected():
    with pytest.raises(ValueError):
        CalibrationRunner("A", "A")
