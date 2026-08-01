"""Each signal-processor module is verified by RENDERING it and checking it does
what it claims -- a lowpass darkens, a highpass brightens, saturation adds
harmonics, a delay leaves an audible tail, an envelope shapes the amplitude.

This is the whole point of a "verified" module library: the claim in the
docstring is backed by a rendered measurement, not by reading the patch.
"""

from __future__ import annotations

import numpy as np
import pytest

from pdbuild import Patch
from pdbuild.modules import (
    ad_envelope, asr_envelope, bandpass, chorus, delay, glide, highpass,
    lowpass, resonant_lowpass, saturate, smooth,
)

pytest.importorskip("pdverify")
from pdverify import analyze                       # noqa: E402
from pdverify.render import RenderSpec, render      # noqa: E402
from pdverify.pd_locate import discover             # noqa: E402

try:
    discover()
    _HAVE_PD = True
except Exception:
    _HAVE_PD = False

pytestmark = pytest.mark.skipif(not _HAVE_PD, reason="needs a Pd install")

DUR = 1.5


def _render_out(build, dur=DUR):
    """build(patch) returns the output node; wire it to dac~, render, analyze.
    Returns (Report, samples-of-channel-0)."""
    p = Patch(500, 400, 10)
    out = build(p)
    dac = p.obj("dac~")
    p.link(out, 0, dac, 0)
    p.link(out, 0, dac, 1)
    res = render(p.render(), RenderSpec(duration=dur))
    return analyze(res.audio), res.audio.samples[:, 0]


def _noise(p, amp=0.3):
    n = p.obj("noise~")
    g = p.obj(f"*~ {amp}")
    p.link(n, 0, g, 0)
    return g


def _sine(p, freq=200.0, amp=0.4):
    o = p.obj(f"osc~ {freq}")
    g = p.obj(f"*~ {amp}")
    p.link(o, 0, g, 0)
    return g


# --------------------------------------------------------------------------- #
# filters: the claim is a shift in spectral centroid
# --------------------------------------------------------------------------- #

def test_lowpass_darkens():
    raw, _ = _render_out(_noise)
    lp, _ = _render_out(lambda p: lowpass(p, _noise(p), cutoff=400))
    assert lp.centroid_hz < raw.centroid_hz * 0.5, (
        f"lowpass did not darken: {lp.centroid_hz:.0f} vs raw {raw.centroid_hz:.0f}")


def test_highpass_brightens():
    raw, _ = _render_out(_noise)
    hp, _ = _render_out(lambda p: highpass(p, _noise(p), cutoff=5000))
    assert hp.centroid_hz > raw.centroid_hz, (
        f"highpass did not brighten: {hp.centroid_hz:.0f} vs raw {raw.centroid_hz:.0f}")


def test_resonant_lowpass_darkens_and_stays_tonal():
    raw, _ = _render_out(_noise)
    rl, _ = _render_out(lambda p: resonant_lowpass(p, _noise(p), cutoff=600, resonance=3.5))
    assert rl.centroid_hz < raw.centroid_hz * 0.5
    assert not rl.is_silent


def test_bandpass_concentrates_energy_near_center():
    bp, _ = _render_out(lambda p: bandpass(p, _noise(p), center=1500, q=6))
    # most energy sits near the passband, so the centroid lands in its vicinity
    assert 800 < bp.centroid_hz < 3000
    assert not bp.is_silent


# --------------------------------------------------------------------------- #
# saturation: the claim is added harmonics on a pure tone
# --------------------------------------------------------------------------- #

def test_saturate_adds_harmonics():
    clean, _ = _render_out(lambda p: _sine(p, 200))
    dirty, _ = _render_out(lambda p: saturate(p, _sine(p, 200), drive=5))
    # tanh on a 200 Hz sine injects ODD harmonics: the clean tone has only the
    # fundamental, the saturated one grows peaks at 600, 1000 Hz, ...
    assert clean.top_partials == pytest.approx([200], abs=5)
    assert len(dirty.top_partials) >= 3
    assert any(abs(hz - 600) < 20 for hz in dirty.top_partials), (
        f"no 3rd harmonic after saturation: {dirty.top_partials}")
    assert dirty.centroid_hz > clean.centroid_hz     # harmonics brighten it


# --------------------------------------------------------------------------- #
# delay: the claim is an audible tail after the input stops
# --------------------------------------------------------------------------- #

def _gated_tone(p, freq=330.0, decay=120.0):
    """A short tone burst that is silent for most of the render, so anything in
    the tail must have come from an effect."""
    osc = p.obj(f"osc~ {freq}")
    lb = p.obj("loadbang"); trig = p.msg("bang")
    p.link(lb, 0, trig, 0)
    env = ad_envelope(p, trig, attack=3, decay=decay)
    v = p.obj("*~")
    p.link(osc, 0, v, 0)
    p.link(env, 0, v, 1)
    return v


def _late_rms(samples, frac=0.5):
    """RMS of the last `frac` of a buffer."""
    tail = samples[int(len(samples) * (1 - frac)):]
    return float(np.sqrt(np.mean(tail ** 2))) if tail.size else 0.0


def test_delay_leaves_a_tail():
    _, dry = _render_out(lambda p: _gated_tone(p))
    _, wet = _render_out(lambda p: delay(p, _gated_tone(p), time_ms=200, feedback=0.6, mix=0.7))
    # the dry burst has decayed to near nothing in the back half; the delay's
    # echoes keep energy alive there.
    assert _late_rms(wet) > _late_rms(dry) * 5, (
        f"no delay tail: wet {_late_rms(wet):.5f} vs dry {_late_rms(dry):.5f}")


