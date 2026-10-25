"""The ambient tier -- the slow, spacious blocks a calm generative piece is
built from: a stereo reverb, a drone of breathing partials, a soft polyphonic
pluck, a band-limited noise bed, an LFO locked to a loop length, and a seeded
random walk over a scale.

    from pdbuild import Patch
    from pdbuild.modules import drone, noise_bed, pluck, reverb, scale_walk

    p = Patch()
    clock = p.obj("metro 600"); p.init(clock, 1)
    walk = scale_walk(p, clock, scale=(0, 2, 4, 7, 9), tonic=60, density=0.4, seed=3)
    hz = p.obj("mtof"); p.link(walk.midi, 0, hz, 0)
    notes = pluck(p, None, hz, decay=2.5)            # no trigger: every pitch plays
    bed = drone(p, 110.0)
    air = noise_bed(p, lo=500, hi=4000, seed=7)
    bus = p.obj("+~"); p.link(notes, 0, bus, 0); p.link(bed, 0, bus, 1)
    bus2 = p.obj("+~"); p.link(bus, 0, bus2, 0); p.link(air, 0, bus2, 1)
    room = reverb(p, bus2, size=0.7, mix=0.35)
    dac = p.obj("dac~"); p.link(room.left, 0, dac, 0); p.link(room.right, 0, dac, 1)

Everything is vanilla Pd (no externals, and no ``extra/`` abstractions such
as ``rev2~``, so the blocks also work where ``extra/`` is absent). Everything
random is seeded at load or on the first tick, so a render is reproducible
bit for bit and independent of where in the file the module was built. Each
claim below is backed by a render in ``tests/test_ambient.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from ._util import wire
from .voices import _freq_signal

__all__ = ["Stereo", "reverb", "drone", "pluck", "noise_bed", "loop_lfo",
           "ScaleWalk", "scale_walk"]


@dataclass
class Stereo:
    """A left/right pair of audio nodes (outlet 0 of each is the signal)."""

    left: object
    right: object


def _loadbang_msg(patch, text: str, target, inlet: int = 0):
    """``[loadbang] -> [text( -> target``; returns the message box."""
    m = patch.msg(text)
    patch.link(patch.loadbang(), 0, m, 0)
    patch.link(m, 0, target, inlet)
    return m


def _sum(patch, ports):
    """Sum signal ports with a chain of ``[+~]``; returns the last node (or
    the single port's node)."""
    ports = list(ports)
    acc = ports[0]
    for nxt in ports[1:]:
        s = patch.obj("+~")
        wire(patch, acc, s, 0)
        wire(patch, nxt, s, 1)
        acc = s
    return acc


def _loop_rate(rate: float, loop_s: float | None) -> float:
    """``rate`` rounded to a whole number (>= 1) of cycles per ``loop_s``."""
    if loop_s is None:
        return rate
    return max(1, round(rate * loop_s)) / loop_s


# --------------------------------------------------------------------------- #
# Butterworth sections whose coefficients follow the running sample rate
# --------------------------------------------------------------------------- #

def _butter(patch, sig, kind: str, freq: float, order: int = 4):
    """``order``-th order Butterworth lowpass or highpass at ``freq`` Hz as a
    cascade of ``[biquad~]`` sections (24 dB/octave at order 4). Coefficients
    are written for 44.1 kHz and recomputed at load from ``[samplerate~]``
    (RBJ cookbook, bilinear with prewarp), so the band edge holds at 48 kHz
    too. ``[lop~]``/``[hip~]`` are one-pole (6 dB/octave): ``[hip~ 300] ->
    [lop~ 3000]`` keeps only 37 % of white noise's energy in the band
    (measured); this cascade keeps 88 %."""
    if kind not in ("lp", "hp"):
        raise ValueError(f"kind must be 'lp' or 'hp', got {kind!r}")
    if order % 2:
        raise ValueError("order must be even")
    out = sig
    for k in range(1, order // 2 + 1):
        q = 1.0 / (2.0 * math.cos(math.pi * (2 * k - 1) / (2 * order)))
        bq = patch.obj("biquad~ " + " ".join(f"{c:.9g}" for c in _rbj(kind, freq, q, 44100.0)))
        wire(patch, out, bq, 0)
        # load-time recompute at the real rate: [samplerate~] -> 5 expressions -> list
        w = f"(6.28318531*{freq:g}/$f1)"
        a = f"(sin({w})/{2 * q:.9g})"
        if kind == "lp":
            ff = [f"(1-cos({w}))/2/(1+{a})", f"(1-cos({w}))/(1+{a})", f"(1-cos({w}))/2/(1+{a})"]
        else:
            ff = [f"(1+cos({w}))/2/(1+{a})", f"-(1+cos({w}))/(1+{a})", f"(1+cos({w}))/2/(1+{a})"]
        fb = [f"2*cos({w})/(1+{a})", f"-(1-{a})/(1+{a})"]
        sr = patch.obj("samplerate~")
        patch.link(patch.loadbang(), 0, sr, 0)
        ex = patch.obj("expr " + "; ".join(fb + ff))
        patch.link(sr, 0, ex, 0)
        pk = patch.obj("pack f f f f f")
        for i in range(5):
            patch.link(ex, i, pk, i)          # outlet 0 fires last, into the hot inlet
        patch.link(pk, 0, bq, 0)
        out = bq
    return out


def _rbj(kind: str, freq: float, q: float, sr: float) -> list[float]:
    """Pd ``[biquad~]`` coefficients ``fb1 fb2 ff1 ff2 ff3`` for an RBJ
    lowpass/highpass. Pd's form: w[n] = x[n] + fb1 w[n-1] + fb2 w[n-2],
    y[n] = ff1 w[n] + ff2 w[n-1] + ff3 w[n-2] (so fb = -a1, -a2)."""
    w = 2 * math.pi * freq / sr
    c, a = math.cos(w), math.sin(w) / (2 * q)
    a0 = 1 + a
    if kind == "lp":
        b = [(1 - c) / 2, 1 - c, (1 - c) / 2]
    else:
        b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
    return [2 * c / a0, -(1 - a) / a0] + [v / a0 for v in b]


# --------------------------------------------------------------------------- #
# reverb -- a feedback delay network
# --------------------------------------------------------------------------- #

_FDN_MS = (43.7, 51.1, 59.3, 67.1, 73.9, 83.3, 91.7, 101.9)   # x (0.5 + 0.7 size)
_DIFFUSER_MS = (4.77, 3.59, 12.73, 9.31)                      # input allpasses
_MOD_HZ = (0.13, 0.17, 0.11, 0.19, 0.07, 0.23, 0.09, 0.29)    # line-length wobble


def _hadamard(patch, ports):
    """Mix ``ports`` (a power of two of them) through an unnormalised
    Hadamard matrix with butterflies of ``[+~]``/``[-~]``: lossless up to the
    factor sqrt(n), which the caller folds into its gains."""
    cur = list(ports)
    n, h = len(cur), 1
    while h < n:
        nxt = list(cur)
        for i in range(0, n, 2 * h):
            for j in range(i, i + h):
                s, d = patch.obj("+~"), patch.obj("-~")
                for node in (s, d):
                    wire(patch, cur[j], node, 0)
                    wire(patch, cur[j + h], node, 1)
                nxt[j], nxt[j + h] = s, d
        cur, h = nxt, h * 2
    return cur


def _allpass(patch, sig, ms: float, g: float):
    """Schroeder allpass: w = x + g w[-D]; y = w[-D] - g w. Smears a click into
    a dense burst without colouring the spectrum."""
    buf = patch.uid("ap")
    into = patch.obj("+~")
    dw = patch.obj(f"delwrite~ {buf} {ms + 5:g}")
    dr = patch.obj(f"delread~ {buf} {ms:g}")
    fb = patch.obj(f"*~ {g:g}")
    ff = patch.obj(f"*~ {-g:g}")
    out = patch.obj("+~")
    wire(patch, sig, into, 0)
    patch.link(into, 0, dw, 0)
    patch.link(dr, 0, fb, 0)
    patch.link(fb, 0, into, 1)
    patch.link(into, 0, ff, 0)
    patch.link(dr, 0, out, 0)
    patch.link(ff, 0, out, 1)
    return out


def reverb(patch, sig, *, size: float = 0.6, damping: float = 0.4, mix: float = 0.3,
           decay_s: float | None = None, predelay_ms: float = 12.0,
           diffusion: float = 0.6, mod_ms: float = 0.3) -> Stereo:
    """A lush stereo reverb: predelay -> four input allpass diffusers -> an
    eight-line feedback delay network (Hadamard mixing, a damping lowpass in
    every line, line lengths wobbled slowly to stop metallic ringing) ->
    two decorrelated output taps. Returns a ``Stereo`` (``.left``, ``.right``).

    ``sig`` is a port, or a ``Stereo`` / ``(left_port, right_port)`` list,
    whose channels are summed into the tank while the dry path stays stereo.
    ``size`` 0..1 sets the room: line lengths scale x0.5..x1.2 and the decay
    time is ``RT60 = 0.4 * 25**size`` s (0.4 s .. 10 s; 0.6 -> 2.7 s) unless
    ``decay_s`` gives it directly. ``damping`` 0..1 moves the in-loop lowpass
    from 18 kHz (bright, no damping) down to 900 Hz (a dark, soft hall): highs
    die faster than lows. ``mix`` blends dry (0) to wet (1); at 0 the output
    is the input, sample for sample. The wet level is normalised by the decay
    time, so a long hall is not much louder than a small room.

    Stable by construction: the Hadamard matrix over sqrt(8) is orthogonal and
    every line's gain is 10**(-3 d / RT60) < 1, so there is no clip in the
    loop. Verified: an impulse rings out with a measured RT60 (Schroeder
    T20) of 0.72 / 1.86 / 4.9 s at size 0.2 / 0.5 / 0.8 against a nominal
    0.76 / 2.0 / 5.25; 20 s of noise into the longest, undamped setting
    stays finite and settles (RMS 0.072 in both 5-10 s and 15-20 s); the
    taps are decorrelated (|r| < 0.06); damping 0.8 drops the late tail's
    centroid from 4.3 kHz to 330 Hz; and ``mix=0`` renders identical to the
    dry input, sample for sample.
    """
    if not 0.0 <= mix <= 1.0:
        raise ValueError("mix must be in 0..1")
    if not 0.0 <= diffusion < 1.0:
        raise ValueError("diffusion must be in 0..1 (exclusive of 1)")
    rt60 = float(decay_s) if decay_s is not None else 0.4 * 25.0 ** size
    if rt60 <= 0:
        raise ValueError("decay must be positive")
    scale = 0.5 + 0.7 * min(max(size, 0.0), 1.0)
    lines = [d * scale for d in _FDN_MS]
    cutoff = 18000.0 * (1.0 / 20.0) ** min(max(damping, 0.0), 1.0)
    n = len(lines)

    # dry channels and the mono send into the tank
    if isinstance(sig, Stereo):
        dry_l, dry_r = sig.left, sig.right
    elif isinstance(sig, list):
        dry_l, dry_r = sig
    else:
        dry_l = dry_r = sig
    if dry_l is dry_r:
        send = dry_l
    else:
        send = patch.obj("+~")
        wire(patch, dry_l, send, 0)
        wire(patch, dry_r, send, 1)
        half = patch.obj("*~ 0.5")
        patch.link(send, 0, half, 0)
        send = half

    if predelay_ms > 0:
        pbuf = patch.uid("pre")
        pw = patch.obj(f"delwrite~ {pbuf} {predelay_ms + 10:g}")
        wire(patch, send, pw, 0)
        send = patch.obj(f"delread~ {pbuf} {predelay_ms:g}")
    if diffusion > 0:
        for ms in _DIFFUSER_MS:
            send = _allpass(patch, send, ms, diffusion)

    # the network: read (wobbled) -> damp -> gain -> Hadamard -> + input -> write
    writes, damped, gained = [], [], []
    for i, d in enumerate(lines):
        buf = patch.uid("fdn")
        writes.append(patch.obj(f"delwrite~ {buf} {d + mod_ms + 20:.6g}"))
        if mod_ms > 0:
            lfo = patch.obj(f"osc~ {_MOD_HZ[i % len(_MOD_HZ)]:g}")
            depth = patch.obj(f"*~ {mod_ms:g}")
            centre = patch.obj(f"+~ {d:.6g}")
            rd = patch.obj(f"vd~ {buf}")
            patch.link(lfo, 0, depth, 0)
            patch.link(depth, 0, centre, 0)
            patch.link(centre, 0, rd, 0)
        else:
            rd = patch.obj(f"delread~ {buf} {d:.6g}")
        lp = patch.obj(f"lop~ {cutoff:.6g}")
        patch.link(rd, 0, lp, 0)
        g = 10.0 ** (-3.0 * (d / 1000.0) / rt60) / math.sqrt(n)
        gn = patch.obj(f"*~ {g:.9g}")
        patch.link(lp, 0, gn, 0)
        damped.append(lp)
        gained.append(gn)
    mixed = _hadamard(patch, gained)
    for i in range(n):
        into = patch.obj("+~")
        wire(patch, mixed[i], into, 0)
        wire(patch, send, into, 1)
        patch.link(into, 0, writes[i], 0)

    # two output taps on orthogonal Hadamard rows (1 and 2): decorrelated L/R.
    # Energy gain of the tank ~ 8 * RT60 / (13.8 * mean line), so normalise.
    mean_s = sum(lines) / n / 1000.0
    norm = math.sqrt(13.8 * mean_s / (n * rt60))
    outs = []
    for row in (1, 2):
        acc = damped[0]
        for i in range(1, n):
            sign = -1 if bin(row & i).count("1") % 2 else 1
            node = patch.obj("+~" if sign > 0 else "-~")
            wire(patch, acc, node, 0)
            patch.link(damped[i], 0, node, 1)
            acc = node
        wet = patch.obj(f"*~ {norm * mix:.9g}")
        patch.link(acc, 0, wet, 0)
        outs.append(wet)

    result = []
    for dry, wet in zip((dry_l, dry_r), outs):
        d = patch.obj(f"*~ {1.0 - mix:g}")
        wire(patch, dry, d, 0)
        o = patch.obj("+~")
        patch.link(d, 0, o, 0)
        patch.link(wet, 0, o, 1)
        result.append(o)
    return Stereo(result[0], result[1])


# --------------------------------------------------------------------------- #
# drone -- partials that breathe
# --------------------------------------------------------------------------- #

_DRONE_HZ = (0.047, 0.071, 0.031, 0.089, 0.059, 0.113, 0.037, 0.083)


def drone(patch, pitch, *, partials: Sequence[tuple[float, float]] = (
              (1, 1.0), (2, 0.5), (3, 0.33), (4, 0.25), (5, 0.2)),
          lfo_rates: float | Sequence[float] | None = None, lfo_depth: float = 0.6,
          loop_s: float | None = None, glide_ms: float = 0.0, gain: float = 0.2):
    """A sustained drone: a sum of sine partials at ``ratio x pitch``, each
    with its own slow amplitude LFO, so the timbre breathes -- one overtone
    swells while another fades -- and never repeats exactly.

    ``partials`` are ``(ratio, gain)`` pairs (integer ratios = a harmonic
    series; 1.5 adds a fifth). ``lfo_rates`` (Hz) is one rate for all or one
    per partial; the default is a set of slow, mutually prime-ish rates
    between 0.03 and 0.11 Hz. ``lfo_depth`` 0..1 is how far each partial dips
    (0 = a steady organ tone). ``loop_s`` rounds every rate to a whole number
    of cycles per loop, so the drone repeats seamlessly over the loop. The
    LFOs start at spread phases. Peaks never exceed ``gain`` (the partial
    gains are normalised). ``pitch`` is Hz, a constant or a control port.

    Verified: a 110 Hz drone keeps 99.99 % of its power on the 110 Hz series
    (A#2's series: 0 %), all five partials are present, over 30 s its level
    wanders 5.4 dB but never more than 0.06 dB between neighbouring 50 ms
    windows, ``lfo_depth=0`` is steady (0.01 dB), and with ``loop_s=8`` the
    second 8 s matches the first to 16-bit rounding.
    """
    if not partials:
        raise ValueError("partials must not be empty")
    k = len(partials)
    if lfo_rates is None:
        rates = [_DRONE_HZ[i % len(_DRONE_HZ)] for i in range(k)]
    elif isinstance(lfo_rates, (int, float)):
        rates = [float(lfo_rates)] * k
    else:
        rates = [float(r) for r in lfo_rates]
        if len(rates) != k:
            raise ValueError("lfo_rates needs one rate per partial")
    rates = [_loop_rate(r, loop_s) for r in rates]
    total = sum(abs(g) for _, g in partials) or 1.0
    const = isinstance(pitch, (int, float))
    freq = None if const else _freq_signal(patch, pitch, glide_ms)

    outs = []
    for i, ((ratio, g), rate) in enumerate(zip(partials, rates)):
        if const:
            osc = patch.obj(f"osc~ {pitch * ratio:.9g}")
        else:
            fr = patch.obj(f"*~ {ratio:g}")
            patch.link(freq, 0, fr, 0)
            osc = patch.obj("osc~")
            patch.link(fr, 0, osc, 0)
        node = osc
        if lfo_depth > 0:
            lfo = patch.obj(f"osc~ {rate:.9g}")
            _loadbang_msg(patch, f"{(i / k + 0.5) % 1.0:g}", lfo, 1)   # spread start phases
            sw = patch.obj(f"*~ {lfo_depth / 2:g}")
            base = patch.obj(f"+~ {1 - lfo_depth / 2:g}")
            vca = patch.obj("*~")
            patch.link(lfo, 0, sw, 0)
            patch.link(sw, 0, base, 0)
            patch.link(osc, 0, vca, 0)
            patch.link(base, 0, vca, 1)
            node = vca
        lvl = patch.obj(f"*~ {gain * g / total:.9g}")
        patch.link(node, 0, lvl, 0)
        outs.append(lvl)
    return _sum(patch, outs)


# --------------------------------------------------------------------------- #
# pluck -- a few decaying partials, round-robin polyphony
# --------------------------------------------------------------------------- #

def pluck(patch, trigger, pitch, *, decay: float = 2.0, brightness: float = 0.5,
          partials: int = 6, voices: int = 4, attack_ms: float = 4.0, gain: float = 0.3):
    """A soft plucked tone: ``partials`` harmonics with exponential decays,
    the upper ones dying sooner (as on a real string), dealt round-robin to
    ``voices`` voices so a note rings on under the next one. Returns the
    audio out.

    ``pitch`` is Hz: a constant, or a control port. With ``trigger=None`` every
    pitch that arrives plays (pitch first, then the pluck, in that order); with
    a ``trigger`` port (a bang, or a 1) the last pitch received plays on each
    trigger. ``decay`` is the fundamental's RT60 in seconds (partial k's is
    ``decay / (1 + 0.5 (k-1))``); ``brightness`` 0..1 sets partial k's level
    to ``brightness**(k-1)`` (0 = a sine, 1 = all harmonics equal). The decay
    is a straight line in dB (``[vline~] -> [dbtorms~]``), so it is truly
    exponential and a retriggered voice ramps up from where it is, with no
    click. Peaks stay below ``gain``.

    Built from oscillators rather than a Karplus-Strong delay loop: a Pd delay
    feedback loop cannot be shorter than one block (64 samples), which caps a
    plain-canvas string at 689 Hz; this voice is exact at any pitch.

    Verified: f0 219.99 / 880.01 / 1500.01 Hz for 220 / 880 / 1500; a
    3.3 ms rise and a percussive envelope (sustain ratio 0.4); 40 dB down at
    0.70 s for ``decay=1`` and 2.00 s for ``decay=3`` (2/3 of the RT60, as
    designed); brightness 0.1 -> 0.8 moves the centroid 221 -> 399 Hz; and
    three notes a quarter second apart still ring together on four voices
    (on one voice only the last is left).
    """
    if voices < 1 or partials < 1:
        raise ValueError("voices and partials must be >= 1")
    # what plays: a held pitch, fired by the trigger (or by the pitch itself)
    if isinstance(pitch, (int, float)):
        if trigger is None:
            raise ValueError("a constant pitch needs a trigger")
        hold = patch.obj(f"f {pitch:g}")
    else:
        hold = patch.obj("f")
        wire(patch, pitch, hold, 0 if trigger is None else 1)
    if trigger is not None:
        tb = patch.obj("t b")                # a 1 from a gate must not become the pitch
        wire(patch, trigger, tb, 0)
        patch.link(tb, 0, hold, 0)

    # round-robin dispatch: "<voice> <hz>" -> [route 0 1 ..]
    if voices == 1:
        feeds = [(hold, 0)]
    else:
        tfb = patch.obj("t f b")
        patch.link(hold, 0, tfb, 0)
        cnt = patch.obj("f")
        inc = patch.obj("+ 1")
        wrap = patch.obj(f"mod {voices}")
        pre = patch.obj("list prepend")
        route = patch.obj("route " + " ".join(str(v) for v in range(voices)))
        patch.link(tfb, 1, cnt, 0)            # bang first: next voice index...
        patch.link(cnt, 0, inc, 0)
        patch.link(inc, 0, wrap, 0)
        patch.link(wrap, 0, cnt, 1)
        patch.link(cnt, 0, pre, 1)
        patch.link(tfb, 0, pre, 0)            # ...then the pitch, tagged with it
        patch.link(pre, 0, route, 0)
        feeds = [(route, v) for v in range(voices)]

    amps = [brightness ** (k - 1) if k > 1 else 1.0 for k in range(1, partials + 1)]
    ks = [k for k, a in zip(range(1, partials + 1), amps) if a >= 1e-3]
    total = sum(amps[k - 1] for k in ks)
    outs = []
    for feed in feeds:
        tv = patch.obj("t b f")
        wire(patch, feed, tv, 0)
        for k in ks:
            mul = patch.obj(f"* {k}")
            osc = patch.obj("osc~")
            patch.link(tv, 1, mul, 0)         # frequency first (right outlet)...
            patch.link(mul, 0, osc, 0)
            t60_ms = 1000.0 * decay / (1 + 0.5 * (k - 1))
            env_msg = patch.msg(f"100 {attack_ms:g}, 0 {t60_ms * 100 / 60:.6g} {attack_ms:g}")
            patch.link(tv, 0, env_msg, 0)     # ...then the pluck
            ln = patch.obj("vline~")
            db = patch.obj("dbtorms~")
            vca = patch.obj("*~")
            lvl = patch.obj(f"*~ {gain * amps[k - 1] / total:.9g}")
            patch.link(env_msg, 0, ln, 0)
            patch.link(ln, 0, db, 0)
            patch.link(osc, 0, vca, 0)
            patch.link(db, 0, vca, 1)
            patch.link(vca, 0, lvl, 0)
            outs.append(lvl)
    return _sum(patch, outs)


# --------------------------------------------------------------------------- #
# noise bed -- water, air, rain
# --------------------------------------------------------------------------- #

# std of [noise~] -> [lop~ 3] -> [lop~ 3] at 44.1 kHz, measured in Pd 0.56.2
# (20 s render: 0.0064); lowpassed white noise's std grows as sqrt(cutoff)
_FLUTTER_STD_3HZ = 0.0064


def noise_bed(patch, *, lo: float = 300.0, hi: float = 3000.0, swell_rate: float = 0.07,
              swell_depth: float = 0.6, flutter: float = 0.3, flutter_hz: float = 3.0,
              seed: int = 1, loop_s: float | None = None, gain: float = 0.1):
    """A band of filtered noise with a slow swell: surf, wind, air, rain or
    running water depending on the band (low and narrow = distant surf; 1-6
    kHz with flutter = rain or a stream; high and wide = air).

    White ``[noise~]`` -> 24 dB/octave Butterworth highpass at ``lo`` and
    lowpass at ``hi`` (``[biquad~]`` sections, recomputed for the running
    sample rate at load) -> a swell (a sine LFO at ``swell_rate`` Hz dipping
    the level by ``swell_depth``, starting at the quiet end) times a flutter
    (seeded, lowpassed noise at about ``flutter_hz``: the irregular bubbling
    that makes it water rather than a steady hiss; ``flutter`` is its depth).
    ``gain`` is roughly the RMS at the top of a swell, whatever the band
    width. ``loop_s`` rounds the swell to whole cycles per loop. Both noise
    sources are seeded at load (``[noise~]`` takes ``seed <n>``), so the bed
    is the same on every render and does not depend on how many other
    ``[noise~]`` objects were built before it.

    Verified: 88 % of the energy lies in 300..3000 Hz (84 % for 2..6 kHz;
    the rest is white noise's skirt above ``hi``) and 97 % within a quarter
    beyond the edges; the edges sit at -3 dB at 44.1 and 48 kHz alike; RMS
    is ``gain`` (0.100); in-band spectral flatness is 0.99 (a drone in the
    band: 4e-6); the level envelope peaks at the swell rate; and two renders
    with one seed are identical, with another ``[noise~]`` built before it
    or not, while another seed differs.
    """
    if not 0 < lo < hi:
        raise ValueError("need 0 < lo < hi")
    noise = patch.obj("noise~")
    _loadbang_msg(patch, f"seed {int(seed)}", noise)
    band = _butter(patch, _butter(patch, noise, "hp", lo), "lp", hi)
    # white noise~ is uniform in -1..1 (RMS 0.577); a band keeps (hi-lo)/nyquist of it
    norm = 1.0 / (0.577 * math.sqrt(min(hi - lo, 22050.0) / 22050.0))
    level = patch.obj(f"*~ {gain * norm:.9g}")
    patch.link(band, 0, level, 0)
    node = level
    if swell_depth > 0:
        rate = _loop_rate(swell_rate, loop_s)
        lfo = patch.obj(f"osc~ {rate:.9g}")
        sw = patch.obj(f"*~ {swell_depth / 2:g}")
        base = patch.obj(f"+~ {1 - swell_depth / 2:g}")
        vca = patch.obj("*~")
        patch.link(lfo, 0, sw, 0)
        patch.link(sw, 0, base, 0)
        patch.link(node, 0, vca, 0)
        patch.link(base, 0, vca, 1)
        node = vca
    if flutter > 0:
        fn = patch.obj("noise~")
        _loadbang_msg(patch, f"seed {int(seed) + 7919}", fn)
        l1 = patch.obj(f"lop~ {flutter_hz:g}")
        l2 = patch.obj(f"lop~ {flutter_hz:g}")
        std = _FLUTTER_STD_3HZ * math.sqrt(flutter_hz / 3.0)
        depth = patch.obj(f"*~ {flutter / std:.6g}")         # unit std, times the depth
        one = patch.obj("+~ 1")
        floor = patch.obj("max~ 0")
        vca = patch.obj("*~")
        patch.link(fn, 0, l1, 0)
        patch.link(l1, 0, l2, 0)
        patch.link(l2, 0, depth, 0)
        patch.link(depth, 0, one, 0)
        patch.link(one, 0, floor, 0)
        patch.link(node, 0, vca, 0)
        patch.link(floor, 0, vca, 1)
        node = vca
    return node


# --------------------------------------------------------------------------- #
# loop_lfo -- whole cycles per loop
# --------------------------------------------------------------------------- #

def loop_lfo(patch, *, loop_s: float, cycles: int = 1, phase: float = 0.0,
             shape: str = "sine", lo: float = 0.0, hi: float = 1.0):
    """A signal LFO that completes exactly ``cycles`` cycles every ``loop_s``
    seconds, so whatever it modulates comes round to the same place at the
    loop point: a loop-locked swell, filter sweep or pan.

    The phase is a 0..1 ramp that a ``[metro]`` restarts every loop through
    ``[vline~]`` (``0, 1 <loop_ms>``), times ``cycles``, offset by ``phase``
    (in cycles) and wrapped (``[wrap~]``), then shaped: 'sine' (a raised
    cosine, at ``lo`` at phase 0), 'triangle', or 'ramp' (``lo`` rising to
    ``hi``). Output spans ``lo..hi``. Returns the signal node. The metro
    starts at load.

    Why not a free-running ``[phasor~ cycles/loop_s]``: it keeps its phase
    in 32-bit fixed point, so it drifts. Measured in Pd 0.56.2 from a
    32-bit float capture over 60 s: a free ``[phasor~]`` at 3/7 Hz is off by
    1.0e-4 of a cycle after six 7 s loops (3e-6 to 6e-6 of a cycle per
    cycle; ~15 ms after an hour at a 4 s loop). Restarted by the scheduler,
    whose logical time is double precision and whose ``[vline~]`` starts are
    sub-sample accurate, the ramp is the same every loop: 2e-8 to 7e-8 of a
    cycle after one loop and under 2e-6 after a minute (at most 3.6e-6 of
    the range, for a 5-cycle triangle) -- roughly 100x tighter, and locked
    to any ``[metro]`` clock in the patch.
    """
    if loop_s <= 0 or cycles < 1:
        raise ValueError("loop_s must be > 0 and cycles >= 1")
    if shape not in ("sine", "triangle", "ramp"):
        raise ValueError(f"unknown shape {shape!r} (sine|triangle|ramp)")
    loop_ms = loop_s * 1000.0
    clock = patch.obj(f"metro {loop_ms:.10g}")
    patch.init(clock, 1)
    ramp = patch.msg(f"0, 1 {loop_ms:.10g}")
    ln = patch.obj("vline~")
    patch.link(clock, 0, ramp, 0)
    patch.link(ramp, 0, ln, 0)
    node = ln
    if cycles > 1:
        mul = patch.obj(f"*~ {int(cycles)}")
        patch.link(node, 0, mul, 0)
        node = mul
    if phase % 1.0:
        off = patch.obj(f"+~ {phase % 1.0:g}")
        patch.link(node, 0, off, 0)
        node = off
    if node is not ln or shape == "ramp":
        wr = patch.obj("wrap~")                  # 1.0 at the loop's end reads 0
        patch.link(node, 0, wr, 0)
        node = wr
    span, mid = hi - lo, (hi + lo) / 2
    if shape == "sine":
        c = patch.obj("cos~")
        sc = patch.obj(f"*~ {-span / 2:g}")
        of = patch.obj(f"+~ {mid:g}")
        patch.link(node, 0, c, 0)
        patch.link(c, 0, sc, 0)
        patch.link(sc, 0, of, 0)
        return of
    if shape == "triangle":
        tri = patch.obj("expr~ 1 - abs(2*$v1 - 1)")
        patch.link(node, 0, tri, 0)
        node = tri
    sc = patch.obj(f"*~ {span:g}")
    of = patch.obj(f"+~ {lo:g}")
    patch.link(node, 0, sc, 0)
    patch.link(sc, 0, of, 0)
    return of


# --------------------------------------------------------------------------- #
# scale_walk -- a seeded random walk over a scale
# --------------------------------------------------------------------------- #

@dataclass
class ScaleWalk:
    """What ``scale_walk`` emits, once per played note."""

    midi: object            # node: outlet 0 emits the MIDI note
    degree: tuple           # port: the scale degree, emitted just before
    density_recv: str | None
    lo: int
    hi: int


def _weight_table(step_weights) -> list[int]:
    """Steps repeated in proportion to their weight: integer weights exactly,
    others rounded to parts per thousand."""
    pairs = list(step_weights.items()) if isinstance(step_weights, dict) else list(step_weights)
    if not pairs or any(w < 0 for _, w in pairs) or sum(w for _, w in pairs) <= 0:
        raise ValueError("step_weights need non-negative weights with a positive sum")
    ws = [w for _, w in pairs]
    if not all(float(w).is_integer() for w in ws):
        total = sum(ws)
        ws = [round(1000 * w / total) for w in ws]
    table: list[int] = []
    for (step, _), w in zip(pairs, ws):
        table += [int(step)] * int(w)
    return table


def scale_walk(patch, clock, *, scale: Sequence[float] = (0, 2, 4, 7, 9), tonic: float = 60,
               seed: int = 1, step_weights=((-2, 1), (-1, 3), (0, 1), (1, 3), (2, 1)),
               density: float = 0.5, lo: int = -5, hi: int = 9, start: int = 0,
               density_recv: str | None = None) -> ScaleWalk:
    """A seeded random walk over scale degrees: on each ``clock`` tick, with
    probability ``density`` a note plays, one step from the last. Returns a
    ``ScaleWalk`` whose ``.midi`` outlet emits the MIDI note (feed ``[mtof]``
    and a voice, e.g. ``pluck(p, None, hz)``) and whose ``.degree`` port emits
    the degree (for ``scale_degree`` and its scale-table bank instead).

    ``clock`` is a control port that bangs (a ``[metro]``) or a receive name.
    ``scale`` is the semitone offsets of one octave's degrees from ``tonic``
    (a MIDI note); the default is a major pentatonic. ``step_weights`` are
    ``(step, weight)`` pairs (or a dict): mostly steps of one, some of two,
    some repeats. The walk stays in degrees ``lo..hi``, reflecting off the
    ends, and starts from ``start``. ``density_recv`` names a receive (0..1)
    that changes the density live.

    Two seeded ``[random]``s: one decides *whether* a tick plays, the other
    *which* step -- and the second is drawn only when a note plays. So the
    k-th note is the same whatever the density: density thins the timing of
    one melody rather than choosing another. Both are seeded (and the tables
    filled) on the first tick, ahead of the first draw, so the walk is the
    same however the patch's loadbangs happen to be ordered.

    Verified by counting printed notes over ~170 ticks: density 0.2 plays 40
    and 0.8 plays 139 (0 plays none); one seed repeats the same notes, with
    or without other ``[random]``s built before it; another seed does not;
    the sparser walk is the denser one's opening, note for note; and every
    note is on the scale, one step (of at most 2) from the last.
    """
    if not scale:
        raise ValueError("scale must not be empty")
    if not lo <= start <= hi or hi - lo < 2:
        raise ValueError("need lo <= start <= hi and a range of at least 2 degrees")
    n = len(scale)
    steps = _weight_table(step_weights)
    midis = [tonic + 12 * (d // n) + scale[d % n] for d in range(lo, hi + 1)]
    step_tab, midi_tab = patch.uid("walkstep"), patch.uid("walkmidi")
    patch.obj(f"table {step_tab} {len(steps)}")
    patch.obj(f"table {midi_tab} {len(midis)}")

    if isinstance(clock, str):
        clk = patch.obj(f"r {clock}")
    else:
        clk = clock
    tick = patch.obj("t b b")
    wire(patch, clk, tick, 0)

    coin = patch.obj("random 1000")
    stepper = patch.obj(f"random {len(steps)}")

    # first tick only: seed both generators and fill the tables, then close
    once = patch.obj("spigot 1")
    patch.link(tick, 1, once, 0)               # right outlet: before the draw
    init = patch.obj("t b b")
    patch.link(once, 0, init, 0)
    shut = patch.msg("0")
    patch.link(init, 1, shut, 0)
    patch.link(shut, 0, once, 1)
    for text, target in ((f"seed {int(seed)}", coin), (f"seed {int(seed) + 1}", stepper)):
        m = patch.msg(text)
        patch.link(init, 0, m, 0)
        patch.link(m, 0, target, 0)
    fill = patch.msg(f"; {step_tab} 0 " + " ".join(map(str, steps))
                     + f"; {midi_tab} 0 " + " ".join(f"{v:g}" for v in midis))
    patch.link(init, 0, fill, 0)

    # the coin: play when random(1000) < density * 1000
    gate = patch.obj(f"moses {round(density * 1000)}")
    patch.link(tick, 0, coin, 0)
    patch.link(coin, 0, gate, 0)
    if density_recv:
        rd = patch.obj(f"r {density_recv}")
        scale_d = patch.obj("* 1000")
        patch.link(rd, 0, scale_d, 0)
        patch.link(scale_d, 0, gate, 1)

    # the step: degree += step, reflected into lo..hi
    play = patch.obj("t b")
    patch.link(gate, 0, play, 0)
    patch.link(play, 0, stepper, 0)
    look = patch.obj(f"tabread {step_tab}")
    patch.link(stepper, 0, look, 0)
    acc = patch.obj(f"+ {start}")
    patch.link(look, 0, acc, 0)
    reflect = patch.obj(f"expr max({lo}, min({hi}, if($f1 > {hi}, {2 * hi} - $f1, "
                        f"if($f1 < {lo}, {2 * lo} - $f1, $f1))))")
    patch.link(acc, 0, reflect, 0)
    keep = patch.obj("t f f")
    patch.link(reflect, 0, keep, 0)
    patch.link(keep, 1, acc, 1)                # store the new degree first
    off = patch.obj(f"- {lo}")
    patch.link(keep, 0, off, 0)
    midi = patch.obj(f"tabread {midi_tab}")
    patch.link(off, 0, midi, 0)
    return ScaleWalk(midi=midi, degree=(keep, 0), density_recv=density_recv, lo=lo, hi=hi)
