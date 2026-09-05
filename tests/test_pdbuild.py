"""pdbuild tests.

pdbuild's whole job is emitting *correct* text — indices and escaping — so these
assert against the rendered `.pd`, not internal state. Pure string generation:
no Pd needed except the two marked integration tests at the bottom.

Several tests below are regressions for bugs that actually shipped into a patch
and cost real debugging time; they're labelled.
"""

import pytest

from pdbuild import PdPatch

BOX_PREFIXES = ("#X obj", "#X msg", "#X text", "#X floatatom")


def records(p):
    return [line.rstrip(";") for line in p.render().splitlines()]


def boxes(p):
    """Box records in index order — exactly what #X connect counts."""
    return [r for r in records(p) if r.startswith(BOX_PREFIXES)]


def connects(p):
    return [r for r in records(p) if r.startswith("#X connect")]


# --------------------------------------------------------------- indices ----

def test_indices_are_sequential():
    p = PdPatch()
    assert [p.obj("osc~"), p.obj("*~"), p.obj("dac~")] == [0, 1, 2]


def test_comment_takes_an_index():
    """REGRESSION: comments count in Pd's connection numbering."""
    p = PdPatch()
    a = p.obj("osc~ 440")
    c = p.comment("a comment", 0, 0)
    b = p.obj("dac~")
    assert (a, c, b) == (0, 1, 2)


def test_floatatom_takes_an_index():
    p = PdPatch()
    a = p.obj("r x")
    f = p.floatatom(0, 0)
    b = p.obj("dac~")
    assert (a, f, b) == (0, 1, 2)


def test_connect_after_comment_targets_the_right_box():
    """REGRESSION: the failure mode is silent — a stray comment shifts every
    later index and connections land on the wrong objects."""
    p = PdPatch()
    src = p.obj("osc~ 440")
    p.comment("explanatory note", 0, 0)
    dst = p.obj("dac~")
    p.connect(src, 0, dst, 0)

    assert connects(p) == ["#X connect 0 0 2 0"]
    assert boxes(p)[2].endswith("dac~")     # index 2 really is the dac~


def test_all_box_kinds_increment_together():
    p = PdPatch()
    ids = [p.obj("f"), p.msg("1"), p.comment("c", 0, 0), p.floatatom(0, 0), p.obj("dac~")]
    assert ids == [0, 1, 2, 3, 4]
    assert len(boxes(p)) == 5


# -------------------------------------------------------------- escaping ----

def _no_bare(body, ch):
    return ch not in body.replace("\\" + ch, "")


def test_msg_escapes_comma():
    p = PdPatch()
    p.msg("1 3, 0 210 3")           # vline~ attack/decay
    body = boxes(p)[0]
    assert "\\," in body and _no_bare(body, ",")


def test_msg_escapes_semicolon():
    p = PdPatch()
    p.msg("; pd dsp 1")             # send to a named receiver
    body = boxes(p)[0]
    assert "\\;" in body and _no_bare(body, ";")


def test_comment_escapes_comma():
    """REGRESSION: an unescaped comma split the comment and Pd evaluated the
    tail -> 'error: canvas: no method for play'."""
    p = PdPatch()
    p.comment("engine (edit here, play from acid_set.pd)", 0, 0)
    body = boxes(p)[0]
    assert "\\," in body and _no_bare(body, ",")


def test_comment_escapes_semicolon():
    """REGRESSION: a ';' in a comment sent the tail to a receiver
    -> 'error: bang: no such object'."""
    p = PdPatch()
    p.comment("generative melody; bang to re-roll", 0, 0)
    body = boxes(p)[0]
    assert "\\;" in body and _no_bare(body, ";")


def test_object_text_is_emitted_verbatim():
    """expr~/expr variables must survive — $ followed by a letter is not a
    dollar-arg and must not be mangled."""
    p = PdPatch()
    p.obj("expr~ tanh($v1)")
    p.obj("expr 15000/$f1")
    assert "expr~ tanh($v1)" in boxes(p)[0]
    assert "expr 15000/$f1" in boxes(p)[1]


# ------------------------------------------- dollars and idempotence (0.8.0) ----

