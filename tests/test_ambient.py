"""The ambient tier, verified by rendering: the reverb rings for as long as
its size says and never runs away, the drone sits on its harmonic series and
breathes slowly, the pluck plays its note and dies away, the noise bed stays
in its band, the loop LFO comes round exactly, and the scale walk is
reproducible and thinned by its density.

Every check has a control that would fail it: a semitone-off series, a dry
impulse with no tail, a steady drone, a tonal signal in the noise band, a
free-running ``[phasor~]``, a different seed.
"""

from __future__ import annotations

import re
import subprocess

import numpy as np
import pytest

from pdbuild import Patch
from pdbuild.modules import (
    Stereo, drone, loop_lfo, noise_bed, pluck, reverb, scale_walk,
)
from pdbuild.modules.ambient import _loop_rate, _weight_table

pytest.importorskip("pdverify")
from pdverify import analyze                       # noqa: E402
from pdverify.music import harmonicity, loop_similarity   # noqa: E402
from pdverify.render import RenderSpec, render      # noqa: E402
from pdverify.pd_locate import discover             # noqa: E402

try:
    discover()
    _HAVE_PD = True
except Exception:
    _HAVE_PD = False

needs_pd = pytest.mark.skipif(not _HAVE_PD, reason="needs a Pd install")
SR = 44100


def _render(build, dur=2.0, sr=SR):
    """build(patch) returns a node or a Stereo; wire it to dac~ and render.
    Returns (Report, samples[frames, 2], AudioBuffer)."""
    p = Patch(500, 400, 10)
    out = build(p)
    dac = p.obj("dac~")
    if isinstance(out, Stereo):
        p.link(out.left, 0, dac, 0)
        p.link(out.right, 0, dac, 1)
    else:
        p.link(out, 0, dac, 0)
        p.link(out, 0, dac, 1)
    assert not p.unvalidated(), p.unvalidated()      # every connection checked
    res = render(p.render(), RenderSpec(duration=dur, sr=sr))
    assert "error" not in res.pd_console.lower(), res.pd_console
    return analyze(res.audio), res.audio.samples, res.audio


def _impulse(p, at_ms=100):
    """A one-sample click at ``at_ms``."""
    d = p.obj(f"del {at_ms}")
    p.link(p.loadbang(), 0, d, 0)
    m = p.msg("1, 0 0 0.0227")
    p.link(d, 0, m, 0)
    v = p.obj("vline~")
    p.link(m, 0, v, 0)
    return v


def _white(p, rms=0.1):
    n = p.obj("noise~")
    g = p.obj(f"*~ {rms * 1.732:g}")
    p.link(n, 0, g, 0)
    return g


def _sine(p, freq=330.0, amp=0.3):
    o = p.obj(f"osc~ {freq}")
    g = p.obj(f"*~ {amp}")
    p.link(o, 0, g, 0)
    return g


def _rms(x, t0, t1, sr=SR):
    seg = x[int(t0 * sr):int(t1 * sr)]
    return float(np.sqrt(np.mean(seg ** 2)))


def _env_db(x, win_s=0.05, sr=SR):
    """Short-window RMS in dB, one value per ``win_s``."""
    w = int(win_s * sr)
    frames = x[:len(x) // w * w].reshape(-1, w)
    return 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-12)


def _rt60(x, sr=SR):
    """Schroeder backward integration; T20 (-5 to -25 dB) times 3."""
    edc = np.cumsum((x ** 2)[::-1])[::-1]
    db = 10 * np.log10(edc / edc[0] + 1e-30)
    return 3 * (np.argmax(db < -25) - np.argmax(db < -5)) / sr


