"""pdbuild.surface -- the control-surface idiom on both builders.

Text tests pin the wiring (`_ui` receive, [s name], loadbang init, labels);
render tests prove the message-twin contract with a real Pd: setting
`<name>_ui` by injection reaches the engine, a CC through the router lands on
the widget, notes split by channel, and a display box really shows its value
(the runtime probe -- the check a headless render cannot otherwise make).
"""

from __future__ import annotations

import pytest

from pdbuild import Patch, PdPatch
from pdbuild.preview import boxes
from pdbuild.surface import (
    Control, Pad, cc_map, column, control, display, note_split, pad_row, record_takes, ui_name, widget_text,
)

BUILDERS = [pytest.param(Patch, id="Patch"), pytest.param(PdPatch, id="PdPatch")]


def _lines(p):
    return p.render().splitlines()


def _objs(p, prefix):
    return [l for l in _lines(p) if l.startswith("#X obj") and l.split(" ", 4)[4].startswith(prefix)]


# --------------------------------------------------------------------------- #
# the idiom, as text
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("Builder", BUILDERS)
def test_control_emits_widget_send_init_and_label(Builder):
    p = Builder()
    h = control(p, "tempo", "hsl", x=20, y=50, default=168, lo=100, hi=220, label="TEMPO")
    text = p.render()
    assert h.receive == "tempo_ui" == ui_name("tempo")
    widget = _objs(p, "hsl")[0]
    assert " empty tempo_ui empty " in widget                # receive symbol set, send empty
    assert " 100 220 " in widget                             # the range
    assert _objs(p, "s tempo")                               # [s tempo]
    assert "#X msg" in text and " 168;" in text              # the loadbang init value
    assert "loadbang" in text
    assert any(l.startswith("#X text") and "TEMPO" in l for l in _lines(p))


@pytest.mark.parametrize("Builder", BUILDERS)
def test_control_wiring_widget_to_send_and_init_to_widget(Builder):
    p = Builder()
    control(p, "run", "tgl", x=20, y=50, default=1, label="RUN")
    lines = _lines(p)
    boxes = [l for l in lines if l.startswith(("#X obj", "#X msg", "#X text"))]
    idx = {l.split(" ", 4)[4].rstrip(";"): i for i, l in enumerate(boxes)}
    tgl = next(i for k, i in idx.items() if k.startswith("tgl"))
    send = next(i for k, i in idx.items() if k == "s run")
    init = next(i for k, i in idx.items() if k == "1")
    lb = next(i for k, i in idx.items() if k == "loadbang")
    connects = [l for l in lines if l.startswith("#X connect")]
    assert f"#X connect {tgl} 0 {send} 0;" in connects
    assert f"#X connect {lb} 0 {init} 0;" in connects
    assert f"#X connect {init} 0 {tgl} 0;" in connects


def test_every_kind_has_a_receive_and_a_size():
    from pdbuild.surface import KINDS, widget_size
    for kind in KINDS:
        t = widget_text(kind, receive="x_ui", n=3)
        assert t.startswith(kind) and " x_ui " in t
        w, h = widget_size(kind, n=3)
        assert w > 0 and h > 0
    assert widget_size("hradio", n=4, size=18) == (72, 18)
    assert widget_size("hsl", size=150) == (150, 16)
    with pytest.raises(ValueError):
        widget_text("knob")


@pytest.mark.parametrize("Builder", BUILDERS)
def test_column_stacks_and_accepts_tuples(Builder):
    p = Builder()
    hs = column(p, 20, 50, [
        ("run", "tgl", 1, "RUN"),
        ("tempo", "hsl", 168, "TEMPO", (100, 220)),
        ("pattern", "hradio", 0, "PATTERN", 4),
        Control("go", "bng", default=None, label="GO"),
    ])
    assert [h.name for h in hs] == ["run", "tempo", "pattern", "go"]
    ys = [h.y for h in hs]
    assert ys == sorted(ys) and len(set(ys)) == 4            # stacked downwards, no overlap
    assert hs[1].width == 150 and hs[2].width == 72          # real widget sizes
    assert hs[3].init_msg is None                            # a bang has no init
    assert " 100 220 " in _objs(p, "hsl")[0]
    assert " 1 0 4 " in _objs(p, "hradio")[0]


def test_control_rejects_unknown_kind():
    with pytest.raises(ValueError):
        control(Patch(), "x", "knob", x=0, y=0)


