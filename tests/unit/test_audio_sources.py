"""Device recognition (UCA222 & co.), source probing/ranking, digital silence, loopback gaps."""

import numpy as np

from dj_director.audio.backends import DeviceInfo, LoopbackBackend, SoundDeviceBackend
from dj_director.audio.devices import BUILTIN, LINE_INTERFACE, classify
from dj_director.audio.probe import ProbeResult, _dedupe, analyse_block, probe_devices, rank, recommend
from dj_director.audio.ringbuffer import AudioRingBuffer
from dj_director.simulation.synth import Section, generate

UCA_IN = DeviceInfo(3, "Microfono (USB Audio CODEC )", "Windows WASAPI", 2, 48000)
UCA_IN_MME = DeviceInfo(1, "Microfono (USB Audio CODEC )", "MME", 2, 44100)
UCA_LOOP = DeviceInfo(9, "Altoparlanti (USB Audio CODEC ) [Loopback]", "WASAPI loopback", 2, 48000, "loopback")
LAPTOP = DeviceInfo(4, "Microphone Array (Realtek(R) Audio)", "Windows WASAPI", 2, 48000)


def test_uca222_recognised_as_line_input():
    c = classify(UCA_IN.name)
    assert c.kind == LINE_INTERFACE and "UCA222" in c.product and not c.loopback
    assert classify(UCA_LOOP.name).loopback
    assert classify(LAPTOP.name).kind == BUILTIN
    assert "UCA222" in UCA_IN.label and UCA_IN.label.startswith("★")


def test_recommend_prefers_interface_over_laptop_mic_and_wasapi_over_mme():
    assert recommend([LAPTOP, UCA_IN_MME, UCA_IN]) is UCA_IN
    assert recommend([]) is None


def test_dedupe_keeps_best_host_api():
    assert _dedupe([UCA_IN_MME, UCA_IN, LAPTOP]) == [UCA_IN, LAPTOP]


def test_activity_distinguishes_music_from_hum():
    tr = generate(bpm=124, sections=[Section("g", 2, ("kick", "hat", "clap", "bass"))])
    _, _, music = analyse_block(tr.audio[: tr.sr], tr.sr)
    hum = 0.3 * np.sin(np.arange(44100) / 44100 * 2 * np.pi * 50)
    _, _, act_hum = analyse_block(hum, 44100)
    assert music > 0.3 and act_hum < 0.1


def test_probe_ranks_line_input_with_music_first():
    tr = generate(bpm=124, sections=[Section("g", 2, ("kick", "hat", "clap", "bass"))])
    music = tr.audio[: tr.sr]

    def fake_capture(dev, seconds):
        if dev is LAPTOP:               # room mic also hears the music, quieter
            return music * 0.1, tr.sr
        if dev is UCA_LOOP:             # DJ software not playing through the PC: silent
            return np.zeros(tr.sr, dtype=np.float32), tr.sr
        if dev is UCA_IN:
            return music, tr.sr
        raise OSError("device busy")

    broken = DeviceInfo(7, "Broken thing", "MME", 2, 44100)
    res = probe_devices(devices=[LAPTOP, UCA_LOOP, broken, UCA_IN], capture=fake_capture)
    assert res[0].device is UCA_IN and res[0].has_signal
    assert res[1].device is LAPTOP
    assert res[-1].device is broken and res[-1].error


def test_loopback_with_music_beats_silent_input():
    r_in = ProbeResult(UCA_IN, -90.0, -80.0, 0.0)
    r_loop = ProbeResult(UCA_LOOP, -18.0, -3.0, 0.8)
    assert rank([r_in, r_loop])[0] is r_loop


def test_digital_silence_flag():
    b = SoundDeviceBackend(AudioRingBuffer(48000 * 4), [0, 1], UCA_IN)
    b.sample_rate = 48000
    for _ in range(200):
        b._push(np.zeros((512, 2), dtype=np.float32), 0.0)
    assert b.digital_silence
    b._push(np.full((512, 2), 1e-4, dtype=np.float32), 0.0)    # analogue noise floor
    assert not b.digital_silence


def test_loopback_silence_gap_is_filled_with_zeros():
    ring = AudioRingBuffer(48000 * 4)
    b = LoopbackBackend(ring, [0, 1], UCA_LOOP)
    b.sample_rate = 48000
    b._last_push = 100.0
    assert b._fill_once(100.1) == 0                # short gaps are normal callback jitter
    n = b._fill_once(102.0)                        # 2 s without packets = Windows playing silence
    assert n == 96000 and ring.write_total == 96000
    assert not b.digital_silence