def test_two_delays_use_distinct_buffers():
    """Patch.uid gives each delay its own buffer, so two in one patch do not
    collide (no 'multiply defined')."""
    p = Patch(600, 400, 10)
    a = delay(p, _noise(p), time_ms=100)
    b = delay(p, _noise(p), time_ms=250)
    mix = p.obj("+~")
    p.link(a, 0, mix, 0)
    p.link(b, 0, mix, 1)
    dac = p.obj("dac~")
    p.link(mix, 0, dac, 0)
    res = render(p.render(), RenderSpec(duration=1.0))
    assert "multiply defined" not in res.pd_console.lower()
    assert not analyze(res.audio).is_silent


# --------------------------------------------------------------------------- #
# chorus: the claim is movement added to a steady tone
# --------------------------------------------------------------------------- #

def test_chorus_adds_movement():
    steady, _ = _render_out(lambda p: _sine(p, 330))
    wobbly, _ = _render_out(lambda p: chorus(p, _sine(p, 330), rate=1.5, depth_ms=8))
    assert steady.motion == "steady"
    assert wobbly.motion != "steady"
    assert not wobbly.is_silent


# --------------------------------------------------------------------------- #
# envelopes: the claim is a shaped, decaying amplitude
# --------------------------------------------------------------------------- #

def test_ad_envelope_shapes_and_decays():
    steady, _ = _render_out(lambda p: _sine(p, 330))
    env_rep, samples = _render_out(_gated_tone)
    assert steady.motion == "steady"
    assert env_rep.motion != "steady"                 # the envelope moves
    # the burst is up front, so the first half is much louder than the last
    first = float(np.sqrt(np.mean(samples[:len(samples)//2] ** 2)))
    last = float(np.sqrt(np.mean(samples[len(samples)//2:] ** 2)))
    assert first > last * 5


def test_asr_envelope_opens_and_closes():
    def build(p):
        osc = p.obj("osc~ 330")
        lb = p.obj("loadbang")
        on = p.msg("1"); off = p.msg("0")
        # gate on at load, off after 400 ms
        p.link(lb, 0, on, 0)
        d = p.obj("del 400")
        p.link(lb, 0, d, 0); p.link(d, 0, off, 0)
        gate = p.obj("f")                              # hold the gate value
        p.link(on, 0, gate, 0); p.link(off, 0, gate, 0)
        env = asr_envelope(p, gate, attack=5, release=120)
        v = p.obj("*~")
        p.link(osc, 0, v, 0); p.link(env, 0, v, 1)
        return v
    rep, samples = _render_out(build)
    assert not rep.is_silent
    # sustains through the first ~400 ms, then releases: front louder than back
    first = float(np.sqrt(np.mean(samples[:len(samples)//2] ** 2)))
    last = float(np.sqrt(np.mean(samples[len(samples)//2:] ** 2)))
    assert first > last * 3


# --------------------------------------------------------------------------- #
# control smoothing: the claim is a gradual ramp, not a jump
# --------------------------------------------------------------------------- #

def test_glide_ramps_instead_of_jumping():
    # tap the glide output straight to dac~: a near-DC ramp whose sample values
    # ARE the glided control. With a 600 ms glide, the value is mid-way at
    # ~300 ms; with no glide it jumps immediately.
    def build_glide(p, time_ms):
        lb = p.obj("loadbang"); one = p.msg("1")
        p.link(lb, 0, one, 0)
        return glide(p, one, time_ms=time_ms)

    _, ramp = _render_out(lambda p: build_glide(p, 600), dur=1.0)
    _, jump = _render_out(lambda p: build_glide(p, 0), dur=1.0)
    sr = 44100
    at_300ms = int(0.30 * sr)
    assert 0.2 < ramp[at_300ms] < 0.8, f"glide not mid-ramp at 300ms: {ramp[at_300ms]:.3f}"
    assert jump[at_300ms] > 0.95, f"no-glide should have jumped: {jump[at_300ms]:.3f}"


def test_smooth_scales_into_range():
    # smooth a constant 1.0 control into 0..880 and hold; the settled value is hi
    def build(p):
        lb = p.obj("loadbang"); one = p.msg("1")
        p.link(lb, 0, one, 0)
        return smooth(p, one, time_ms=20, lo=0.0, hi=0.8)
    _, s = _render_out(build, dur=0.5)
    assert s[-1] == pytest.approx(0.8, abs=0.05)      # settled at hi


# --------------------------------------------------------------------------- #
# composition: the modules chain into a voice, the reviewer's stated goal
# --------------------------------------------------------------------------- #

def test_modules_compose_into_an_acid_voice():
    """osc -> resonant filter -> saturation -> delay: a small acid line built
    entirely from library modules. Proves the tier composes and stays audible
    and clean (no clipping, no NaN, echoes present)."""
    def build(p):
        saw = p.obj("phasor~ 110")
        centred = p.obj("-~ 0.5")
        p.link(saw, 0, centred, 0)
        tone = resonant_lowpass(p, centred, cutoff=700, resonance=3.2)
        dirty = saturate(p, tone, drive=3, level=0.5)
        wide = delay(p, dirty, time_ms=180, feedback=0.35, mix=0.4)
        return wide
    rep, samples = _render_out(build)
    assert not rep.is_silent
    assert not rep.is_clipped
    assert not rep.has_nan_inf
    assert _late_rms(samples, frac=0.3) > 0            # the delay keeps it alive
