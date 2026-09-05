"""The control tier, verified by RENDERING: a kick on the clock's pulse reads
even intervals at swing 0 and uneven ones at swing 0.4 with the same mean;
a once-per-bar hit reads the bar as sum(groups) pulses; tier gates count
hits per intensity; the melody loop repeats beat for beat at MUTATE 0 and
diverges at MUTATE 1, thickens with DENSITY, realigns to bar 1 on a reset;
the scale-degree lookup lands the quarter tone.

Uses pdverify's musical measurands (0.2.0): ioi statistics, loop similarity,
pitch sequences. Skipped without Pd."""

from __future__ import annotations

import pytest

from pdbuild import Patch, PdPatch
from pdbuild.modules import (
    HOLD, REST, euclid, euclid_rows, gated_value, kick, melody_loop, phrase_steps, scale_degree,
    scale_tables, step_priority, step_tables, subtractive_voice, swing_clock,
)

pytest.importorskip("pdverify")
music = pytest.importorskip("pdverify.music")                # the 0.2.0 measurands
from pdverify import analyze, control                        # noqa: E402
from pdverify.render import RenderSpec, render               # noqa: E402
from pdverify.pd_locate import discover                      # noqa: E402

try:
    discover()
    _HAVE_PD = True
except Exception:
    _HAVE_PD = False

needs_pd = pytest.mark.skipif(not _HAVE_PD, reason="needs a Pd install")

TEMPO = 200                       # pulse = 8th = 150 ms; a 12-pulse bar = 1.8 s
PULSE = 30.0 / TEMPO


def _wire(p, a, o, b, i=0):
    (p.link if hasattr(p, "link") else p.connect)(a, o, b, i)


def _out(p, sig):
    dac = p.obj("dac~")
    _wire(p, sig, 0, dac, 0)
    _wire(p, sig, 0, dac, 1)


def _defaults(p, **values):
    """Initialise the module receives at load, the way a surface's inits do."""
    m = p.msg(" ".join(f"; {k} {v}" for k, v in values.items()))
    _wire(p, p.loadbang(), 0, m, 0)


def _hear(p, dur, controls=(), skip=0.0):
    res = render(p.render(), RenderSpec(duration=dur, controls=tuple(controls)))
    audio = res.audio.slice(skip) if skip else res.audio
    return analyze(audio), res


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #

def test_euclid_rows():
    assert euclid(5, 16) == [1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 0]
    assert euclid(0) == [0] * 16 and euclid(16) == [1] * 16
    assert sum(euclid(7, 12)) == 7
    assert len(euclid_rows(16)) == 17 and [sum(r) for r in euclid_rows(8)] == list(range(9))
    with pytest.raises(ValueError):
        euclid(3, 0)


def test_phrase_steps_tokens():
    assert phrase_steps("4 h . 4", 4) == [4, HOLD, REST, 4]
    assert phrase_steps([0, 2, REST, HOLD], 4) == [0, 2, -99, -98]
    with pytest.raises(ValueError):
        phrase_steps("0 1 2", 4)


def test_step_priority_is_lila_rigs_order_for_12_8():
    order = [0, 12, 6, 18, 3, 9, 15, 21, 1, 4, 7, 10, 13, 16, 19, 22, 2, 5, 8, 11, 14, 17, 20, 23]
    rank = step_priority(24, 12, 3)
    assert [rank.index(r) for r in range(24)] == order        # rank r is held by step order[r]
    assert sorted(rank) == list(range(24))


def test_step_tables_validates_presets():
    with pytest.raises(ValueError):
        step_tables(Patch(), [{"a": [1, 0], "b": [1, 0, 0]}])
    with pytest.raises(ValueError):
        step_tables(Patch(), [{"a": [1, 0]}, {"b": [1, 0]}])
    with pytest.raises(ValueError):
        step_tables(Patch(), [])