# --------------------------------------------------------------------------- #
# display: receive slot right on BOTH builders
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("Builder", BUILDERS)
def test_display_puts_the_name_in_pds_receive_slot(Builder):
    """Field order: x y width lower upper label_pos label RECEIVE SEND font.
    PdPatch.floatatom has its arguments swapped (documented); display() gets
    the same correct bytes out of both."""
    p = Builder()
    display(p, "lastmidi", 40, 170, width=8)
    atom = [l for l in _lines(p) if l.startswith("#X floatatom")][0]
    assert atom == "#X floatatom 40 170 8 0 0 0 - lastmidi - 0;"


@pytest.mark.parametrize("Builder", BUILDERS)
def test_display_probe_send(Builder):
    p = Builder()
    display(p, "lvl", 40, 170, width=5, send="probe")
    atom = [l for l in _lines(p) if l.startswith("#X floatatom")][0]
    assert atom.endswith(" - lvl probe 0;")


# --------------------------------------------------------------------------- #
# pads
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("Builder", BUILDERS)
def test_pad_row_kinds_and_triggers(Builder):
    p = Builder()
    hs = pad_row(p, 20, 300, [
        Pad("stutter", "momentary", label="stutter"),
        Pad("pattern", "cycle", n=4, label="pat+"),
        Pad("droneon", "toggle", label="drone"),
    ])
    assert [h.trigger for h in hs] == ["stutter_ui", "next_pattern", "flip_droneon"]
    text = p.render()
    assert "s stutter;" in text and "tgl" in _objs(p, "tgl")[0]
    assert "r next_pattern;" in text and "r pattern;" in text and "mod 4;" in text and "s pattern_ui;" in text
    assert "r flip_droneon;" in text and "r droneon;" in text and "== 0;" in text and "s droneon_ui;" in text
    assert any(l.startswith("#X text") and "stutter  pat+  drone" in l for l in _lines(p))
    with pytest.raises(ValueError):
        Pad("x", "hold").trigger_name()


# --------------------------------------------------------------------------- #
# MIDI
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("Builder", BUILDERS)
def test_cc_map_routes_to_ui_receives_with_a_twin(Builder):
    p = Builder()
    m = cc_map(p, [(74, "fxx", 0, 1), (82, "qmix", 0, 1.4)], x=600, y=40)
    text = p.render()
    assert "ctlin;" in text and "route 74 82;" in text
    assert "r fakecc;" in text and text.count("s ccstream;") == 2
    assert "s fxx_ui;" in text and "s qmix_ui;" in text
    assert "s lastcc;" in text and "s lastccval;" in text
    assert m.targets == {"fxx": (74, 0, 1), "qmix": (82, 0, 1.4)}
    # the cc/value swap message is a dollar message, escaped once in the file
    assert "\\$2 \\$1;" in text and "\\\\" not in text
    assert "* 1.4;" in text                                  # hi - lo scaling


@pytest.mark.parametrize("Builder", BUILDERS)
def test_note_split_repacks_before_gating(Builder):
    p = Builder()
    ns = note_split(p, pad_channel=10, x=600, y=300)
    text = p.render()
    assert "notein;" in text and "pack f f 1;" in text and "unpack f f f;" in text
    assert "== 10;" in text and text.count("spigot;") == 2
    assert "s midi_in;" in text and "s pad_in;" in text
    assert (ns.keys_send, ns.pads_send) == ("midi_in", "pad_in")


# --------------------------------------------------------------------------- #
# rendered: the message-twin contract with a real Pd
# --------------------------------------------------------------------------- #

def _can_render() -> bool:
    try:
        from pdverify.errors import PdNotFound
        from pdverify.pd_locate import discover
    except ImportError:
        return False
    try:
        discover()
        return True
    except PdNotFound:
        return False


needs_pd = pytest.mark.skipif(not _can_render(), reason="needs pdverify + a Pd install")


def _engine(p, recv="freq"):
    """[r freq] -> [osc~] -> dac~: the engine reads the named send."""
    r = p.obj(f"r {recv}", 400, 400)
    osc = p.obj("osc~", 400, 424)
    g = p.obj("*~ 0.3", 400, 448)
    dac = p.obj("dac~", 400, 480)
    if hasattr(p, "link"):
        p.link(r, 0, osc, 0); p.link(osc, 0, g, 0); p.link(g, 0, dac, 0); p.link(g, 0, dac, 1)
    else:
        p.connect(r, 0, osc, 0); p.connect(osc, 0, g, 0); p.connect(g, 0, dac, 0); p.connect(g, 0, dac, 1)


