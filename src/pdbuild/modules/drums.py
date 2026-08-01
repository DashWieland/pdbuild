"""Drum voices -- a trigger in, a one-shot percussion sound out.

Like the pitched voices these generate audio from a control trigger, but they
have no sustained pitch: a bang fires a short, shaped burst. Built from the same
processor tier (envelopes, filters, saturation).
"""

from __future__ import annotations

from ._util import wire
from .envelopes import ad_envelope
from .filters import bandpass, highpass


def kick(patch, trigger, *, tune: float = 50.0, punch: float = 110.0,
         pitch_ms: float = 50.0, decay: float = 180.0, drive: float = 1.6,
         gain: float = 0.8):
    """A kick: a sine whose pitch drops from ``punch`` to ``tune`` fast, with a
    percussive amp decay and a little drive for body. Returns the audio out."""
    # pitch envelope: snap to punch, then glide down to tune over pitch_ms
    pmsg = patch.msg(f"{punch} 1, {tune} {pitch_ms} 1")
    penv = patch.obj("vline~")
    wire(patch, trigger, pmsg, 0)
    patch.link(pmsg, 0, penv, 0)
    osc = patch.obj("osc~")
    patch.link(penv, 0, osc, 0)

    aenv = ad_envelope(patch, trigger, attack=2.0, decay=decay)
    vca = patch.obj("*~")
    patch.link(osc, 0, vca, 0)
    patch.link(aenv, 0, vca, 1)

    dr = patch.obj(f"*~ {drive}")
    patch.link(vca, 0, dr, 0)
    sat = patch.obj("expr~ tanh($v1)")
    patch.link(dr, 0, sat, 0)
    out = patch.obj(f"*~ {gain}")
    patch.link(sat, 0, out, 0)
    return out


def snare(patch, trigger, *, tone: float = 1800.0, q: float = 2.0,
          decay: float = 150.0, gain: float = 0.5):
    """A snare: a band-passed noise burst with a short decay. Returns the audio
    out. (A tonal 'body' pair could be layered on later.)"""
    noise = patch.obj("noise~")
    band = bandpass(patch, noise, center=tone, q=q)
    aenv = ad_envelope(patch, trigger, attack=1.0, decay=decay)
    vca = patch.obj("*~")
    patch.link(band, 0, vca, 0)
    patch.link(aenv, 0, vca, 1)
    out = patch.obj(f"*~ {gain}")
    patch.link(vca, 0, out, 0)
    return out


def hat(patch, trigger, *, cutoff: float = 7000.0, decay: float = 45.0,
        gain: float = 0.3):
    """A hi-hat: bright high-passed noise with a very short decay. ``decay``
    around 40 ms reads as closed, longer as open. Returns the audio out."""
    noise = patch.obj("noise~")
    bright = highpass(patch, noise, cutoff=cutoff)
    aenv = ad_envelope(patch, trigger, attack=0.5, decay=decay)
    vca = patch.obj("*~")
    patch.link(bright, 0, vca, 0)
    patch.link(aenv, 0, vca, 1)
    out = patch.obj(f"*~ {gain}")
    patch.link(vca, 0, out, 0)
    return out