def _psd(x, sr=SR, nfft=4096):
    win = np.hanning(nfft)
    segs = [x[i:i + nfft] * win for i in range(0, len(x) - nfft, nfft // 2)]
    return np.fft.rfftfreq(nfft, 1 / sr), np.mean([np.abs(np.fft.rfft(s)) ** 2 for s in segs], axis=0)


# --------------------------------------------------------------------------- #
# reverb
# --------------------------------------------------------------------------- #

@needs_pd
def test_reverb_rt60_tracks_size():
    """An impulse rings out, and the measured RT60 follows the nominal
    0.4 * 25**size (measured 0.72 / 1.86 / 4.9 s against 0.76 / 2.0 / 5.25)."""
    measured = []
    for size in (0.2, 0.5, 0.8):
        nominal = 0.4 * 25 ** size
        _, x, _ = _render(lambda p: reverb(p, _impulse(p), size=size, damping=0.0, mix=1.0),
                          dur=max(4.0, nominal * 1.6))
        rt = _rt60(x[:, 0])
        assert rt == pytest.approx(nominal, rel=0.2), (size, rt, nominal)
        measured.append(rt)
    assert measured == sorted(measured)


@needs_pd
def test_reverb_tail_is_the_reverb():
    """Negative control: the dry click has nothing after it; the wet one does."""
    _, dry, _ = _render(lambda p: reverb(p, _impulse(p), mix=0.0), dur=2.0)
    _, wet, _ = _render(lambda p: reverb(p, _impulse(p), size=0.6, mix=0.5), dur=2.0)
    assert _rms(dry[:, 0], 0.5, 1.5) == 0.0
    assert _rms(wet[:, 0], 0.5, 1.5) > 1e-4             # a one-sample click: measured 4.8e-4


@needs_pd
def test_reverb_mix_zero_is_the_input_sample_for_sample():
    _, dry, _ = _render(lambda p: _sine(p), dur=1.0)
    _, wet, _ = _render(lambda p: reverb(p, _sine(p), mix=0.0), dur=1.0)
    assert np.array_equal(dry, wet)


@needs_pd
def test_reverb_stays_stable_at_its_longest():
    """20 s of noise into the biggest, undamped, most diffuse setting (RT60
    10 s): finite, unclipped, and the level has settled -- the last 5 s are
    no louder than the 5 s before them (measured 0.072 vs 0.072)."""
    rep, x, _ = _render(lambda p: reverb(p, _white(p), size=1.0, damping=0.0, mix=1.0,
                                         diffusion=0.75), dur=20.0)
    assert not rep.has_nan_inf and not rep.is_clipped
    assert np.abs(x).max() < 0.6
    assert _rms(x, 15, 20) < _rms(x, 5, 10) * 1.05


@needs_pd
def test_reverb_level_is_normalised_across_sizes():
    """Noise at RMS 0.1 comes out of the tank at a similar level for a small
    room and a long hall (measured 0.080 / 0.062 / 0.050)."""
    levels = []
    for size in (0.2, 0.6, 1.0):
        _, x, _ = _render(lambda p: reverb(p, _white(p), size=size, mix=1.0), dur=8.0)
        levels.append(_rms(x, 5, 8))
    assert max(levels) / min(levels) < 2.0, levels           # within 6 dB
    assert all(0.03 < v < 0.15 for v in levels), levels


@needs_pd
def test_reverb_is_wide_stereo():
    """The two taps are decorrelated (|r| measured ~0.02-0.05), both live."""
    rep, x, _ = _render(lambda p: reverb(p, _impulse(p), size=0.6, mix=1.0), dur=3.0)
    assert rep.channels == 2
    assert min(rep.channel_peaks_dbfs) > -40
    assert abs(np.corrcoef(x[:, 0], x[:, 1])[0, 1]) < 0.2


@needs_pd
def test_reverb_damping_darkens_the_tail():
    """The in-loop lowpass: highs die before lows (late-tail centroid
    measured 4.3 kHz undamped, 330 Hz at damping 0.8)."""
    cents = []
    for damping in (0.0, 0.8):
        _, x, _ = _render(lambda p: reverb(p, _impulse(p), size=0.6, damping=damping, mix=1.0),
                          dur=2.6)
        cents.append(analyze(x[int(1.0 * SR):int(2.5 * SR), 0], SR).centroid_hz)
    assert cents[1] < cents[0] * 0.3, cents


@needs_pd
def test_reverb_stereo_input_keeps_a_stereo_dry_path():
    """A left-only input stays left in the dry path; the tank fills both."""
    def build(p):
        left = _sine(p, 440)
        silent = p.obj("sig~ 0")
        return reverb(p, Stereo(left, silent), mix=0.0)
    _, x, _ = _render(build, dur=1.0)
    assert _rms(x[:, 0], 0.2, 1.0) > 0.1 and _rms(x[:, 1], 0.2, 1.0) == 0.0

    def wet(p):
        return reverb(p, [_sine(p, 440), p.obj("sig~ 0")], mix=1.0)
    _, y, _ = _render(wet, dur=1.5)
    assert _rms(y[:, 1], 0.5, 1.5) > 0.01


# --------------------------------------------------------------------------- #
# drone
# --------------------------------------------------------------------------- #

@needs_pd
def test_drone_sits_on_its_harmonic_series():
    rep, _, buf = _render(lambda p: drone(p, 110.0), dur=4.0)
    assert harmonicity(buf, 110.0) > 0.99                # measured 0.9999
    assert harmonicity(buf, 116.54) < 0.2                # A#2: measured 0.0
    assert rep.f0_hz == pytest.approx(110.0, abs=1.0)
    for k in (1, 2, 3, 4, 5):
        assert any(abs(hz - 110 * k) < 8 for hz in rep.top_partials), (k, rep.top_partials)
    assert not rep.is_clipped and rep.peak_dbfs < -10      # peaks stay under gain 0.2


@needs_pd
def test_drone_breathes_slowly():
    """Over 30 s the level wanders several dB (measured 5.4) but never jumps:
    the biggest change between neighbouring 50 ms windows is a fraction of
    a dB (measured 0.06). Control: lfo_depth=0 is steady (0.01 dB)."""
    _, x, _ = _render(lambda p: drone(p, 110.0), dur=30.0)
    db = _env_db(x[:, 0])
    assert db.max() - db.min() > 3.0
    assert np.abs(np.diff(db)).max() < 0.5
    _, y, _ = _render(lambda p: drone(p, 110.0, lfo_depth=0.0), dur=4.0)
    dy = _env_db(y[:, 0])[4:]
    assert dy.max() - dy.min() < 0.1


@needs_pd
def test_drone_with_loop_s_repeats_over_the_loop():
    """loop_s rounds every LFO to whole cycles per loop, so with a pitch that
    also fits (110 Hz x 8 s) the drone comes round sample for sample: the
    second loop matches the first to within 16-bit rounding, the unrounded
    drone does not."""
    _, x, _ = _render(lambda p: drone(p, 110.0, loop_s=8.0), dur=17.0)
    _, y, _ = _render(lambda p: drone(p, 110.0), dur=17.0)
    L = 8 * SR
    seg = slice(SR, 3 * SR)
    looped = np.abs(x[seg.start + L:seg.stop + L, 0] - x[seg, 0]).max()
    free = np.abs(y[seg.start + L:seg.stop + L, 0] - y[seg, 0]).max()
    assert looped < 2e-3, looped
    assert free > 10 * looped, (free, looped)


def test_loop_rate_rounds_to_whole_cycles():
    assert _loop_rate(0.07, 20.0) == pytest.approx(1 / 20)
    assert _loop_rate(0.3, 10.0) == pytest.approx(0.3)
    assert _loop_rate(0.01, 10.0) == pytest.approx(0.1)     # at least one cycle
    assert _loop_rate(0.07, None) == 0.07


# --------------------------------------------------------------------------- #
# pluck
# --------------------------------------------------------------------------- #

def _metro_pluck(period, pitch, **kw):
    def build(p):
        m = p.obj(f"metro {period}")
        p.init(m, 1)
        return pluck(p, m, pitch, **kw)
    return build


@needs_pd
@pytest.mark.parametrize("hz", [220.0, 880.0, 1500.0])
def test_pluck_plays_the_pitch(hz):
    """Exact at any pitch, including above the 689 Hz ceiling of a one-block
    Karplus-Strong loop (measured 219.99 / 880.01 / 1500.01 Hz)."""
    rep, _, _ = _render(_metro_pluck(1000, hz), dur=3.0)
    assert not rep.is_silent and not rep.is_clipped
    assert rep.f0_hz == pytest.approx(hz, rel=0.005)


@needs_pd
def test_pluck_is_percussive():
    """One pluck: up in ~3 ms (sample-level rise, measured 3.3 ms), then
    gone; the report reads percussive."""
    def build(p):
        d = p.obj("del 200")
        p.link(p.loadbang(), 0, d, 0)
        return pluck(p, d, 330.0, decay=2.0)
    rep, x, _ = _render(build, dur=3.0)
    a = np.abs(x[:, 0])
    rise_ms = (np.argmax(a) - np.argmax(a > 1e-4)) / SR * 1000
    assert rise_ms < 10, rise_ms
    assert rep.sustain_ratio < 0.5
    assert _rms(x[:, 0], 2.5, 3.0) < _rms(x[:, 0], 0.2, 0.7) * 0.05


@needs_pd
@pytest.mark.parametrize("decay", [1.0, 3.0])
def test_pluck_decay_is_its_rt60(decay):
    """The fundamental falls 60 dB in ``decay`` s, so the whole tone is 40 dB
    down at about 2/3 of it (measured 0.70 s and 2.00 s)."""
    def build(p):
        b = p.msg("bang")
        p.link(p.loadbang(), 0, b, 0)
        return pluck(p, b, 330.0, decay=decay)
    _, x, _ = _render(build, dur=decay + 1.5)
    db = _env_db(x[:, 0])
    db -= db.max()
    t40 = np.argmax(db < -40) * 0.05
    assert t40 == pytest.approx(decay * 2 / 3, rel=0.15), t40


@needs_pd
def test_pluck_brightness_raises_the_centroid():
    dark, _, _ = _render(_metro_pluck(500, 220.0, brightness=0.1), dur=2.0)
    bright, _, _ = _render(_metro_pluck(500, 220.0, brightness=0.8), dur=2.0)
    assert bright.centroid_hz > dark.centroid_hz * 1.5        # measured 221 -> 399 Hz
    assert dark.f0_hz == pytest.approx(bright.f0_hz, rel=0.01)


def _three_notes(voices):
    """A3, C#4, E4 a quarter second apart, pitch-triggered (trigger=None)."""
    def build(p):
        hz = p.obj("f")
        for k, v in enumerate((220.0, 277.18, 329.63)):
            d = p.obj(f"del {100 + 250 * k}")
            p.link(p.loadbang(), 0, d, 0)
            m = p.msg(f"{v}")
            p.link(d, 0, m, 0)
            p.link(m, 0, hz, 0)
        return pluck(p, None, hz, decay=3.0, voices=voices)
    return build


@needs_pd
def test_pluck_voices_ring_under_each_other():
    """Four voices: after the third note all three still sound (a chord).
    Control: one voice re-pitches its own tail, so only the last note is
    left."""
    _, poly, _ = _render(_three_notes(4), dur=1.6)
    _, mono, _ = _render(_three_notes(1), dur=1.6)

    def present(x):
        f, P = _psd(x[int(0.7 * SR):int(1.6 * SR), 0], nfft=16384)
        floor = np.median(P)
        return [P[np.abs(f - hz) < 3].max() > floor * 1e3 for hz in (220.0, 277.18, 329.63)]
    assert present(poly) == [True, True, True]
    assert present(mono) == [False, False, True]


# --------------------------------------------------------------------------- #
# noise bed
# --------------------------------------------------------------------------- #

def _band_share(x, lo, hi, k=1.0, sr=SR):
    F = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / sr)
    return float(F[(f >= lo / k) & (f <= hi * k)].sum() / F.sum())


@needs_pd
@pytest.mark.parametrize("lo,hi", [(300.0, 3000.0), (2000.0, 6000.0)])
def test_noise_bed_stays_in_its_band(lo, hi):
    """24 dB/octave edges: measured 0.88 / 0.84 of the energy inside
    lo..hi (white noise's skirt above ``hi`` is the rest), 0.97 within a
    quarter beyond the edges, 0.99 within a half. RMS is ``gain``."""
    rep, x, _ = _render(lambda p: noise_bed(p, lo=lo, hi=hi, flutter=0, swell_depth=0, gain=0.1),
                        dur=3.0)
    s = x[:, 0]
    assert _band_share(s, lo, hi) > 0.8
    assert _band_share(s, lo, hi, 1.25) > 0.95
    assert _band_share(s, lo, hi, 1.5) > 0.98
    assert float(np.sqrt(np.mean(s ** 2))) == pytest.approx(0.1, rel=0.1)
    assert not rep.is_clipped


@needs_pd
@pytest.mark.parametrize("sr", [44100, 48000])
def test_noise_bed_edges_follow_the_sample_rate(sr):
    """The biquads are recomputed from [samplerate~] at load: the edges sit
    at -3 dB at 44.1 and 48 kHz alike (measured -2.6..-2.8 dB; coefficients
    left at 44.1 kHz would put the top edge at -1.8 dB at 48 kHz)."""
    _, x, _ = _render(lambda p: noise_bed(p, lo=300, hi=3000, flutter=0, swell_depth=0),
                      dur=6.0, sr=sr)
    f, P = _psd(x[:, 0], sr)
    ref = P[(f > 800) & (f < 1200)].mean()

    def at(hz):
        return 10 * np.log10(P[np.abs(f - hz) < 40].mean() / ref)
    assert -3.6 < at(3000) < -2.0, at(3000)
    assert -3.6 < at(300) < -2.0, at(300)
    assert at(6000) < -20


def _inband_flatness(x, lo, hi, sr=SR):
    f, P = _psd(x, sr)
    band = P[(f >= lo * 1.2) & (f <= hi / 1.2)]
    return float(np.exp(np.mean(np.log(band + 1e-30))) / np.mean(band))


@needs_pd
def test_noise_bed_is_noise_not_tone():
    """Spectral flatness inside the band: noise reads flat, a drone placed in
    the same band does not. (pdverify's whole-spectrum flatness reads ~0 for
    any band-limited sound, so it cannot tell these apart.)"""
    _, x, _ = _render(lambda p: noise_bed(p, lo=300, hi=3000, flutter=0, swell_depth=0), dur=3.0)
    _, y, _ = _render(lambda p: drone(p, 440.0, lfo_depth=0), dur=3.0)
    noisy, tonal = _inband_flatness(x[:, 0], 300, 3000), _inband_flatness(y[:, 0], 300, 3000)
    assert noisy > 0.8, noisy                      # measured 0.99
    assert tonal < 0.01, tonal                     # measured 4e-6


@needs_pd
def test_noise_bed_swells_at_its_rate():
    """The level envelope's strongest slow component is the swell rate."""
    _, x, _ = _render(lambda p: noise_bed(p, swell_rate=0.5, swell_depth=0.8, flutter=0),
                      dur=12.0)
    db = _env_db(x[:, 0], win_s=0.1)
    spec = np.abs(np.fft.rfft(db - db.mean()))
    freqs = np.fft.rfftfreq(len(db), 0.1)
    assert freqs[np.argmax(spec[1:]) + 1] == pytest.approx(0.5, abs=0.1)


@needs_pd
def test_noise_bed_flutter_adds_irregular_motion():
    _, still, _ = _render(lambda p: noise_bed(p, swell_depth=0, flutter=0), dur=4.0)
    _, wet, _ = _render(lambda p: noise_bed(p, swell_depth=0, flutter=0.5), dur=4.0)
    assert np.std(_env_db(wet[:, 0])) > 2 * np.std(_env_db(still[:, 0]))


@needs_pd
def test_noise_bed_is_seeded():
    """Same seed, same bed -- even with another [noise~] built before it,
    which shifts the default seeds. Another seed, another bed."""
    def build(seed, extra):
        def b(p):
            if extra:
                p.obj("noise~")
            return noise_bed(p, seed=seed)
        return b
    _, a, _ = _render(build(5, False), dur=1.0)
    _, b, _ = _render(build(5, True), dur=1.0)
    _, c, _ = _render(build(6, False), dur=1.0)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


# --------------------------------------------------------------------------- #
# loop_lfo
# --------------------------------------------------------------------------- #

@needs_pd
@pytest.mark.parametrize("shape", ["sine", "triangle", "ramp"])
def test_loop_lfo_spans_its_range_and_starts_at_lo(shape):
    _, x, _ = _render(lambda p: loop_lfo(p, loop_s=1.0, shape=shape, lo=-0.5, hi=0.5), dur=2.0)
    s = x[:, 0]
    assert s.min() == pytest.approx(-0.5, abs=0.01) and s.max() == pytest.approx(0.5, abs=0.01)
    assert s[0] == pytest.approx(-0.5, abs=0.01)
    if shape != "ramp":
        assert s[SR // 2] == pytest.approx(0.5, abs=0.01)     # the peak at half a cycle


@needs_pd
def test_loop_lfo_makes_what_it_modulates_periodic():
    """A 3-cycle swell on a tone over a 2 s loop: the two passes match."""
    def build(p):
        lfo = loop_lfo(p, loop_s=2.0, cycles=3)
        tone = _sine(p, 220.0)
        vca = p.obj("*~")
        p.link(tone, 0, vca, 0)
        p.link(lfo, 0, vca, 1)
        return vca
    _, _, buf = _render(build, dur=4.0)
    assert loop_similarity(buf, 2.0).similarity > 0.99


def _capture32(run_pd, tmp_path, build, dur):
    """Render ``build(patch)``'s output in a bare Pd batch run and write it
    as 32-bit float (pdverify captures 16-bit, too coarse for drift)."""
    from pdverify.wavio import read_wav
    p = Patch()
    out = build(p)
    p.obj(f"table cap {int(dur * 48000) + 64}")
    tw = p.obj("tabwrite~ cap")
    p.link(out, 0, tw, 0)
    lb = p.loadbang()
    dsp = p.msg("; pd dsp 1")
    p.link(lb, 0, dsp, 0)
    go = p.msg("bang")
    p.link(lb, 0, go, 0)
    p.link(go, 0, tw, 0)
    d = p.obj(f"del {dur * 1000:g}")
    p.link(lb, 0, d, 0)
    wr = p.msg("write -wave -bytes 4 cap.wav cap")
    sf = p.obj("soundfiler")
    q = p.msg("; pd quit")
    p.link(d, 0, wr, 0)
    p.link(wr, 0, sf, 0)
    p.link(sf, 0, q, 0)
    path = tmp_path / "cap.pd"
    path.write_text(p.render(), encoding="utf-8")
    run_pd(path, cwd=tmp_path, timeout=120)
    buf = read_wav(tmp_path / "cap.wav")
    return buf.samples[:, 0].astype(np.float64), buf.sr


def _phase_error(x, sr, loop_s, loops):
    """Largest wrapped difference between loop 0 and loop ``loops``."""
    L = int(round(loop_s * sr))
    i0 = 1000
    d = x[i0 + loops * L:i0 + loops * L + 20000] - x[i0:i0 + 20000]
    return float(np.abs((d + 0.5) % 1.0 - 0.5).max())


def test_loop_lfo_does_not_drift(run_pd, tmp_path):
    """60 s of a 3-cycle ramp over a 7 s loop, captured as 32-bit float: after
    eight loops the ramp is where it was to ~1e-6 of a cycle (measured
    4e-7). Control: a free-running [phasor~] at the same 3/7 Hz is off by
    ~1e-4 (measured 1.0e-4 after six loops)."""
    x, sr = _capture32(run_pd, tmp_path, lambda p: loop_lfo(p, loop_s=7.0, cycles=3, shape="ramp"),
                       60.0)
    locked = _phase_error(x, sr, 7.0, 8)
    y, _ = _capture32(run_pd, tmp_path, lambda p: p.obj(f"phasor~ {3 / 7:.9g}"), 60.0)
    free = _phase_error(y, sr, 7.0, 8)
    assert locked < 2e-6, locked
    assert free > 3e-5, free
    assert free > 30 * locked


# --------------------------------------------------------------------------- #
# scale_walk
# --------------------------------------------------------------------------- #

def _walk(density, seed=3, *, before=0, dur=3.0, sends=(), **kw):
    """Run a scale_walk off a 20 ms metro; return (midi notes, degrees)."""
    from pdverify.control import Control
    p = Patch()
    for _ in range(before):
        p.obj("random 5")                     # shifts every default seed after it
    m = p.obj("metro 20")
    p.init(m, 1)
    w = scale_walk(p, m, density=density, seed=seed, **kw)
    pm, pdg = p.obj("print walk"), p.obj("print deg")
    p.link(w.midi, 0, pm, 0)
    p.link(w.degree[0], w.degree[1], pdg, 0)
    dac = p.obj("dac~")
    p.link(p.obj("sig~ 0"), 0, dac, 0)
    assert not p.unvalidated(), p.unvalidated()
    spec = RenderSpec(duration=dur, controls=tuple(Control(t, n, (v,)) for t, n, v in sends))
    con = render(p.render(), spec).pd_console
    assert "error" not in con.lower(), con
    return ([float(v) for v in re.findall(r"walk: ([-\d.]+)", con)],
            [int(float(v)) for v in re.findall(r"deg: ([-\d.]+)", con)])


@needs_pd
def test_scale_walk_density_sets_the_note_count():
    """~170 ticks: density 0.2 plays ~a quarter as many as 0.8 (measured 40
    vs 139); density 0 plays nothing."""
    sparse, _ = _walk(0.2)
    dense, _ = _walk(0.8)
    assert 0.15 < len(sparse) / len(dense) < 0.4, (len(sparse), len(dense))
    assert len(dense) > 100
    assert _walk(0.0)[0] == []


@needs_pd
def test_scale_walk_is_reproducible_and_seeded():
    a, _ = _walk(0.6, seed=3)
    b, _ = _walk(0.6, seed=3, before=3)       # seeded on the first tick: order-proof
    c, _ = _walk(0.6, seed=4)
    assert a == b
    assert a != c


@needs_pd
def test_scale_walk_density_thins_one_melody():
    """The step generator only draws when a note plays, so the sparse walk
    is the dense walk's opening, note for note."""
    sparse, _ = _walk(0.2)
    dense, _ = _walk(0.8)
    assert sparse == dense[:len(sparse)]


@needs_pd
def test_scale_walk_stays_in_range_and_on_the_scale():
    notes, degrees = _walk(1.0, dur=4.0, lo=-5, hi=9)
    assert min(degrees) >= -5 and max(degrees) <= 9
    assert set(np.diff([0] + degrees)) <= {-2, -1, 0, 1, 2}        # one step at a time
    pent = {0, 2, 4, 7, 9}
    assert all((n - 60) % 12 in pent for n in notes)
    assert min(notes) >= 48 and max(notes) <= 81
    assert len(set(degrees)) >= 8                                  # it really walks


@needs_pd
def test_scale_walk_density_receive_changes_it_live():
    quiet, _ = _walk(0.9, density_recv="walkdensity", sends=((0.05, "walkdensity", 0.0),))
    assert len(quiet) < 5, len(quiet)


def test_weight_table():
    assert _weight_table({-1: 1, 1: 2}) == [-1, 1, 1]
    assert len(_weight_table(((-1, 0.25), (1, 0.75)))) == 1000
    with pytest.raises(ValueError):
        _weight_table({1: 0})


# --------------------------------------------------------------------------- #
# composition: a small ambient piece from the tier
# --------------------------------------------------------------------------- #

def _ambient_piece(p):
    """scale_walk -> pluck, over a drone and a noise bed, into the reverb."""
    clock = p.obj("metro 375")
    p.init(clock, 1)
    walk = scale_walk(p, clock, scale=(0, 2, 4, 7, 9), tonic=57, density=0.45, seed=11)
    hz = p.obj("mtof")
    p.link(walk.midi, 0, hz, 0)
    notes = pluck(p, None, hz, decay=2.5, brightness=0.4, gain=0.25)
    bed = drone(p, 110.0, gain=0.12, loop_s=12.0)
    air = noise_bed(p, lo=600, hi=4000, swell_rate=0.08, seed=7, gain=0.025, loop_s=12.0)
    bus = p.obj("+~")
    p.link(notes, 0, bus, 0)
    p.link(bed, 0, bus, 1)
    bus2 = p.obj("+~")
    p.link(bus, 0, bus2, 0)
    p.link(air, 0, bus2, 1)
    return reverb(p, bus2, size=0.7, damping=0.45, mix=0.35)


@needs_pd
def test_ambient_piece_renders_and_sounds_like_its_claim():
    rep, x, _ = _render(_ambient_piece, dur=12.0)
    print(rep.summary())
    print(rep.pretty())
    assert not rep.is_silent and not rep.is_clipped and not rep.has_nan_inf
    assert -30 < rep.rms_dbfs < -10
    assert rep.channels == 2 and abs(np.corrcoef(x[:, 0], x[:, 1])[0, 1]) < 0.95   # wide
    assert rep.f0_hz is not None
    assert rep.onset_count >= 3 or rep.motion != "steady"       # the plucks move it
    assert rep.bands["bass"] > 0.1                              # the drone's body
    assert rep.bands["high_mid"] + rep.bands["presence"] > 0.005  # the air
    # every part present and seeded: a second render is identical
    _, y, _ = _render(_ambient_piece, dur=12.0)
    assert np.array_equal(x, y)
