"""Tests for pdbuild.Patch — the idioms layer over py2pd.

Every idiom here exists to prevent a bug that actually shipped, so each one
is tested against the rendered `.pd` text rather than against the object
model. A helper that prevents a bug and is not itself verified has only
moved the bug.
"""

from __future__ import annotations

import pytest

from pdbuild import Patch, object_io


# --------------------------------------------------------------------------- #
# indices — the #1 failure mode of machine-written Pd
# --------------------------------------------------------------------------- #

def test_connect_indices_account_for_comments():
    """REGRESSION: comments are non-functional but still take an index."""
    p = Patch()
    p.comment("a note", 10, 10)
    a = p.obj("osc~ 440")
    b = p.obj("dac~")
    p.link(a, 0, b, 0)
    assert "#X connect 1 0 2 0;" in p.render()


def test_link_reads_source_outlet_sink_inlet():
    p = Patch()
    a = p.obj("osc~ 440")
    b = p.obj("dac~")
    p.link(a, 0, b, 1)
    assert "#X connect 0 0 1 1;" in p.render()


def test_chain_wires_outlet0_to_inlet0():
    p = Patch()
    a, b, c = p.obj("noise~"), p.obj("lop~ 500"), p.obj("dac~")
    p.chain(a, b, c)
    text = p.render()
    assert "#X connect 0 0 1 0;" in text
    assert "#X connect 1 0 2 0;" in text


# --------------------------------------------------------------------------- #
# escaping — an unescaped ',' or ';' ends the record
# --------------------------------------------------------------------------- #

def test_comment_escapes_separators():
    """REGRESSION: an unescaped comma split a comment and Pd sent the tail as
    a message ('canvas: no method for ...')."""
    p = Patch()
    p.comment("press play, then go; fast", 10, 10)
    line = p.render()
    assert r"\," in line and r"\;" in line


def test_message_keeps_vline_comma_syntax():
    p = Patch()
    p.msg("1 3, 0 210 3")
    assert r"\," in p.render()


def test_expr_dollar_args_survive():
    """$v1/$f1 are expr variables, not message dollar-args — they must not be
    escaped away."""
    p = Patch()
    p.obj("expr~ tanh($v1)")
    p.obj("expr 15000/$f1")
    text = p.render()
    assert "tanh($v1)" in text
    assert "15000/$f1" in text


# --------------------------------------------------------------------------- #
# init() — GUI controls emit nothing at load
# --------------------------------------------------------------------------- #

def test_init_wires_loadbang_through_a_value():
    p = Patch()
    tgl = p.pd.add_toggle(x_pos=10, y_pos=10)
    p.init(tgl, 1)
    text = p.render()
    assert "#X obj" in text and "loadbang" in text
    assert "#X msg" in text
    # loadbang -> msg, and msg -> the toggle
    assert text.count("#X connect") >= 2


def test_init_all_shares_one_loadbang():
    p = Patch()
    a = p.pd.add_toggle(x_pos=10, y_pos=10)
    b = p.pd.add_toggle(x_pos=40, y_pos=10)
    p.init_all({a: 1, b: 0})
    assert p.render().count("loadbang") == 1


def test_loadbang_is_created_once():
    p = Patch()
    assert p.loadbang() is p.loadbang()


# --------------------------------------------------------------------------- #
# floatatom — send and receive are distinct slots
# --------------------------------------------------------------------------- #

def test_floatatom_send_lands_in_the_send_slot():
    """REGRESSION: both the legacy emitter and py2pd put `send` in Pd's
    `receive` position, yielding a number box that looked wired and was inert.

    Field order: x y width lower upper label_pos label receive send
    """
    p = Patch()
    p.floatatom(100, 82, send="bpmset", width=5)
    # ten fields incl. Pd's trailing font flag: ... label receive send font
    assert "#X floatatom 100 82 5 0 0 0 - - bpmset 0;" in p.render()


def test_floatatom_receive_is_separate_from_send():
    p = Patch()
    p.floatatom(0, 0, receive="shown", send="typed")
    assert " - shown typed 0;" in p.render()


# --------------------------------------------------------------------------- #
# I/O declarations — validation only fires on objects with known arity
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,expected", [
    ("bob~", (3, 1)),
    ("rev3~ 0 72 2200 55", (2, 4)),
    ("else/pad 100 100", (1, 2)),
    ("mtof", (1, 1)),
    ("pack f f", (2, 1)),
    ("pack f 18", (2, 1)),
    ("expr~ tanh($v1)", (1, 1)),
    ("expr 15000/$f1", (1, 1)),
    ("expr $f1 + $f2", (2, 1)),
    ("osc~ 440", None),          # py2pd knows this one; defer to it
])
def test_object_io_arity(text, expected):
    assert object_io(text) == expected


def test_declared_io_makes_validation_fire():
    """Without a declared count, py2pd leaves num_inlets None and a bogus
    inlet index sails through."""
    from py2pd import PdConnectionError
    p = Patch()
    src = p.obj("osc~ 440")
    filt = p.obj("bob~")          # 3 inlets, declared by us
    with pytest.raises(PdConnectionError):
        p.link(src, 0, filt, 7)


def test_unvalidated_reports_unknown_objects():
    p = Patch()
    p.obj("osc~ 440")
    p.obj("some/unknown~ 1 2")
    assert "some/unknown~ 1 2" in p.unvalidated()


def test_known_objects_are_not_reported_unvalidated():
    p = Patch()
    p.obj("bob~")
    p.obj("mtof")
    assert p.unvalidated() == []


# --------------------------------------------------------------------------- #
# canvas + layout
# --------------------------------------------------------------------------- #

def test_canvas_geometry_is_emitted():
    p = Patch(760, 560, 12, x=20, y=20)
    assert p.render().startswith("#N canvas 20 20 760 560 12;")


def test_cursor_places_guts_clear_of_a_gui_zone():
    p = Patch()
    p.cursor(560, 120)
    node = p.obj("osc~ 440")
    assert node.parameters["x_pos"] == 560
    assert node.parameters["y_pos"] == 120


def test_cursor_wraps_to_a_new_column():
    p = Patch(origin=(20, 40), step=26, bottom=100, column=150)
    xs = [p.obj(f"f {i}").parameters["x_pos"] for i in range(6)]
    assert xs[0] == 20 and max(xs) == 170


def test_explicit_position_overrides_the_cursor():
    p = Patch()
    node = p.obj("dac~", 400, 700)
    assert (node.parameters["x_pos"], node.parameters["y_pos"]) == (400, 700)


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def test_save_writes_a_loadable_patch(tmp_path):
    p = Patch()
    a = p.obj("osc~ 440")
    d = p.obj("dac~")
    p.link(a, 0, d, 0)
    p.link(a, 0, d, 1)
    out = p.save(tmp_path / "nested" / "t.pd")
    text = out.read_text(encoding="utf-8")
    assert text.startswith("#N canvas")
    assert "#X obj" in text and "#X connect" in text


def test_abstraction_instance():
    p = Patch()
    a = p.abstraction("acid303", inlets=0, outlets=2)
    assert a.num_outlets == 2
    assert "acid303" in p.render()
