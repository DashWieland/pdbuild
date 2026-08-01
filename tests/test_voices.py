"""Synth voices, verified by rendering: a pitched voice plays the note it was
asked for, and each voice's timbre matches its claim (an acid voice is rich and
resonant, a kick is low and punchy, a hat is bright noise).

Voices are driven by a [metro] so notes recur across the whole render -- both
because that is how a voice is really played (from a sequencer) and because a
single one-shot at the front would fall outside the analysis window.
"""

from __future__ import annotations

import numpy as np
import pytest

from pdbuild import Patch
from pdbuild.modules import (
    acid_voice, chorus, delay, fm_voice, hat, kick, oscillator, snare,
    subtractive_voice,
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


def _play(build, *, period=200, dur=1.5):
    """Drive `build(patch, trigger)` from a metro and render.

    Returns (Report, channel-0 samples). `build` returns the voice's audio out.
    """
    p = Patch(500, 400, 10)
    lb = p.obj("loadbang")
    on = p.msg("1")
    m = p.obj(f"metro {period}")
    p.link(lb, 0, on, 0)
    p.link(on, 0, m, 0)
    out = build(p, m)
    dac = p.obj("dac~")
    p.link(out, 0, dac, 0)
    p.link(out, 0, dac, 1)
    res = render(p.render(), RenderSpec(duration=dur))
    return analyze(res.audio), res.audio.samples[:, 0]


def _has_partial(report, hz, tol=15):
    return any(abs(p - hz) < tol for p in report.top_partials)


# --------------------------------------------------------------------------- #
# pitched voices: the claim is "plays the requested note"
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("freq,note", [(220, "A3"), (110, "A2"), (330, "E4")])
def test_subtractive_voice_plays_the_pitch(freq, note):
    r, _ = _play(lambda p, t: subtractive_voice(p, t, freq, cutoff=1800, resonance=2))
    assert not r.is_silent
    assert r.dominant_hz == pytest.approx(freq, abs=max(3, freq * 0.02))
    assert r.note == note


def test_subtractive_cutoff_darkens_the_voice():
    dark, _ = _play(lambda p, t: subtractive_voice(p, t, 220, cutoff=500, resonance=1))
    bright, _ = _play(lambda p, t: subtractive_voice(p, t, 220, cutoff=4000, resonance=1))
    assert dark.centroid_hz < bright.centroid_hz
    assert dark.dominant_hz == pytest.approx(220, abs=6)   # same note, different colour


def test_acid_voice_is_pitched_and_harmonically_rich():
    r, _ = _play(lambda p, t: acid_voice(p, t, 110, resonance=3.5, decay=180))
    assert not r.is_silent and not r.is_clipped
    # the fundamental is present, plus harmonics from the saw + resonance + drive
    assert _has_partial(r, 110, tol=6)
    assert len(r.top_partials) >= 3
    assert r.motion != "steady"                            # the filter env sweeps


def test_acid_resonance_adds_emphasis():
    flat_res, _ = _play(lambda p, t: acid_voice(p, t, 110, resonance=0.5))
    peaky, _ = _play(lambda p, t: acid_voice(p, t, 110, resonance=4.2))
    # the resonant peak injects energy well above the fundamental, so a peaky
    # filter is markedly brighter than a flat one at the same note.
    assert not peaky.is_silent and not flat_res.is_silent
    assert peaky.centroid_hz > flat_res.centroid_hz * 2
    assert peaky.dominant_hz == pytest.approx(110, abs=6)


def test_fm_voice_grows_sidebands_with_index():
    clean, _ = _play(lambda p, t: fm_voice(p, t, 330, ratio=2, index=0.2))
    bright, _ = _play(lambda p, t: fm_voice(p, t, 330, ratio=2, index=8))
    # high index throws real sidebands far from the fundamental, dragging the
    # centroid up sharply and putting energy well above the low-index tone.
    assert bright.centroid_hz > clean.centroid_hz * 2.5
    assert max(bright.top_partials) > 1500          # genuine sidebands


def test_voice_pitch_can_come_from_a_control_port():
    """A sequencer drives pitch: feed the voice a control value, not a constant,
    and it plays that note."""
    def build(p, trig):
        val = p.msg("440")
        lb = p.obj("loadbang")
        p.link(lb, 0, val, 0)                              # emit 440 at load
        return subtractive_voice(p, trig, val, cutoff=2500, resonance=2)
    r, _ = _play(build)
    assert r.dominant_hz == pytest.approx(440, abs=8)
    assert r.note == "A4"


# --------------------------------------------------------------------------- #
# drum voices: the claim is a shaped percussion timbre
# --------------------------------------------------------------------------- #

def test_kick_is_low_and_punchy():
    r, samples = _play(lambda p, t: kick(p, t), period=300)
    assert not r.is_silent
    assert r.dominant_hz is not None and r.dominant_hz < 120     # sub/low range
    assert r.centroid_hz < 300                                   # energy is low
    # percussive: each hit decays, so the signal is dynamic, not sustained
    assert r.crest_factor > 2.0


def test_snare_sits_in_its_band():
    r, _ = _play(lambda p, t: snare(p, t, tone=1800, q=2), period=300)
    assert not r.is_silent
    assert 900 < r.centroid_hz < 3500                           # around the band
    assert _has_partial(r, 1800, tol=600)


def test_hat_is_bright_noise():
    r, _ = _play(lambda p, t: hat(p, t, cutoff=7000), period=250)
    assert not r.is_silent
    assert r.centroid_hz > 6000                                 # very bright
    assert r.flatness > 0.1                                     # noisy, not tonal


def test_hat_is_brighter_than_kick():
    khat, _ = _play(lambda p, t: hat(p, t), period=250)
    kk, _ = _play(lambda p, t: kick(p, t), period=250)
    assert khat.centroid_hz > kk.centroid_hz * 10


# --------------------------------------------------------------------------- #
# composition: a voice feeds a processor; drums layer into a mix
# --------------------------------------------------------------------------- #

def test_voice_composes_with_an_effect():
    """A voice is just a port, so an effect chains onto it."""
    dry, _ = _play(lambda p, t: subtractive_voice(p, t, 220))
    wet, _ = _play(lambda p, t: delay(p, subtractive_voice(p, t, 220),
                                      time_ms=150, feedback=0.5, mix=0.5))
    # both play A3; the delayed one is not silent and still tracks the pitch
    assert wet.dominant_hz == pytest.approx(220, abs=8)
    assert not wet.is_silent


def test_drums_layer_into_one_mix():
    def build(p, trig):
        k = kick(p, trig, gain=0.5)
        h = hat(p, trig, gain=0.5)
        mix = p.obj("+~")
        p.link(k, 0, mix, 0)
        p.link(h, 0, mix, 1)
        return mix
    r, _ = _play(build, period=300)
    assert not r.is_silent and not r.has_nan_inf and not r.is_clipped
    # the kick dominates the energy (its low body), but the hat's brilliance is
    # unmistakably there too -- both layers survive the mix.
    assert r.bands["sub"] + r.bands["bass"] > 0.3       # kick body
    assert r.bands["brilliance"] > 0.005                # hat top
    assert any(p < 150 for p in r.top_partials)         # the kick fundamental