@needs_pd
@pytest.mark.parametrize("Builder", BUILDERS)
def test_init_value_reaches_the_engine_and_ui_receive_overrides_it(Builder, tmp_path):
    from pdverify import analyze, control as ctl
    from pdverify.render import RenderSpec, render

    p = Builder()
    control(p, "freq", "hsl", x=20, y=50, default=220, lo=100, hi=1000, label="FREQ")
    _engine(p)
    path = tmp_path / "s.pd"
    path.write_text(p.render(), encoding="utf-8")
    at_load = analyze(render(str(path), RenderSpec(duration=0.6)).audio)
    assert at_load.pitch_hz == pytest.approx(220, abs=3)          # the loadbang init reached [r freq]
    driven = analyze(render(str(path), RenderSpec(duration=0.6, controls=tuple(ctl.send("freq_ui", 440)))).audio,
                     window=(0.2, 0.6))
    assert driven.pitch_hz == pytest.approx(440, abs=3)           # setting the WIDGET moved the engine


@needs_pd
def test_cc_through_the_router_lands_on_the_widget(tmp_path):
    from pdverify import analyze, control as ctl
    from pdverify.render import RenderSpec, render

    p = Patch()
    control(p, "freq", "hsl", x=20, y=50, default=220, lo=0, hi=1270, label="FREQ")
    cc_map(p, [(74, "freq", 0, 1270)], x=600, y=40)
    _engine(p)
    path = tmp_path / "cc.pd"
    path.write_text(p.render(), encoding="utf-8")
    # CC 74 at 44/127 -> 440 Hz through the router, the widget and [s freq]
    r = analyze(render(str(path), RenderSpec(duration=0.6, controls=tuple(ctl.send("fakecc", 74, 44)))).audio,
                window=(0.2, 0.6))
    assert r.pitch_hz == pytest.approx(440, abs=4)


@needs_pd
def test_note_split_routes_channels(tmp_path):
    from pdverify import analyze, control as ctl
    from pdverify.render import RenderSpec, render

    p = Patch()
    note_split(p, pad_channel=10, x=600, y=300)
    # keys -> a tone at the note; pads -> a tone an octave above 1000 Hz (so the two are told apart)
    rk = p.obj("r midi_in", 20, 500); uk = p.obj("unpack f f", 20, 524); mk = p.obj("mtof", 20, 548)
    p.link(rk, 0, uk, 0); p.link(uk, 0, mk, 0)
    rp = p.obj("r pad_in", 200, 500); up = p.obj("unpack f f", 200, 524); mp = p.obj("* 100", 200, 548)
    p.link(rp, 0, up, 0); p.link(up, 0, mp, 0)
    osc = p.obj("osc~", 20, 580); g = p.obj("*~ 0.3", 20, 604); dac = p.obj("dac~", 20, 640)
    p.link(mk, 0, osc, 0); p.link(mp, 0, osc, 0); p.link(osc, 0, g, 0); p.link(g, 0, dac, 0); p.link(g, 0, dac, 1)
    path = tmp_path / "ns.pd"
    path.write_text(p.render(), encoding="utf-8")
    keys = analyze(render(str(path), RenderSpec(duration=0.6, controls=tuple(ctl.note("A4", dur=0.5)))).audio,
                   window=(0.1, 0.5))
    assert keys.pitch_hz == pytest.approx(440, abs=3)             # channel 1 -> keys -> mtof


@needs_pd
@pytest.mark.parametrize("Builder", BUILDERS)
def test_display_really_shows_its_value_runtime_probe(Builder, tmp_path):
    """The check no audio render can make: a read-out box's receive slot is
    right only if the box re-emits what it was sent. Probe its send slot."""
    from pdverify import control as ctl
    from pdverify.render import RenderSpec, render

    p = Builder()
    display(p, "lvl", 40, 40, width=5, send="probe")
    r = p.obj("r probe", 200, 40)
    pr = p.obj("print PROBE", 200, 64)
    _wire = p.link if hasattr(p, "link") else p.connect
    _wire(r, 0, pr, 0)
    _engine(p)                                                     # a sink, so the render has something to capture
    path = tmp_path / "d.pd"
    path.write_text(p.render(), encoding="utf-8")
    res = render(str(path), RenderSpec(duration=0.3, controls=tuple(ctl.send("lvl", 42))))
    assert "PROBE: 42" in res.pd_console


