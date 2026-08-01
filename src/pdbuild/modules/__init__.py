"""pdbuild.modules -- a verified library of reusable Pure Data blocks.

Each module is a plain function ``module(patch, input_port, **params) -> output``
that adds its objects to ``patch``, wires the input, and returns the output node
(outlet 0 is the result). They compose into voices, and a composed chain can be
pulled into a reusable abstraction with ``pdbuild.extract``.

    from pdbuild import Patch
    from pdbuild.modules import resonant_lowpass, saturate, delay

    p = Patch()
    osc = p.obj("phasor~ 110")
    ctr = p.obj("-~ 0.5")                       # saw, centred
    p.link(osc, 0, ctr, 0)
    tone = resonant_lowpass(p, ctr, cutoff=900, resonance=3.2)
    dirty = saturate(p, tone, drive=3)
    wide = delay(p, dirty, time_ms=180, feedback=0.35)
    dac = p.obj("dac~")
    p.link(wide, 0, dac, 0); p.link(wide, 0, dac, 1)

This is the **signal-processor tier**: general-purpose DSP blocks, each with a
render-verified test that it does what it claims (a lowpass darkens, saturation
adds harmonics, a delay leaves an audible tail). Synth voices and input/control
surfaces are separate tiers, added later.
"""

from __future__ import annotations

from .control import glide, smooth
from .effects import chorus, delay, saturate
from .envelopes import ad_envelope, asr_envelope
from .filters import bandpass, highpass, lowpass, resonant_lowpass

__all__ = [
    # envelopes
    "ad_envelope", "asr_envelope",
    # control
    "glide", "smooth",
    # filters
    "lowpass", "highpass", "bandpass", "resonant_lowpass",
    # effects
    "saturate", "delay", "chorus",
]