def test_obj_escapes_creation_arg_dollars():
    """REGRESSION (lila_rig): an unescaped $2 in an abstraction file is evaluated
    at load against nothing -> 0 + 'argument number out of range'; every arg of
    the Karplus-Strong string came out as 0 and the oud was silent."""
    p = PdPatch()
    p.obj("lop~ $2")
    p.obj("delwrite~ $0-ks 300")
    assert boxes(p)[0].endswith("lop~ \\$2")
    assert boxes(p)[1].endswith("delwrite~ \\$0-ks 300")


def test_obj_escaping_is_idempotent():
    """Text a script already escaped (the way lila_rig's did) is left alone."""
    p = PdPatch()
    p.obj("lop~ \\$2")
    p.obj("expr min(max($f1*4\\, 600)\\, \\$4)")
    assert boxes(p)[0].endswith("lop~ \\$2")            # not \\\\$2
    assert boxes(p)[1].endswith("expr min(max($f1*4\\, 600)\\, \\$4)")


def test_obj_escapes_a_bare_comma_in_expr():
    """`expr if(a, b, c)` -- the comma would otherwise split the object's args."""
    p = PdPatch()
    p.obj("expr if($f1>0, $f1, $f2)")
    body = boxes(p)[0]
    assert body.endswith("expr if($f1>0\\, $f1\\, $f2)")
    assert _no_bare(body, ",")


def test_msg_escapes_dollars_and_is_idempotent():
    """REGRESSION (choral_break): a pre-escaped `\\,` was escaped again into
    `\\ \\,` and garbled the granulator's setup message."""
    p = PdPatch()
    p.msg("1 $1 $2 0")
    p.msg("1 \\$1 \\$2 0")
    p.msg("a\\, b")
    assert boxes(p)[0].endswith("1 \\$1 \\$2 0")
    assert boxes(p)[1].endswith("1 \\$1 \\$2 0")
    assert boxes(p)[2].endswith("a\\, b")


def test_comment_escapes_dollars():
    p = PdPatch()
    p.comment("arg $1 is the clone number", 0, 0)
    assert boxes(p)[0].endswith("arg \\$1 is the clone number")


def test_legacy_spacing_is_preserved_for_bare_separators():
    """Byte-stability: the frozen emitter always wrote ` \\,` / ` \\;` with a
    leading space; the committed instruments depend on exactly that."""
    p = PdPatch()
    p.msg("1 3, 0 210 3")
    p.msg("; pd dsp 1")
    p.comment("a, b; c", 0, 0)
    assert boxes(p)[0].endswith("1 3 \\, 0 210 3")
    assert boxes(p)[1].endswith(" \\; pd dsp 1")
    assert boxes(p)[2].endswith("a \\, b \\; c")


# ------------------------------------------------------------- structure ----

def test_render_header_and_terminators():
    p = PdPatch()
    p.obj("osc~ 440")
    out = p.render()
    assert out.splitlines()[0].startswith("#N canvas")
    assert all(line.endswith(";") for line in out.splitlines())
    assert out.endswith("\n")


def test_connect_format():
    p = PdPatch()
    a, b = p.obj("f"), p.obj("+ 1")
    p.connect(a, 0, b, 1)
    assert connects(p) == ["#X connect 0 0 1 1"]


def test_chain_wires_outlet0_to_inlet0():
    p = PdPatch()
    ids = [p.obj("phasor~"), p.obj("-~ 0.5"), p.obj("dac~")]
    p.chain(*ids)
    assert connects(p) == ["#X connect 0 0 1 0", "#X connect 1 0 2 0"]


# ---------------------------------------------------------------- layout ----

def test_auto_layout_does_not_stack_boxes():
    p = PdPatch()
    for _ in range(6):
        p.obj("f")
    coords = [tuple(b.split()[2:4]) for b in boxes(p)]
    assert len(set(coords)) == len(coords)


def test_cursor_moves_the_auto_layout():
    p = PdPatch()
    p.cursor(500, 600)
    p.obj("f")
    assert boxes(p)[0].split()[2:4] == ["500", "600"]


# ------------------------------------------------------------------ init ----