@pytest.mark.parametrize("Builder", [Patch, PdPatch])
def test_control_tier_emits_on_both_builders(Builder):
    p = Builder()
    c = swing_clock(p, groups=(2, 3), prefix="k_")
    t = step_tables(p, [{"d": [1, 0, 2, 0, 3]}], pulse_recv=c.pulse, reset_send=c.reset)
    m = melody_loop(p, phrases=["0 . 2 h 4"], steps=5, barlen=5, beat=1, pulse_recv=c.pulse,
                    bar_recv=c.bar, reset_recv=c.reset)
    text = p.render()
    assert c.barlen == 5 and "table k_grpos 5" in text and "s k_pulse" in text and "r k_reset" in text
    # the retime expr's commas are escaped exactly once, on both builders (the
    # two emitters space `\,` differently; Pd accepts both)
    retime = next(l for l in text.splitlines() if "expr $f2 * if($f1==0" in l)
    assert retime.count("\\,") == 4
    assert "," not in retime.replace("\\,", "")               # no bare comma left
    assert "\\\\" not in text
    import re
    norm = re.sub(r"\s+", " ", text.replace("\\;", ";").replace("\\,", ","))
    assert "; d 0 1 0 2 0 3" in norm and t.hits["d"] is not None
    assert "table mel 5" in text and "; mel 0 0 -99 2 -98 4" in norm and m.note_send == "note_in"


# --------------------------------------------------------------------------- #
# swing_clock
# --------------------------------------------------------------------------- #

def _clock_patch(groups=(3, 3, 3, 3), swing=0.0, on="pulse"):
    """A kick on every `pulse` (or once per `bar`)."""
    p = Patch(700, 600, 10)
    c = swing_clock(p, groups=groups)
    _defaults(p, tempo=TEMPO, swing=swing, run=1)
    r = p.obj(f"r {getattr(c, on)}")
    _out(p, kick(p, r, decay=90, gain=0.7))
    return p, c


@needs_pd
def test_swing_zero_is_straight_and_swing_makes_it_uneven():
    straight, _ = _hear(_clock_patch(swing=0.0)[0], 5.0, skip=0.3)
    swung, _ = _hear(_clock_patch(swing=0.4)[0], 5.0, skip=0.3)
    assert straight.ioi_mean_s == pytest.approx(PULSE, abs=0.008)
    assert straight.ioi_cv < 0.05, straight.ioi_cv
    assert swung.ioi_cv > 0.15, swung.ioi_cv
    # the group keeps its total length: same mean pulse
    assert swung.ioi_mean_s == pytest.approx(straight.ioi_mean_s, abs=0.008)


@needs_pd
@pytest.mark.parametrize("groups", [(3, 3, 3, 3), (2, 3), (2, 2, 3)])
def test_bar_length_is_sum_of_groups(groups):
    p, c = _clock_patch(groups=groups, on="bar")
    r, _ = _hear(p, 4 * sum(groups) * PULSE + 0.5, skip=0.1)
    s = music.ioi_stats(r.onset_times)
    assert c.barlen == sum(groups)
    assert s.n >= 2
    assert s.mean_s == pytest.approx(sum(groups) * PULSE, abs=0.02), s


@needs_pd
def test_tempo_and_run_receives_drive_the_clock():
    p, _ = _clock_patch()
    slow, _ = _hear(p, 5.0, controls=control.send("tempo", 120, at=0.05), skip=0.5)
    assert slow.ioi_mean_s == pytest.approx(30.0 / 120, abs=0.01)
    stopped, _ = _hear(p, 3.0, controls=control.send("run", 0, at=0.4), skip=1.0)
    assert stopped.is_silent


# --------------------------------------------------------------------------- #
# step_tables
# --------------------------------------------------------------------------- #

