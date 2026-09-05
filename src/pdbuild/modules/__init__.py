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

Three tiers, each render-verified:

* **signal processors** -- general-purpose DSP blocks (a lowpass darkens,
  saturation adds harmonics, a delay leaves an audible tail);
* **synth voices** -- a trigger and a pitch in, audio out;
* **control** (sequencing) -- the machinery that makes the triggers: a swung
  clock, per-pulse step tables with intensity tiers, a mutating melody loop,
  Euclidean rows and a scale-degree lookup. These read control receives and
  broadcast named sends (``pulse``, ``bar``, ``note_in``) that voices listen
  to -- the broadcast-clock idiom.

Input / control *surfaces* are ``pdbuild.surface``.
"""

from __future__ import annotations

from .control import glide, smooth
from .drums import hat, kick, snare
from .effects import chorus, delay, saturate
from .envelopes import ad_envelope, asr_envelope
from .filters import bandpass, highpass, lowpass, resonant_lowpass
from .scales import euclid, euclid_rows, scale_degree, scale_tables
from .sequencing import (
    HOLD, REST, gated_value, melody_loop, phrase_steps, step_priority, step_tables, swing_clock,
)
from .voices import acid_voice, fm_voice, oscillator, subtractive_voice

__all__ = [
    # --- signal processors ---
    # envelopes
    "ad_envelope", "asr_envelope",
    # control
    "glide", "smooth",
    # filters
    "lowpass", "highpass", "bandpass", "resonant_lowpass",
    # effects
    "saturate", "delay", "chorus",
    # --- synth voices ---
    "oscillator", "subtractive_voice", "acid_voice", "fm_voice",
    # drum voices
    "kick", "snare", "hat",
    # --- control tier (sequencing) ---
    "swing_clock", "step_tables", "gated_value", "melody_loop",
    "phrase_steps", "step_priority", "REST", "HOLD",
    "euclid", "euclid_rows", "scale_tables", "scale_degree",
]