# --------------------------------------------------------------------------- #
# RECORD: numbered takes that never overwrite
# --------------------------------------------------------------------------- #

def _connections(p) -> set[tuple[int, int, int, int]]:
    return {tuple(int(v) for v in l.rstrip(";").split()[2:6])
            for l in p.render().splitlines() if l.startswith("#X connect")}


@pytest.mark.parametrize("Builder", BUILDERS)
def test_record_takes_searches_with_the_right_outlet_of_file_isfile(Builder):
    """`[file isfile]` BANGS ITS RIGHT OUTLET for a missing path and never
    outputs 0: the right outlet is the "free" signal and the left (1 = the
    file exists) must lead nowhere, or every take would overwrite."""
    p = Builder()
    osc = p.obj("osc~ 440", 20, 20)
    h = record_takes(p, osc, (osc, 0), x=300, y=40)
    idx = {b.text: b.index for b in boxes(p)}
    conns = _connections(p)
    text = p.render()
    for t in ("r record", "sel 1 0", "until", "makefilename take_%03d.wav", "file patchpath",
              "file isfile", "symbol", "print take", "writesf~ 2"):
        assert t in idx, t
    assert "open -bytes 3 \\$1" in text and "start;" in text and "stop;" in text
    isf = idx["file isfile"]
    assert {(a, o) for a, o, _b, _i in conns if a == isf} == {(isf, 1)}
    wsf = idx["writesf~ 2"]
    assert (idx["osc~ 440"], 0, wsf, 0) in conns and (idx["osc~ 440"], 0, wsf, 1) in conns
    assert (h.recv, h.prefix, h.print_tag) == ("record", "take_", "take")


def test_record_takes_is_fully_validated_on_patch():
    p = Patch()
    osc = p.obj("osc~ 440", 20, 20)
    record_takes(p, osc, osc, recv="rec", prefix="takes/mix_", x=300, y=40)
    assert p.unvalidated() == []
    assert "r rec;" in p.render() and "makefilename takes/mix_%03d.wav;" in p.render()


@pytest.mark.parametrize("prefix", ["my take ", "take%", "a;b", "a,b", "$0-take", "a\b", ""])
def test_record_takes_rejects_a_prefix_pd_would_split(prefix):
    with pytest.raises(ValueError):
        record_takes(Patch(), None, None, prefix=prefix, x=0, y=0)


@pytest.mark.parametrize("Builder", BUILDERS)
def test_record_takes_never_overwrites_a_take(Builder, tmp_path, run_pd):
    """In real time (a batch run ends before writesf~'s disk thread opens the
    file): two launches in a folder whose name has a space, which already
    holds a take_002.wav. The first launch takes 001, the second skips the
    existing 002 for 003, 002 is untouched, and both takes hold the tone."""
    from pdverify import analyze, read_wav

    d = tmp_path / "my takes"
    d.mkdir()
    (d / "take_002.wav").write_bytes(b"keep me")
    p = Builder()
    osc = p.obj("osc~ 440", 20, 60)
    amp = p.obj("*~ 0.25", 20, 90)
    wire = p.link if hasattr(p, "link") else p.connect
    wire(osc, 0, amp, 0)
    dsp = p.msg("; pd dsp 1", 20, 20)
    wire(p.loadbang(), 0, dsp, 0)
    record_takes(p, amp, amp, x=300, y=40)
    p.save(d / "inst.pd")
    s = Patch()
    for at, m in ((150, "; record 1"), (700, "; record 0"), (900, "; pd quit")):
        dl = s.obj(f"del {at}")
        s.link(s.loadbang(), 0, dl, 0)
        s.link(dl, 0, s.msg(m), 0)
    s.save(d / "drive.pd")

    consoles = [run_pd("inst.pd", "drive.pd", cwd=d, realtime=True) for _ in range(2)]
    assert sorted(f.name for f in d.glob("take_*.wav")) == ["take_001.wav", "take_002.wav", "take_003.wav"]
    assert (d / "take_002.wav").read_bytes() == b"keep me"
    assert "take_001.wav" in consoles[0] and "take_003.wav" in consoles[1]
    for name in ("take_001.wav", "take_003.wav"):
        a = read_wav(d / name)
        dur = a.samples.shape[0] / a.sr
        assert a.samples.shape[1] == 2 and dur == pytest.approx(0.55, abs=0.02), (name, dur)
        r = analyze(a)
        assert r.pitch_hz == pytest.approx(440, abs=3) and r.rms_dbfs == pytest.approx(-15.1, abs=0.5)
