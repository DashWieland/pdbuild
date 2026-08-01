"""Filters -- signal in, filtered signal out."""

from __future__ import annotations

from ._util import wire


def lowpass(patch, sig, *, cutoff: float = 1000.0):
    """One-pole lowpass ``[lop~]``. Darkens: rolls off highs above ``cutoff``."""
    f = patch.obj(f"lop~ {cutoff}")
    wire(patch, sig, f, 0)
    return f


def highpass(patch, sig, *, cutoff: float = 200.0):
    """One-pole highpass ``[hip~]``. Brightens: rolls off lows below ``cutoff``."""
    f = patch.obj(f"hip~ {cutoff}")
    wire(patch, sig, f, 0)
    return f


def bandpass(patch, sig, *, center: float = 1000.0, q: float = 4.0):
    """Resonant bandpass ``[bp~]`` around ``center`` with quality ``q``."""
    f = patch.obj(f"bp~ {center} {q}")
    wire(patch, sig, f, 0)
    return f


def resonant_lowpass(patch, sig, *, cutoff: float = 1000.0, resonance: float = 2.0):
    """Moog-style resonant ladder ``[bob~]``. The acid-bass staple: a lowpass
    with an emphasised peak at ``cutoff`` (``resonance`` ~0..4.5; self-oscillates
    near the top). ``cutoff`` and ``resonance`` are given as constant signals so
    they can later be swapped for modulation.
    """
    filt = patch.obj("bob~")
    cut = patch.obj(f"sig~ {cutoff}")
    res = patch.obj(f"sig~ {resonance}")
    wire(patch, sig, filt, 0)
    patch.link(cut, 0, filt, 1)
    patch.link(res, 0, filt, 2)
    return filt
