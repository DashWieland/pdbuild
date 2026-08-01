"""Control-signal shaping -- a control value in, a smoothed signal out."""

from __future__ import annotations

from ._util import wire


def glide(patch, control, *, time_ms: float = 50.0):
    """Portamento: ramp a stepped control value to a smooth signal over
    ``time_ms``.

    ``control`` is a control port (a float, a number box, a sequencer's pitch
    outlet). Returns a ``[line~]`` -- feed it into an oscillator's frequency to
    hear notes glide instead of jump.

        freq = glide(p, pitch, time_ms=80)
        osc = p.obj("phasor~"); p.link(freq, 0, osc, 0)
    """
    pk = patch.obj(f"pack f {time_ms}")
    ln = patch.obj("line~")
    wire(patch, control, pk, 0)
    patch.link(pk, 0, ln, 0)
    return ln


def smooth(patch, control, *, time_ms: float = 25.0, lo: float = 0.0,
           hi: float = 1.0):
    """Scale a 0..1 control into ``lo..hi`` and de-zipper it to a signal.

    The staple for wiring a normalised control (an X-Y pad axis, a slider) to a
    parameter: no clicks when it jumps. Returns a ``[line~]``.
    """
    span = patch.obj(f"* {hi - lo}")
    off = patch.obj(f"+ {lo}")
    pk = patch.obj(f"pack f {time_ms}")
    ln = patch.obj("line~")
    wire(patch, control, span, 0)
    patch.link(span, 0, off, 0)
    patch.link(off, 0, pk, 0)
    patch.link(pk, 0, ln, 0)
    return ln