def _tables_patch(intensity):
    p = Patch(700, 600, 10)
    c = swing_clock(p)
    tabs = step_tables(p, [{"d": [1, 0, 0, 2, 0, 0, 1, 0, 0, 3, 0, 0]},
                           {"d": [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}],
                       pulse_recv=c.pulse, reset_send=c.reset)
    _defaults(p, tempo=TEMPO, swing=0, run=1, intensity=intensity, pattern=0)
    _out(p, kick(p, tabs.hits["d"], decay=90, gain=0.7))
    return p


@needs_pd
def test_intensity_tiers_gate_the_hits():
    """Levels 1/2/1/3 on pulses 0/3/6/9: two hits per bar at intensity 1,
    three at 2, four at 3 -- over two bars plus the third downbeat."""
    counts = {}
    for tier in (1, 2, 3):
        r, _ = _hear(_tables_patch(tier), 2 * 12 * PULSE + 0.4)
        counts[tier] = r.onset_count
    assert counts[1] == pytest.approx(5, abs=1)
    assert counts[2] == pytest.approx(7, abs=1)
    assert counts[3] == pytest.approx(9, abs=1)
    assert counts[1] < counts[2] < counts[3], counts


@needs_pd
def test_preset_change_restarts_the_bar():
    """Preset 1 hits only on pulse 0. Switching to it at 1.0 s (mid-bar) must
    restart the bar: a hit right after the switch, then one per bar."""
    p = _tables_patch(3)
    r, _ = _hear(p, 4.5, controls=control.send("pattern", 1, at=1.0))
    after = [t for t in r.onset_times if t > 1.0]
    assert after, r.onset_times
    assert after[0] < 1.0 + 2 * PULSE, after                 # the restarted bar's downbeat, not 1.8 s
    gaps = [b - a for a, b in zip(after, after[1:])]
    assert all(abs(g - 12 * PULSE) < 0.03 for g in gaps), gaps


@needs_pd
def test_gated_value_reads_the_value_before_the_gate():
    """A bass row: degree read on the gated pulses only, and read BEFORE the
    bang so the right value comes out."""
    p = Patch(700, 600, 10)
    c = swing_clock(p)
    tabs = step_tables(p, [{"blev": [1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0],
                            "bdeg": [60, 0, 0, 67, 0, 0, 72, 0, 0, 67, 0, 0]}],
                       gate_rows=(), pulse_recv=c.pulse, reset_send=c.reset)
    deg = gated_value(p, tabs, "blev", "bdeg", pulse_recv=c.pulse)
    _defaults(p, tempo=TEMPO, swing=0, run=1, intensity=3, pattern=0)
    t = p.obj("t b f")
    _wire(p, deg, 0, t, 0)
    hz = p.obj("mtof")
    _wire(p, t, 1, hz, 0)
    _out(p, subtractive_voice(p, t, hz, cutoff=3000, resonance=1, decay=120))
    r, res = _hear(p, 12 * PULSE + 0.3)
    notes = [e.note for e in music.pitch_sequence(res.audio, win_s=0.1)][:4]
    assert notes == ["C4", "G4", "C5", "G4"], notes


# --------------------------------------------------------------------------- #
# melody_loop
# --------------------------------------------------------------------------- #

RIFF = "0 . 0 2 . 3 4 . 3 2 . 1   0 . 0 3 . 4 3 . 2 0 . ."
TWO_BARS = "7 . . 7 . . 7 . . 7 . .   0 . . 0 . . 0 . . 0 . ."      # bar 1 on the 5th, bar 2 on the tonic


def _loop_patch(phrase, mutate, density, strong_send=None):
    p = Patch(900, 700, 10)
    c = swing_clock(p)
    loop = melody_loop(p, phrases=[phrase], pulse_recv=c.pulse, bar_recv=c.bar, reset_recv=c.reset,
                       strong_send=strong_send)
    _defaults(p, tempo=TEMPO, swing=0, run=1, phrase=0, mutate=mutate, density=density, autoplay=1)
    # a voice: degree -> semitones above C4 -> a plucked saw
    r = p.obj(f"r {loop.note_send}")
    un = p.obj("unpack f f")
    _wire(p, r, 0, un, 0)
    t = p.obj("t b f")
    _wire(p, un, 0, t, 0)
    add = p.obj("+ 60")
    hz = p.obj("mtof")
    _wire(p, t, 1, add, 0)
    _wire(p, add, 0, hz, 0)
    _out(p, subtractive_voice(p, t, hz, cutoff=2500, resonance=1, decay=130))
    return p, loop


LOOP_S = 24 * PULSE                                        # 3.6 s


@needs_pd
def test_locked_loop_repeats_and_mutating_loop_diverges():
    locked, res_l = _hear(_loop_patch(RIFF, mutate=0, density=1)[0], 2 * LOOP_S + 0.3)
    _mut, res_m = _hear(_loop_patch(RIFF, mutate=1.0, density=1)[0], 2 * LOOP_S + 0.3)
    s_locked = music.loop_similarity(res_l.audio, LOOP_S, nseg=8)
    s_mut = music.loop_similarity(res_m.audio, LOOP_S, nseg=8)
    assert s_locked.similarity > 0.9, s_locked
    assert s_mut.similarity < s_locked.similarity - 0.1, (s_locked, s_mut)


@needs_pd
def test_density_adds_notes():
    sparse, _ = _hear(_loop_patch(RIFF, mutate=0, density=0.2)[0], LOOP_S + 0.3)
    full, _ = _hear(_loop_patch(RIFF, mutate=0, density=1.0)[0], LOOP_S + 0.3)
    assert sparse.onset_count >= 2
    assert full.onset_count > sparse.onset_count * 1.5, (sparse.onset_count, full.onset_count)


@needs_pd
def test_reset_realigns_the_loop_to_bar_one():
    """The lila_rig v3 bug: the bar restart zeroed the pulse counter but not
    the pass parity, so the loop resumed on its second half. Bar 1 plays the
    5th, bar 2 the tonic; a reset during bar 2 must bring back the 5th."""
    p, _ = _loop_patch(TWO_BARS, mutate=0, density=1)
    bar = 12 * PULSE
    reset_at = bar + 0.5                                     # inside bar 2 (the tonic bar)
    _r, res = _hear(p, 2 * bar + 1.0, controls=control.bang("reset", at=reset_at))
    ev = music.pitch_sequence(res.audio, win_s=0.1)
    before = [e.note for e in ev if bar + 0.05 < e.time < reset_at]
    after = [e.note for e in ev if reset_at + 0.05 < e.time < reset_at + bar - 0.1]
    assert before and set(before) == {"C4"}, before          # bar 2 was playing the tonic
    assert after and set(after) == {"G4"}, after             # after the reset: bar 1, the 5th


@needs_pd
def test_rests_release_and_strong_flag_marks_beats():
    """A REST bangs the release send; the strong flag is 1 on beat-level
    notes and 0 off the beat (printed, read from the console)."""
    p, loop = _loop_patch("0 1 . 0 1 . 0 1 . 0 1 .   0 1 . 0 1 . 0 1 . 0 1 .", mutate=0, density=1,
                          strong_send="strong")
    rr = p.obj(f"r {loop.release_send}")
    pr = p.obj("print REL")
    _wire(p, rr, 0, pr, 0)
    rs = p.obj("r strong")
    ps = p.obj("print STRONG")
    _wire(p, rs, 0, ps, 0)
    _r, res = _hear(p, 12 * PULSE + 0.2)
    lines = res.pd_console.splitlines()
    assert sum("REL: bang" in l for l in lines) >= 3
    strong = [l.split()[-1] for l in lines if l.startswith("STRONG:")]
    assert "1" in strong and "0" in strong, strong           # downbeats strong, the pulse after not


# --------------------------------------------------------------------------- #
# scale_degree
# --------------------------------------------------------------------------- #

BAYATI = ("BAYATI", [0, 1, 3, 5, 7, 8, 10], [0, 1, 0, 0, 0, 0, 0])


def _degree_patch(degree, tonic=62, neut=0.5):
    p = Patch(700, 500, 10)
    scale_tables(p, [BAYATI])
    _defaults(p, maqam=0, tonic=tonic, neut=neut)
    lb = p.obj("loadbang")
    d = p.obj("del 20")                                       # after the tables and receives are set
    _wire(p, lb, 0, d, 0)
    m = p.msg(str(degree))
    _wire(p, d, 0, m, 0)
    midi = scale_degree(p, m)
    hz = p.obj("mtof")
    _wire(p, midi, 0, hz, 0)
    osc = p.obj("osc~")
    _wire(p, hz, 0, osc, 0)
    g = p.obj("*~ 0.3")
    _wire(p, osc, 0, g, 0)
    _out(p, g)
    return p


def _cents(a, b):
    import math
    return 1200 * math.log2(a / b)


@needs_pd
@pytest.mark.parametrize("degree,neut,midi", [
    (0, 0.5, 62.0),        # tonic D4
    (1, 0.5, 63.5),        # Bayati 2nd: E half-flat, the quarter tone
    (1, 0.0, 63.0),        # HALF-FLAT 0 -> Eb
    (1, 1.0, 64.0),        # HALF-FLAT 1 -> E
    (7, 0.5, 74.0),        # the octave
    (-1, 0.5, 60.0),       # below the tonic: the 7th of the octave below (C4)
])
def test_scale_degree_lands_the_pitch(degree, neut, midi):
    r, _ = _hear(_degree_patch(degree, neut=neut), 0.8, skip=0.15)
    expected = 440.0 * 2 ** ((midi - 69) / 12)
    assert r.f0_hz is not None
    assert abs(_cents(r.f0_hz, expected)) < 8, (r.f0_hz, expected)
