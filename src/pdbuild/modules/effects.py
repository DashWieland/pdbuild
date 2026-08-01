"""Effects -- signal in, processed signal out.

The delay-based effects allocate a named ``[delwrite~]`` buffer. They take a
patch-unique name from ``Patch.uid`` so two of them never collide, and
``extract`` namespaces the name to ``$0-`` if the effect later moves into a
reusable abstraction.
"""

from __future__ import annotations

from ._util import wire


def saturate(patch, sig, *, drive: float = 2.0, level: float = 0.7):
    """Soft-clip through ``tanh``. Adds harmonics and warmth; higher ``drive``
    is dirtier. ``level`` trims the output back down after the gain-up."""
    dr = patch.obj(f"*~ {drive}")
    sat = patch.obj("expr~ tanh($v1)")
    out = patch.obj(f"*~ {level}")
    wire(patch, sig, dr, 0)
    patch.link(dr, 0, sat, 0)
    patch.link(sat, 0, out, 0)
    return out


def delay(patch, sig, *, time_ms: float = 250.0, feedback: float = 0.4,
          mix: float = 0.5, name: str | None = None):
    """Feedback delay / echo. ``feedback`` (0..~0.95) sets how many repeats,
    ``mix`` blends dry (0) to wet (1). Returns the mixed output."""
    buf = name or patch.uid("dl")
    size = max(time_ms * 2.0, 1000.0)
    dw = patch.obj(f"delwrite~ {buf} {size:g}")
    dr = patch.obj(f"delread~ {buf} {time_ms:g}")
    fb = patch.obj(f"*~ {feedback}")
    into = patch.obj("+~")                 # input + feedback -> the delay line
    wire(patch, sig, into, 0)
    patch.link(dr, 0, fb, 0)
    patch.link(fb, 0, into, 1)
    patch.link(into, 0, dw, 0)
    # dry/wet blend
    dry = patch.obj(f"*~ {1.0 - mix}")
    wet = patch.obj(f"*~ {mix}")
    out = patch.obj("+~")
    wire(patch, sig, dry, 0)
    patch.link(dr, 0, wet, 0)
    patch.link(dry, 0, out, 0)
    patch.link(wet, 0, out, 1)
    return out


def chorus(patch, sig, *, rate: float = 0.3, depth_ms: float = 6.0,
           base_ms: float = 14.0, mix: float = 0.5, name: str | None = None):
    """Chorus: mix the signal with a copy read through a slowly LFO-modulated
    delay. Thickens and widens a plain tone. Returns the mixed output."""
    buf = name or patch.uid("cho")
    dw = patch.obj(f"delwrite~ {buf} {base_ms + depth_ms + 10:g}")
    wire(patch, sig, dw, 0)
    lfo = patch.obj(f"osc~ {rate}")
    swing = patch.obj(f"*~ {depth_ms}")
    offset = patch.obj(f"+~ {base_ms}")
    vd = patch.obj(f"vd~ {buf}")
    patch.link(lfo, 0, swing, 0)
    patch.link(swing, 0, offset, 0)
    patch.link(offset, 0, vd, 0)
    dry = patch.obj(f"*~ {1.0 - mix}")
    wet = patch.obj(f"*~ {mix}")
    out = patch.obj("+~")
    wire(patch, sig, dry, 0)
    patch.link(vd, 0, wet, 0)
    patch.link(dry, 0, out, 0)
    patch.link(wet, 0, out, 1)
    return out
