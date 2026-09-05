"""Scale and rhythm tables: Euclidean rows, a scale-table bank, and the
scale-degree lookup -- degree in, fractional MIDI out (maqam quarter tones
included).

    from pdbuild.modules import euclid, scale_tables, scale_degree

    euclid(5, 16)                 # -> [1,0,0,1,0,0,1,0,0,1,0,0,1,0,0,0]
    scale_tables(p, MAQAMS)       # [r maqam] -> "; scale 0 ..; qflag 0 .." per preset
    midi = scale_degree(p, deg)   # tonic + 12*octave + scale[d mod 7] + qflag[d mod 7] * neut
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

__all__ = ["euclid", "euclid_rows", "ScaleTables", "scale_tables", "scale_degree"]


def _wire(patch, src, outlet, dst, inlet=0) -> None:
    if hasattr(patch, "link"):
        patch.link(src, outlet, dst, inlet)
    else:
        patch.connect(src, outlet, dst, inlet)


# --------------------------------------------------------------------------- #
# Euclidean rhythms
# --------------------------------------------------------------------------- #

def euclid(k: int, n: int = 16) -> list[int]:
    """E(k, n): ``k`` onsets spread as evenly as possible over ``n`` steps
    (Bjorklund), as a 0/1 row for ``step_tables`` -- the DENSITY control of
    choral_break precomputed one row per k. Rotated so step 0 carries an
    onset and the longest gap falls at the end (E(5,16) = x..x..x..x..x...);
    choral_break's rows are the same rhythms rotated by a few steps."""
    if n <= 0:
        raise ValueError("n must be positive")
    k = max(0, min(int(k), n))
    raw = [(i + 1) * k // n - i * k // n for i in range(n)]
    if k == 0:
        return raw
    first = raw.index(1)
    return raw[first:] + raw[:first]


def euclid_rows(n: int = 16) -> list[list[int]]:
    """All rows E(0, n) .. E(n, n) -- one preset per density."""
    return [euclid(k, n) for k in range(n + 1)]


# --------------------------------------------------------------------------- #
# scale tables
# --------------------------------------------------------------------------- #

@dataclass
class ScaleTables:
    scale_table: str
    qflag_table: str
    select_recv: str
    names: list
    nodes: list = field(default_factory=list)


def scale_tables(patch, scales: Sequence[tuple], *, select_recv: str = "maqam",
                 scale_table: str = "scale", qflag_table: str = "qflag", size: int = 8) -> ScaleTables:
    """A bank of scales as two tables: ``scale`` (semitone offset of each
    degree from the tonic) and ``qflag`` (1 where the degree is NEUTRAL --
    a half-flat that sits at ``base + neutral``, 0..1 semitone, 0.5 = the
    quarter tone). ``scales`` are ``(name, offsets, qflags)``; ``[r maqam]``
    (an index) loads a preset with one message. Pair with ``scale_degree``.

        MAQAMS = [("BAYATI", [0, 1, 3, 5, 7, 8, 10], [0, 1, 0, 0, 0, 0, 0]),
                  ("RAST",   [0, 2, 3, 5, 7, 9, 10], [0, 0, 1, 0, 0, 0, 1])]
    """
    nodes: list = []
    nodes.append(patch.obj(f"table {scale_table} {size}"))
    nodes.append(patch.obj(f"table {qflag_table} {size}"))
    r = patch.obj(f"r {select_recv}")
    sel = patch.obj("sel " + " ".join(str(i) for i in range(len(scales))))
    _wire(patch, r, 0, sel, 0)
    nodes += [r, sel]
    names = []
    for i, (name, base, qflag) in enumerate(scales):
        if len(base) != len(qflag):
            raise ValueError(f"scale {name!r}: offsets and qflags differ in length")
        m = patch.msg(f"; {scale_table} 0 " + " ".join(str(v) for v in base)
                      + f" ; {qflag_table} 0 " + " ".join(str(int(v)) for v in qflag))
        _wire(patch, sel, i, m, 0)
        nodes.append(m)
        names.append(name)
    return ScaleTables(scale_table, qflag_table, select_recv, names, nodes)


def scale_degree(patch, degree, *, scale_table: str = "scale", qflag_table: str = "qflag",
                 tonic_recv: str = "tonic", neutral_recv: str = "neut", degrees_per_octave: int = 7):
    """Scale degree (any integer, negative = below the tonic) -> fractional
    MIDI note::

        midi = tonic + 12 * floor(d / 7) + scale[d mod 7] + qflag[d mod 7] * neutral

    reading the ``scale_tables`` bank and ``[r tonic]`` / ``[r neut]``.
    ``degree`` is a control port (a node or ``(node, outlet)``); returns the
    node whose outlet is the MIDI pitch -- feed it ``[mtof]``. This is
    lila_rig's ``maqam_deg`` inline: the same maqam system for keys, bass
    and the melody loop, so they always agree. ``[mod]``/``[div]`` are Pd's
    floor versions, so degree -1 is the 7th below the tonic.
    """
    node, outlet = degree if isinstance(degree, tuple) else (degree, 0)
    n = degrees_per_octave
    t = patch.obj("t f f f")
    _wire(patch, node, outlet, t, 0)
    ex = patch.obj("expr $f2 + $f3 + $f4*$f5 + $f6")
    # octave (fires first, right outlet)
    dv = patch.obj(f"div {n}")
    _wire(patch, t, 2, dv, 0)
    oc = patch.obj("* 12")
    _wire(patch, dv, 0, oc, 0)
    _wire(patch, oc, 0, ex, 1)
    # scale index -> base offset + neutral flag
    md = patch.obj(f"mod {n}")
    _wire(patch, t, 1, md, 0)
    t2 = patch.obj("t f f")
    _wire(patch, md, 0, t2, 0)
    tq = patch.obj(f"tabread {qflag_table}")
    _wire(patch, t2, 1, tq, 0)
    _wire(patch, tq, 0, ex, 3)
    ts = patch.obj(f"tabread {scale_table}")
    _wire(patch, t2, 0, ts, 0)
    _wire(patch, ts, 0, ex, 2)
    rn = patch.obj(f"r {neutral_recv}")
    _wire(patch, rn, 0, ex, 4)
    rt = patch.obj(f"r {tonic_recv}")
    _wire(patch, rt, 0, ex, 5)
    # the degree itself fires last, into the hot inlet
    _wire(patch, t, 0, ex, 0)
    return ex
