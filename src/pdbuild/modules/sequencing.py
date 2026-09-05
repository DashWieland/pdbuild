"""The control tier: a swung clock, per-pulse step tables with intensity
tiers, and a mutating melody loop -- the sequencing machinery of lila_rig,
promoted to modules and verified by rendering.

Where a voice makes sound from a trigger, these make the TRIGGERS: they read
control receives (``tempo``, ``swing``, ``pattern``, ``density``, ...) and
broadcast named sends (``pulse``, ``bar``, ``note_in``, ...) that voices and
each other listen to. That is the broadcast-clock idiom (cookbook 4): every
part stays independent and everything stays locked. Nothing here is wired
directly to a voice -- attach one to the sends.

Works on both builders (``Patch`` and ``PdPatch``); all names can be
``prefix``-ed so two machines coexist.

    from pdbuild import Patch
    from pdbuild.modules import swing_clock, step_tables, melody_loop, kick

    p = Patch()
    clock = swing_clock(p, groups=(3, 3, 3, 3))          # 12/8: tempo/swing/run receives -> pulse/bar sends
    tabs = step_tables(p, [{"d": [1, 0, 0, 2, 0, 0, 1, 0, 0, 3, 0, 0]}])   # d plays when 0 < level <= intensity
    drum = kick(p, tabs.hits["d"])
    loop = melody_loop(p, phrases=["0 . 0 2 . 3 4 . 3 2 . 1  0 . 0 3 . 4 3 . 2 0 . ."])
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

__all__ = [
    "Clock", "swing_clock", "StepTables", "step_tables", "gated_value",
    "MelodyLoop", "melody_loop", "phrase_steps", "step_priority", "REST", "HOLD",
]

REST, HOLD = -99, -98            # melody-loop tokens: a rest releases the voice; a hold plays nothing


def _wire(patch, src, outlet, dst, inlet=0) -> None:
    if hasattr(patch, "link"):
        patch.link(src, outlet, dst, inlet)
    else:
        patch.connect(src, outlet, dst, inlet)


def _loadbang_msg(patch, text: str):
    """``[loadbang] -> [text(``; returns the message box."""
    m = patch.msg(text)
    _wire(patch, patch.loadbang(), 0, m, 0)
    return m


# --------------------------------------------------------------------------- #
# the clock
# --------------------------------------------------------------------------- #

@dataclass
class Clock:
    """The sends a ``swing_clock`` broadcasts, and its receives."""

    pulse: str          # pulse index within the bar, every pulse (after the retime)
    pulsepos: str       # position of the pulse within its beat group (0 = the leaned-on first)
    bar: str            # a bang on pulse 0 -- fires BEFORE `pulse` on that pulse
    halfpulse: str      # half the current pulse length, ms (for doubles / tremolo)
    pulsems: str        # the un-swung pulse length, ms (for tempo-synced delays)
    reset: str          # RECEIVE: a bang restarts the bar on the next tick
    tempo_recv: str
    swing_recv: str
    run_recv: str
    barlen: int
    groups: tuple
    metro: object
    counter: object
    nodes: list = field(default_factory=list)


def swing_clock(patch, *, groups: Sequence[int] = (3, 3, 3, 3), tempo_recv: str = "tempo",
                swing_recv: str = "swing", run_recv: str = "run", factor_recv: str | None = None,
                pulse_div: int = 2, metro_ms: float = 180.0, prefix: str = "") -> Clock:
    """One ``[metro]`` retimed on every tick so the first pulse of each beat
    group is lengthened and the rest shortened -- swing inside the group.

    ``groups`` are the beat-group sizes in pulses (``(3, 3, 3, 3)`` = 12/8,
    ``(2, 3)`` = 5/8); the bar is their sum. Pulse length is
    ``60000 / (bpm * pulse_div)`` ms (``pulse_div=2``: the pulse is an 8th).
    On pulse ``i`` the metro's interval is set, during its own tick, to

        pulsems * (pos == 0 ? 1 + swing : 1 - swing / (size - 1))

    which applies to the NEXT tick (verified), so the group's total length is
    unchanged and a once-per-bar voice keeps its bar length exactly.
    ``factor_recv`` (optional) multiplies the tempo -- feed it a ``[line]`` from
    1 to 1.3 over 45 s for a lila's accelerando. Reads ``[r run]`` (1 starts,
    0 stops), ``[r tempo]`` (BPM) and ``[r swing]`` (0..~0.45); a bang on the
    ``reset`` receive restarts the bar (``step_tables`` sends it on a preset
    change). Order on pulse 0: retime, then ``bar``, then ``pulse``.
    """
    groups = tuple(int(g) for g in groups)
    if not groups or any(g < 1 for g in groups):
        raise ValueError("groups must be non-empty positive pulse counts")
    n = sum(groups)
    P = prefix
    grpos, grsize = [], []
    for g in groups:
        grpos += list(range(g))
        grsize += [g] * g
    nodes: list = []

    # group tables, filled at load
    nodes.append(patch.obj(f"table {P}grpos {n}"))
    nodes.append(patch.obj(f"table {P}grsize {n}"))
    nodes.append(_loadbang_msg(patch, f"; {P}grpos 0 " + " ".join(map(str, grpos))
                               + f"; {P}grsize 0 " + " ".join(map(str, grsize))))

    # pulse length: 60000 / (bpm * div * factor) -> [s pulsems]
    rt = patch.obj(f"r {tempo_recv}")
    ttb = patch.obj("t b f")
    fac = patch.obj("f 1")                                  # tempo factor store (hot: recompute)
    pex = patch.obj(f"expr {60000 / pulse_div:g}/($f1*$f2)")   # $f1 factor (hot), $f2 bpm
    _wire(patch, rt, 0, ttb, 0)
    _wire(patch, ttb, 1, pex, 1)
    _wire(patch, ttb, 0, fac, 0)
    _wire(patch, fac, 0, pex, 0)
    nodes += [rt, ttb, fac, pex]
    if factor_recv:
        rf = patch.obj(f"r {factor_recv}")
        ftf = patch.obj("t f f")
        _wire(patch, rf, 0, ftf, 0)
        _wire(patch, ftf, 1, fac, 1)                        # store first...
        _wire(patch, ftf, 0, pex, 0)                        # ...then recompute with it
        nodes += [rf, ftf]
    spm = patch.obj(f"s {P}pulsems")
    _wire(patch, pex, 0, spm, 0)
    nodes.append(spm)

    # metro + bar counter
    rrun = patch.obj(f"r {run_recv}")
    metro = patch.obj(f"metro {metro_ms:g}")
    _wire(patch, rrun, 0, metro, 0)
    cnt = patch.obj("f")
    _wire(patch, metro, 0, cnt, 0)
    rreset = patch.obj(f"r {P}reset")
    zero = patch.msg("0")
    _wire(patch, rreset, 0, zero, 0)
    _wire(patch, zero, 0, cnt, 1)                            # next tick is pulse 0
    cmod = patch.obj(f"mod {n}")
    _wire(patch, cnt, 0, cmod, 0)
    ctf = patch.obj("t f f")
    _wire(patch, cmod, 0, ctf, 0)
    cinc = patch.obj("+ 1")
    _wire(patch, ctf, 1, cinc, 0)
    _wire(patch, cinc, 0, cnt, 1)
    nodes += [rrun, metro, cnt, rreset, zero, cmod, ctf, cinc]

    # pulse i: (2) retime the metro, (1) bar flag, (0) broadcast
    ptf = patch.obj("t f f f")
    _wire(patch, ctf, 0, ptf, 0)
    itf = patch.obj("t f f")
    _wire(patch, ptf, 2, itf, 0)
    tsize = patch.obj(f"tabread {P}grsize")
    _wire(patch, itf, 1, tsize, 0)                          # size first (cold)...
    tpos = patch.obj(f"tabread {P}grpos")
    _wire(patch, itf, 0, tpos, 0)                           # ...then position (hot)
    iex = patch.obj("expr $f2 * if($f1==0, 1+$f3, if($f4>1, 1-$f3/($f4-1), 1))")
    _wire(patch, tpos, 0, iex, 0)
    rpm = patch.obj(f"r {P}pulsems")
    _wire(patch, rpm, 0, iex, 1)
    rsw = patch.obj(f"r {swing_recv}")
    _wire(patch, rsw, 0, iex, 2)
    _wire(patch, tsize, 0, iex, 3)
    ietf = patch.obj("t f f")
    _wire(patch, iex, 0, ietf, 0)
    _wire(patch, ietf, 1, metro, 1)                         # the next interval
    half = patch.obj("/ 2")
    shalf = patch.obj(f"s {P}halfpulse")
    _wire(patch, ietf, 0, half, 0)
    _wire(patch, half, 0, shalf, 0)
    spos = patch.obj(f"s {P}pulsepos")
    _wire(patch, tpos, 0, spos, 0)
    bsel = patch.obj("sel 0")
    sbar = patch.obj(f"s {P}bar")
    _wire(patch, ptf, 1, bsel, 0)
    _wire(patch, bsel, 0, sbar, 0)
    spulse = patch.obj(f"s {P}pulse")
    _wire(patch, ptf, 0, spulse, 0)
    nodes += [ptf, itf, tsize, tpos, iex, rpm, rsw, ietf, half, shalf, spos, bsel, sbar, spulse]

    return Clock(pulse=f"{P}pulse", pulsepos=f"{P}pulsepos", bar=f"{P}bar", halfpulse=f"{P}halfpulse",
                 pulsems=f"{P}pulsems", reset=f"{P}reset", tempo_recv=tempo_recv, swing_recv=swing_recv,
                 run_recv=run_recv, barlen=n, groups=groups, metro=metro, counter=cnt, nodes=nodes)


# --------------------------------------------------------------------------- #
# step tables with intensity tiers
# --------------------------------------------------------------------------- #

@dataclass
class StepTables:
    tables: dict            # row -> table name
    hits: dict              # row -> node whose outlet 0 BANGS when the row's level passes the gate
    select_recv: str
    reset_send: str | None
    steps: int
    nodes: list = field(default_factory=list)


def _row_message(prefix: str, preset: dict, rows: Sequence[str]) -> str:
    parts = []
    for row in rows:
        vals = preset[row]
        parts.append(f"; {prefix}{row} 0 " + " ".join(str(int(v)) for v in vals))
    return " ".join(parts)


def step_tables(patch, presets: Sequence[dict], *, rows: Sequence[str] | None = None,
                select_recv: str = "pattern", pulse_recv: str = "pulse", intensity_recv: str | None = "intensity",
                reset_send: str | None = "reset", gate_rows: Sequence[str] | None = None,
                prefix: str = "") -> StepTables:
    """Per-pulse pattern tables loaded by ONE message box per preset, with an
    intensity-tier gate per row.

    ``presets`` are dicts ``{row: [level per pulse]}``, all rows the same
    length within a preset. Each row gets a ``[table]``; ``[r pattern]``
    selects the preset whose message fills every table at once (the whole
    pattern is visible in the file), and -- first -- bangs ``reset_send`` so
    the clock restarts its bar. A row's ``hits[row]`` node bangs on a pulse
    when ``0 < level <= INTENSITY`` (``[r intensity]``: 1 sparse, 2 groove,
    3 full -- or every level > 0 when ``intensity_recv`` is None); wire a
    voice to it. Rows that carry VALUES rather than tiers (a bass degree)
    are read with ``gated_value`` so the value is stored before the gate
    fires. Levels are integers; the table size is the longest preset.
    """
    if not presets:
        raise ValueError("step_tables needs at least one preset")
    rows = list(rows) if rows else list(presets[0].keys())
    steps = 0
    for i, pr in enumerate(presets):
        lengths = {len(pr[r]) for r in rows if r in pr}
        missing = [r for r in rows if r not in pr]
        if missing:
            raise ValueError(f"preset {i} lacks rows {missing}")
        if len(lengths) != 1:
            raise ValueError(f"preset {i}: rows differ in length ({sorted(lengths)})")
        steps = max(steps, lengths.pop())
    P = prefix
    nodes: list = []
    tables = {row: f"{P}{row}" for row in rows}
    for row in rows:
        nodes.append(patch.obj(f"table {tables[row]} {steps}"))

    rsel = patch.obj(f"r {select_recv}")
    tfb = patch.obj("t f b")                                 # (1) reset the bar first, (0) then load
    _wire(patch, rsel, 0, tfb, 0)
    if reset_send:
        srst = patch.obj(f"s {reset_send}")
        _wire(patch, tfb, 1, srst, 0)
        nodes.append(srst)
    sel = patch.obj("sel " + " ".join(str(i) for i in range(len(presets))))
    _wire(patch, tfb, 0, sel, 0)
    nodes += [rsel, tfb, sel]
    for i, pr in enumerate(presets):
        m = patch.msg(_row_message(P, pr, rows))
        _wire(patch, sel, i, m, 0)
        nodes.append(m)

    hits: dict = {}
    for row in (gate_rows if gate_rows is not None else rows):
        rp = patch.obj(f"r {pulse_recv}")
        hits[row] = _gate(patch, rp, 0, tables[row], intensity_recv, nodes)
        nodes.append(rp)
    return StepTables(tables=tables, hits=hits, select_recv=select_recv, reset_send=reset_send,
                      steps=steps, nodes=nodes)


def _gate(patch, src, outlet, table: str, intensity_recv: str | None, nodes: list):
    """pulse index -> table level -> bang if 0 < level <= INTENSITY."""
    tr = patch.obj(f"tabread {table}")
    _wire(patch, src, outlet, tr, 0)
    if intensity_recv:
        ex = patch.obj("expr ($f1>0)&&($f1<=$f2)")
        _wire(patch, tr, 0, ex, 0)
        ri = patch.obj(f"r {intensity_recv}")
        _wire(patch, ri, 0, ex, 1)
        nodes.append(ri)
    else:
        ex = patch.obj("> 0")
        _wire(patch, tr, 0, ex, 0)
    sel = patch.obj("sel 1")
    _wire(patch, ex, 0, sel, 0)
    nodes += [tr, ex, sel]
    return sel


def gated_value(patch, tables: StepTables, gate_row: str, value_row: str, *,
                pulse_recv: str = "pulse", intensity_recv: str | None = "intensity"):
    """The value of ``value_row`` on the pulses where ``gate_row`` passes its
    tier gate -- read in the right order: the value is stored (cold) BEFORE
    the gate bangs it out (hot). Returns the ``[f]`` whose outlet carries the
    value; a bass line is ``gated_value(p, tabs, "blev", "bdeg")``."""
    rp = patch.obj(f"r {pulse_recv}")
    tff = patch.obj("t f f")
    _wire(patch, rp, 0, tff, 0)
    tv = patch.obj(f"tabread {tables.tables[value_row]}")
    _wire(patch, tff, 1, tv, 0)                              # value first (right outlet)
    f = patch.obj("f")
    _wire(patch, tv, 0, f, 1)
    gate = _gate(patch, tff, 0, tables.tables[gate_row], intensity_recv, tables.nodes)
    _wire(patch, gate, 0, f, 0)
    tables.nodes += [rp, tff, tv, f]
    return f


# --------------------------------------------------------------------------- #
# the melody loop
# --------------------------------------------------------------------------- #

def phrase_steps(phrase, steps: int) -> list[int]:
    """A phrase as a list of ``steps`` tokens: an int is a scale degree,
    ``REST`` (-99) a rest, ``HOLD`` (-98) a hold. A string uses ``.`` for a
    rest and ``h`` for a hold: ``"4 h h 4 5 . ."``."""
    if isinstance(phrase, str):
        vals = [REST if t == "." else HOLD if t == "h" else int(t) for t in phrase.split()]
    else:
        vals = [int(v) for v in phrase]
    if len(vals) != steps:
        raise ValueError(f"phrase has {len(vals)} steps, the loop has {steps}")
    return vals


def step_priority(steps: int, barlen: int, beat: int) -> list[int]:
    """Rank (0 = strongest) per step of a ``steps``-long loop of ``barlen``-
    pulse bars with ``beat``-pulse beats: bar downbeats, then half-bars, then
    beat starts, then the pulse after each beat, then the rest. DENSITY plays
    the strongest ranks first, so a thinned loop is the same tune breathing.
    For 24 steps of 12/8 this is lila_rig's order."""
    def key(s: int):
        pos = s % barlen
        if pos == 0:
            tier = 0
        elif barlen % 2 == 0 and pos == barlen // 2:
            tier = 1
        elif pos % beat == 0:
            tier = 2
        elif pos % beat == 1:
            tier = 3
        else:
            tier = 4
        return (tier, s)
    order = sorted(range(steps), key=key)
    rank = [0] * steps
    for r, s in enumerate(order):
        rank[s] = r
    return rank


@dataclass
class MelodyLoop:
    table: str
    prio_table: str
    note_send: str          # "degree velocity" per played step
    release_send: str       # a bang on a rest (legato voices release)
    strong_send: str | None # 1/0 per played step: is it a beat-level note
    phrase_recv: str
    mutate_recv: str
    density_recv: str
    autoplay_recv: str
    steps: int
    barlen: int
    nodes: list = field(default_factory=list)


def melody_loop(patch, *, phrases: Sequence, steps: int = 24, barlen: int = 12, beat: int = 3,
                pulse_recv: str = "pulse", bar_recv: str = "bar", reset_recv: str = "reset",
                phrase_recv: str = "phrase", mutate_recv: str = "mutate", density_recv: str = "density",
                autoplay_recv: str = "autoplay", note_send: str = "note_in", release_send: str = "note_release",
                strong_send: str | None = None, strong_rank: int = 8, degree_range: tuple = (-3, 10),
                leaps: Sequence[int] = (0, 4, 7), velocity: tuple = (70, 30, 15, 15),
                prefix: str = "") -> MelodyLoop:
    """A Turing-machine-style melody loop: ``steps`` scale degrees in a table,
    played one per pulse, seeded from a phrase bank, thinned by a priority
    mask, rewritten as it plays.

    - **PHRASE** (``[r phrase]``, an index) seeds the table from ``phrases``
      (strings like ``"0 . 0 2 . 3 4 h"`` -- ``.`` rest, ``h`` hold -- or
      lists of ints).
    - **DENSITY** (``[r density]``, 0..1): a step plays only if its priority
      rank < density * steps, strongest beats first (``step_priority``);
      masked steps become holds, so low density is the same tune with fewer,
      longer notes.
    - **MUTATE** (``[r mutate]``, 0..1): with that probability a step is
      rewritten for the NEXT pass by a scale-aware generator -- stepwise from
      the last real degree (50%), a 2-step (15%), a leap to one of ``leaps``
      (15%), a rest (10%) or a hold (10%), clipped to ``degree_range``. 0 is a
      locked loop, 1 a random walk with memory.
    - a rest bangs ``release_send`` (a legato voice releases); a degree emits
      ``"degree velocity"`` on ``note_send``, velocity accented by rank
      (``velocity`` = base, +downbeat, +beat, random spread); if
      ``strong_send`` is given, 1/0 (rank < ``strong_rank``) is sent just
      before each note, so a second voice can take only the beat-level notes.
    - the step is ``pulse + barlen * pass``; the pass counter advances on
      ``[r bar]`` and is ZEROED by ``[r reset]`` together with the clock, so
      the loop stays aligned to bar 1 (a real bug: the pattern init reset
      the pulse counter but not the parity, and the loop played its second
      half first).
    """
    if steps % barlen:
        raise ValueError("steps must be a whole number of bars")
    passes = steps // barlen
    P = prefix
    nodes: list = []
    lo, hi = degree_range
    base_vel, acc_down, acc_beat, spread = velocity

    mel = f"{P}mel"
    prio = f"{P}prio"
    nodes.append(patch.obj(f"table {mel} {steps}"))
    nodes.append(patch.obj(f"table {prio} {steps}"))
    nodes.append(_loadbang_msg(patch, f"; {prio} 0 " + " ".join(map(str, step_priority(steps, barlen, beat)))))

    # phrase bank -> the table
    rph = patch.obj(f"r {phrase_recv}")
    psel = patch.obj("sel " + " ".join(str(i) for i in range(len(phrases))))
    _wire(patch, rph, 0, psel, 0)
    nodes += [rph, psel]
    for i, ph in enumerate(phrases):
        m = patch.msg(f"; {mel} 0 " + " ".join(str(v) for v in phrase_steps(ph, steps)))
        _wire(patch, psel, i, m, 0)
        nodes.append(m)

    # pass counter: advances on `bar`, zeroed by `reset` (realigns to bar 1)
    rbar = patch.obj(f"r {bar_recv}")
    parf = patch.obj("f")
    pinc = patch.obj("+ 1")
    pmod = patch.obj(f"mod {passes}")
    _wire(patch, rbar, 0, parf, 0)
    _wire(patch, parf, 0, pinc, 0)
    _wire(patch, pinc, 0, pmod, 0)
    _wire(patch, pmod, 0, parf, 1)
    rrst = patch.obj(f"r {reset_recv}")
    pzero = patch.msg("0")
    _wire(patch, rrst, 0, pzero, 0)
    _wire(patch, pzero, 0, parf, 1)
    poff = patch.obj(f"* {barlen}")
    _wire(patch, parf, 0, poff, 0)
    nodes += [rbar, parf, pinc, pmod, rrst, pzero, poff]

    # step = pulse + offset, if AUTO-PLAY
    rp = patch.obj(f"r {pulse_recv}")
    aspg = patch.obj("spigot")
    _wire(patch, rp, 0, aspg, 0)
    rauto = patch.obj(f"r {autoplay_recv}")
    _wire(patch, rauto, 0, aspg, 1)
    stp = patch.obj("+")
    _wire(patch, aspg, 0, stp, 0)
    _wire(patch, poff, 0, stp, 1)
    st4 = patch.obj("t f f f f")
    _wire(patch, stp, 0, st4, 0)
    nodes += [rp, aspg, rauto, stp, st4]

    # (3) priority -> active flag (DENSITY), strong flag, accent
    tpr = patch.obj(f"tabread {prio}")
    _wire(patch, st4, 3, tpr, 0)
    prt = patch.obj("t f f f")
    _wire(patch, tpr, 0, prt, 0)
    actlt = patch.obj("<")
    _wire(patch, prt, 2, actlt, 0)
    rden = patch.obj(f"r {density_recv}")
    dsteps = patch.obj(f"* {steps}")
    _wire(patch, rden, 0, dsteps, 0)
    _wire(patch, dsteps, 0, actlt, 1)
    strlt = patch.obj(f"< {strong_rank}")
    _wire(patch, prt, 1, strlt, 0)
    strf = patch.obj("f")
    _wire(patch, strlt, 0, strf, 1)
    nodes += [tpr, prt, actlt, rden, dsteps, strlt, strf]

    # (2) token -> store; a real degree is also the mutation base
    ttk = patch.obj(f"tabread {mel}")
    _wire(patch, st4, 2, ttk, 0)
    tkt = patch.obj("t f f")
    _wire(patch, ttk, 0, tkt, 0)
    tokf = patch.obj("f")
    _wire(patch, tkt, 0, tokf, 1)
    bmo = patch.obj("moses -97.5")
    _wire(patch, tkt, 1, bmo, 0)
    nodes += [ttk, tkt, tokf, bmo]

    # (1) mutate this step for the next pass, with probability MUTATE
    mtb = patch.obj("t b f")
    _wire(patch, st4, 1, mtb, 0)
    twr = patch.obj(f"tabwrite {mel}")
    _wire(patch, mtb, 1, twr, 1)
    mrn = patch.obj("random 1000")
    _wire(patch, mtb, 0, mrn, 0)
    mlt = patch.obj("<")
    _wire(patch, mrn, 0, mlt, 0)
    rmut = patch.obj(f"r {mutate_recv}")
    m1000 = patch.obj("* 1000")
    _wire(patch, rmut, 0, m1000, 0)
    _wire(patch, m1000, 0, mlt, 1)
    msel = patch.obj("sel 1")
    _wire(patch, mlt, 0, msel, 0)
    grn = patch.obj("random 100")
    _wire(patch, msel, 0, grn, 0)
    gm1 = patch.obj("moses 50")
    _wire(patch, grn, 0, gm1, 0)
    gadd = patch.obj(f"+ {leaps[0] if leaps else 0}")       # base = last real degree (cold)
    _wire(patch, bmo, 1, gadd, 1)
    g1 = patch.obj("random 2"); g1m = patch.obj("* 2"); g1s = patch.obj("- 1")      # +-1
    _wire(patch, gm1, 0, g1, 0); _wire(patch, g1, 0, g1m, 0); _wire(patch, g1m, 0, g1s, 0)
    _wire(patch, g1s, 0, gadd, 0)
    gclip = patch.obj(f"clip {lo} {hi}")
    _wire(patch, gadd, 0, gclip, 0)
    _wire(patch, gclip, 0, twr, 0)
    gm2 = patch.obj("moses 65")
    _wire(patch, gm1, 1, gm2, 0)
    g2 = patch.obj("random 2"); g2m = patch.obj("* 4"); g2s = patch.obj("- 2")      # +-2
    _wire(patch, gm2, 0, g2, 0); _wire(patch, g2, 0, g2m, 0); _wire(patch, g2m, 0, g2s, 0)
    _wire(patch, g2s, 0, gadd, 0)
    gm3 = patch.obj("moses 80")
    _wire(patch, gm2, 1, gm3, 0)
    gj = patch.obj(f"random {len(leaps)}")
    gjs = patch.obj("sel " + " ".join(str(i) for i in range(len(leaps))))
    _wire(patch, gm3, 0, gj, 0); _wire(patch, gj, 0, gjs, 0)
    nodes += [mtb, twr, mrn, mlt, rmut, m1000, msel, grn, gm1, gadd, g1, g1m, g1s, gclip, gm2, g2, g2m, g2s, gm3, gj, gjs]
    for i, v in enumerate(leaps):
        jm = patch.msg(str(v))
        _wire(patch, gjs, i, jm, 0)
        _wire(patch, jm, 0, twr, 0)
        nodes.append(jm)
    gm4 = patch.obj("moses 90")
    _wire(patch, gm3, 1, gm4, 0)
    grest = patch.msg(str(REST)); ghold = patch.msg(str(HOLD))
    _wire(patch, gm4, 0, grest, 0); _wire(patch, grest, 0, twr, 0)
    _wire(patch, gm4, 1, ghold, 0); _wire(patch, ghold, 0, twr, 0)
    nodes += [gm4, grest, ghold]

    # (0) play the step: REST releases, HOLD does nothing, a degree plays if active
    ptb = patch.obj("t b")
    _wire(patch, st4, 0, ptb, 0)
    _wire(patch, ptb, 0, tokf, 0)
    pmo1 = patch.obj("moses -98.5")
    _wire(patch, tokf, 0, pmo1, 0)
    relb = patch.obj("t b")                                  # a REST is a bang, not the token -99
    _wire(patch, pmo1, 0, relb, 0)
    srel = patch.obj(f"s {release_send}")
    _wire(patch, relb, 0, srel, 0)
    nodes.append(relb)
    pmo2 = patch.obj("moses -97.5")
    _wire(patch, pmo1, 1, pmo2, 0)
    pact = patch.obj("spigot")
    _wire(patch, pmo2, 1, pact, 0)
    _wire(patch, actlt, 0, pact, 1)
    etf = patch.obj("t f b b")
    _wire(patch, pact, 0, etf, 0)
    nodes += [ptb, pmo1, srel, pmo2, pact, etf]
    if strong_send:
        _wire(patch, etf, 2, strf, 0)
        sstr = patch.obj(f"s {strong_send}")
        _wire(patch, strf, 0, sstr, 0)
        nodes.append(sstr)
    vrn = patch.obj(f"random {max(1, int(spread))}")
    _wire(patch, etf, 1, vrn, 0)
    vex = patch.obj(f"expr {base_vel} + if($f2<4, {acc_down}, if($f2<12, {acc_beat}, 0)) + $f1")
    _wire(patch, vrn, 0, vex, 0)
    _wire(patch, prt, 0, vex, 1)
    epk = patch.obj("pack f f")
    _wire(patch, vex, 0, epk, 1)
    _wire(patch, etf, 0, epk, 0)
    snote = patch.obj(f"s {note_send}")
    _wire(patch, epk, 0, snote, 0)
    nodes += [vrn, vex, epk, snote]

    return MelodyLoop(table=mel, prio_table=prio, note_send=note_send, release_send=release_send,
                      strong_send=strong_send, phrase_recv=phrase_recv, mutate_recv=mutate_recv,
                      density_recv=density_recv, autoplay_recv=autoplay_recv, steps=steps, barlen=barlen,
                      nodes=nodes)