def test_init_wires_loadbang_through_a_message_to_the_target():
    p = PdPatch()
    tgl = p.obj("tgl 22 0")
    m = p.init(tgl, 0)
    lb = p.loadbang()
    assert f"#X connect {lb} 0 {m} 0" in connects(p)
    assert f"#X connect {m} 0 {tgl} 0" in connects(p)
    assert boxes(p)[lb].endswith("loadbang")


def test_init_reuses_a_single_loadbang():
    p = PdPatch()
    a, b, c = p.obj("tgl"), p.obj("hsl"), p.obj("hradio")
    p.init(a, 0)
    p.init(b, 0.65)
    p.init(c, 0)
    assert sum(1 for box in boxes(p) if box.endswith("loadbang")) == 1


def test_init_all_reaches_every_target():
    p = PdPatch()
    a, b = p.obj("tgl"), p.obj("hradio")
    p.init_all({a: 0, b: 2})
    targets = {c.split()[4] for c in connects(p)}
    assert str(a) in targets and str(b) in targets


# ----------------------------------------------------- integration (Pd) ----

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


def _gui_gain_patch(with_init: bool) -> PdPatch:
    """osc~ through a gain driven by a toggle (gain = 1 - toggle).

    This is the exact shape that silently killed the acid_set rig: the toggle
    emits nothing at load, so line~ sits at 0 and multiplies the signal away.
    """
    p = PdPatch()
    osc = p.obj("osc~ 440")
    tgl = p.obj("tgl 22 0 empty empty empty 0 -8 0 10 #fcfcfc #000000 #000000 0 1")
    inv1, inv2 = p.obj("* -1"), p.obj("+ 1")
    pk, ln = p.obj("pack f 20"), p.obj("line~")
    p.connect(tgl, 0, inv1, 0)
    p.chain(inv1, inv2, pk, ln)
    gain = p.obj("*~")
    p.connect(osc, 0, gain, 0)
    p.connect(ln, 0, gain, 1)
    dac = p.obj("dac~")
    p.connect(gain, 0, dac, 0)
    p.connect(gain, 0, dac, 1)
    if with_init:
        p.init(tgl, 0)
    return p


@pytest.mark.skipif(not _can_render(), reason="needs pdverify + a Pd install")
def test_uninitialized_gui_control_really_is_silent(tmp_path):
    from pdverify import analyze
    from pdverify.render import RenderSpec, render

    path = tmp_path / "no_init.pd"
    _gui_gain_patch(with_init=False).save(path)
    assert analyze(render(str(path), RenderSpec(duration=0.5)).audio).is_silent


@pytest.mark.skipif(not _can_render(), reason="needs pdverify + a Pd install")
def test_init_makes_the_same_patch_sound(tmp_path):
    from pdverify import analyze
    from pdverify.render import RenderSpec, render

    path = tmp_path / "with_init.pd"
    _gui_gain_patch(with_init=True).save(path)
    report = analyze(render(str(path), RenderSpec(duration=0.5)).audio)
    assert not report.is_silent
    assert report.note == "A4"


@pytest.mark.skipif(not _can_render(), reason="needs pdverify + a Pd install")
def test_creation_arg_written_with_obj_reaches_the_abstraction(tmp_path):
    """The lila_rig experiment as a test: [argtone 0 440] -> [f $2] must output
    440, not 0. With the dollar written bare in the file it is evaluated at
    load (console: 'argument number out of range') and the tone is DC."""
    from pdverify import analyze
    from pdverify.render import RenderSpec, render

    a = PdPatch()
    inl, f, osc, out = a.obj("inlet"), a.obj("f $2"), a.obj("osc~"), a.obj("outlet~")
    a.chain(inl, f, osc, out)
    a.save(tmp_path / "argtone.pd")
    p = PdPatch()
    lb, inst, g, dac = p.obj("loadbang"), p.obj("argtone 0 440"), p.obj("*~ 0.3"), p.obj("dac~")
    p.chain(lb, inst, g, dac)
    p.connect(g, 0, dac, 1)
    p.save(tmp_path / "parent.pd")
    res = render(str(tmp_path / "parent.pd"), RenderSpec(duration=0.6))
    assert "out of range" not in res.pd_console
    r = analyze(res.audio)
    assert r.dominant_hz == pytest.approx(440, abs=3), r.pretty()
