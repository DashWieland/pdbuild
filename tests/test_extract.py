"""Tests for subgraph selection.

The behaviour that matters is which nodes a cut *does not* take. A selection
that over-reaches drags half the patch into the abstraction; one that
under-reaches silently leaves the engine behind.
"""

from __future__ import annotations

import re

import pytest

from pdbuild import Patch
from pdbuild.extract import (
    between,
    crossing_edges,
    downstream,
    extraction_plan,
    is_signal_outlet,
    node_text,
    resolve,
    upstream,
)
from pdbuild.extract import _resource_uses


@pytest.fixture
def chain():
    """osc~ -> gain -> filter -> dac~, plus a detached branch off osc~.

    The branch is the interesting part: it is downstream of a source but
    never reaches the sink.
    """
    p = Patch()
    p.osc = p.obj("osc~ 440")
    p.gain = p.obj("*~ 0.5")
    p.filt = p.obj("lop~ 800")
    p.dac = p.obj("dac~")
    p.branch = p.obj("hip~ 2000")      # hangs off osc~, goes nowhere
    p.chain(p.osc, p.gain, p.filt)
    p.link(p.filt, 0, p.dac, 0)
    p.link(p.osc, 0, p.branch, 0)
    return p


def texts(nodes):
    return [node_text(n) for n in nodes]


# --------------------------------------------------------------------------- #
# between
# --------------------------------------------------------------------------- #

def test_between_selects_the_path(chain):
    got = texts(between(chain, chain.osc, chain.dac))
    assert got == ["osc~ 440", "*~ 0.5", "lop~ 800", "dac~"]


def test_between_excludes_branches_that_never_reach_the_sink(chain):
    """The whole point of an intersection rather than plain reachability."""
    assert "hip~ 2000" not in texts(between(chain, chain.osc, chain.dac))


def test_between_can_exclude_endpoints(chain):
    got = texts(between(chain, chain.osc, chain.dac,
                        include_sources=False, include_sinks=False))
    assert got == ["*~ 0.5", "lop~ 800"]


def test_between_accepts_box_text(chain):
    assert texts(between(chain, "osc~ 440", "dac~"))[0] == "osc~ 440"


def test_between_accepts_a_regex(chain):
    got = texts(between(chain, re.compile(r"^osc~"), re.compile(r"^dac~")))
    assert got[0] == "osc~ 440" and got[-1] == "dac~"


def test_between_accepts_multiple_sources(chain):
    p = chain
    other = p.obj("noise~")
    p.link(other, 0, p.gain, 0)
    got = texts(between(p, [p.osc, other], p.dac))
    assert "noise~" in got and "osc~ 440" in got


def test_between_is_empty_when_no_path_exists(chain):
    assert between(chain, chain.branch, chain.dac) == []


def test_between_handles_a_diamond():
    """Both arms of a split-and-rejoin belong to the cut."""
    p = Patch()
    src = p.obj("osc~ 440")
    a = p.obj("*~ 0.3")
    b = p.obj("*~ 0.7")
    mix = p.obj("+~")
    out = p.obj("dac~")
    p.link(src, 0, a, 0); p.link(src, 0, b, 0)
    p.link(a, 0, mix, 0); p.link(b, 0, mix, 1)
    p.link(mix, 0, out, 0)
    got = texts(between(p, src, out))
    assert "*~ 0.3" in got and "*~ 0.7" in got and "+~" in got


def test_between_tolerates_a_feedback_loop():
    """Pd patches contain cycles (delay feedback); traversal must terminate."""
    p = Patch()
    a = p.obj("delread~ d 100")
    b = p.obj("*~ 0.5")
    c = p.obj("delwrite~ d 500")
    out = p.obj("dac~")
    p.chain(a, b, c)
    p.link(b, 0, a, 0)          # the loop
    p.link(b, 0, out, 0)
    got = texts(between(p, a, out))
    assert "delread~ d 100" in got and "*~ 0.5" in got


# --------------------------------------------------------------------------- #
# resolve
# --------------------------------------------------------------------------- #

def test_resolve_rejects_a_selector_matching_nothing(chain):
    """A silent empty selection would produce a silently empty extraction."""
    with pytest.raises(ValueError, match="no node with text"):
        resolve(chain, "osc~ 999")


def test_resolve_suggests_near_misses(chain):
    with pytest.raises(ValueError, match="Did you mean"):
        resolve(chain, "osc~ 441")


def test_resolve_finds_every_duplicate(chain):
    p = chain
    p.obj("*~ 0.5")             # a second, identical box
    assert len(resolve(p, "*~ 0.5")) == 2


def test_resolve_by_identity_distinguishes_equal_boxes():
    p = Patch()
    first = p.obj("+ 1")
    second = p.obj("+ 1")
    assert resolve(p, first) != resolve(p, second)


# --------------------------------------------------------------------------- #
# directional helpers
# --------------------------------------------------------------------------- #

def test_downstream_includes_dead_end_branches(chain):
    assert "hip~ 2000" in texts(downstream(chain, chain.osc))


def test_upstream_walks_backwards(chain):
    got = texts(upstream(chain, chain.dac))
    assert "osc~ 440" in got and "hip~ 2000" not in got


# --------------------------------------------------------------------------- #
# boundary classification -- this becomes the abstraction's interface
# --------------------------------------------------------------------------- #

def test_crossing_edges_classifies_the_boundary(chain):
    cut = between(chain, chain.gain, chain.filt)
    edges = crossing_edges(chain, cut)

    assert [node_text(e[0]) for e in edges["inbound"]] == ["osc~ 440"]
    assert [node_text(e[2]) for e in edges["outbound"]] == ["dac~"]
    assert [(node_text(e[0]), node_text(e[2])) for e in edges["internal"]] == [
        ("*~ 0.5", "lop~ 800")
    ]
    # the osc~ -> hip~ branch touches neither side of the cut
    assert [(node_text(e[0]), node_text(e[2])) for e in edges["external"]] == [
        ("osc~ 440", "hip~ 2000")
    ]


def test_ports_get_the_intended_x_not_an_auto_placed_one(tmp_path):
    """Port index is assigned by x-position, so a port must land at exactly the
    x extract() chose. py2pd auto-places any box given a negative coordinate,
    which would silently scramble a multi-port interface -- guard against it."""
    from pdbuild.extract import extract, node_text

    p = Patch()
    a = p.obj("osc~ 440")
    b = p.obj("*~ 0.5")
    c = p.obj("lop~ 800")
    d = p.obj("dac~")
    p.chain(a, b, c)
    p.link(c, 0, d, 0)
    extract(p, [b, c], "fx", str(tmp_path))

    from py2pd import parse_file
    ast = parse_file(str(tmp_path / "fx.pd"))
    ports = {}
    for el in ast.elements:
        txt = getattr(el, "text", "")
        if txt in ("inlet~", "outlet~", "inlet", "outlet"):
            ports.setdefault(txt, []).append(el.position.x)
    # every port at a non-negative x on the intended 40 + 160*n grid
    for xs in ports.values():
        for x in xs:
            assert x >= 0 and (x - 40) % 160 == 0


def test_multiport_extraction_orders_inlets_by_x(tmp_path):
    """Two signal inlets must be laid out left-to-right in a stable order, at
    distinct increasing x -- the wiring contract for which is inlet 0 vs 1."""
    from pdbuild.extract import extract

    p = Patch()
    a = p.obj("osc~ 440")
    b = p.obj("osc~ 330")
    mix = p.obj("*~")           # two signal inlets, both fed from outside
    out = p.obj("dac~")
    p.link(a, 0, mix, 0)
    p.link(b, 0, mix, 1)
    p.link(mix, 0, out, 0)
    extract(p, [mix], "mixer", str(tmp_path))

    from py2pd import parse_file
    ast = parse_file(str(tmp_path / "mixer.pd"))
    inlet_xs = sorted(el.position.x for el in ast.elements
                      if getattr(el, "text", "") == "inlet~")
    assert len(inlet_xs) == 2
    assert inlet_xs[0] < inlet_xs[1]        # distinct, increasing
    assert len(set(inlet_xs)) == 2          # never share an x (would tie)


def test_crossing_edges_records_outlet_and_inlet_numbers():
    p = Patch()
    a = p.obj("osc~ 440")
    b = p.obj("*~")
    out = p.obj("dac~")
    p.link(a, 0, b, 1)          # into the RIGHT inlet
    p.link(b, 0, out, 0)
    edges = crossing_edges(p, [b])
    assert edges["inbound"][0][1] == 0     # outlet
    assert edges["inbound"][0][3] == 1     # inlet


# --------------------------------------------------------------------------- #
# M2: signal/control outlet typing
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,outlet,expected", [
    ("osc~ 440", 0, True),
    ("*~ 0.5", 0, True),
    ("lop~ 800", 0, True),
    ("dac~", 0, False),           # zero-outlet
    ("delwrite~ d 100", 0, False),
    ("snapshot~", 0, False),      # ~ but control outlet
    ("env~", 0, False),
    ("bang~", 0, False),
    ("metro 125", 0, False),      # no ~
    ("r foo", 0, False),
    ("tabplay~ arr", 0, True),    # mixed: out 0 signal
    ("tabplay~ arr", 1, False),   # mixed: out 1 control (done bang)
    ("readsf~ 2", 0, True),       # 2ch: outs 0,1 signal
    ("readsf~ 2", 1, True),
    ("readsf~ 2", 2, False),      # trailing control bang
])
def test_is_signal_outlet(text, outlet, expected):
    assert is_signal_outlet(text, outlet) is expected


# --------------------------------------------------------------------------- #
# M2: named-resource parsing
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,expected", [
    ("delwrite~ echo 200", [(1, "echo", "delay", "w")]),
    ("delread~ echo 125", [(1, "echo", "delay", "r")]),
    ("vd~ echo", [(1, "echo", "delay", "r")]),
    ("s drive", [(1, "drive", "send", "w")]),
    ("r drive", [(1, "drive", "send", "r")]),
    ("send xpad", [(1, "xpad", "send", "w")]),
    ("throw~ bus", [(1, "bus", "sigbus", "w")]),
    ("catch~ bus", [(1, "bus", "sigbus", "r")]),
    ("value gain", [(1, "gain", "value", "rw")]),
    ("osc~ 440", []),             # not a resource object
    ("s", []),                    # no name (name comes from inlet)
])
def test_resource_uses(text, expected):
    assert _resource_uses(text) == expected


# --------------------------------------------------------------------------- #
# M2: between() follows signal buffers
# --------------------------------------------------------------------------- #

def test_between_follows_a_delay_line_to_its_read_tap():
    """delread~ has no explicit input; without buffer-following it falls
    outside the path and the delay is split across the cut."""
    p = Patch()
    src = p.obj("osc~ 110")
    dw = p.obj("delwrite~ echo 200")
    dr = p.obj("delread~ echo 125")
    out = p.obj("dac~")
    p.link(src, 0, dw, 0)         # write side
    p.link(dr, 0, out, 0)         # read side, no explicit input
    got = [node_text(n) for n in between(p, src, out)]
    assert "delread~ echo 125" in got

    without = [node_text(n) for n in between(p, src, out, follow_buffers=False)]
    assert "delread~ echo 125" not in without


# --------------------------------------------------------------------------- #
# M2: extraction plan -- namespacing and warnings
# --------------------------------------------------------------------------- #

def _engine_with_delay():
    p = Patch()
    src = p.obj("osc~ 110")
    g = p.obj("*~ 0.8")
    dw = p.obj("delwrite~ echo 200")
    dr = p.obj("delread~ echo 125")
    mix = p.obj("+~")
    out = p.obj("dac~")
    p.link(src, 0, g, 0)
    p.link(g, 0, dw, 0); p.link(g, 0, mix, 0)
    p.link(dr, 0, mix, 1); p.link(mix, 0, out, 0)
    return p, g, mix


def test_plan_namespaces_a_fully_internal_delay():
    p, g, mix = _engine_with_delay()
    plan = extraction_plan(p, between(p, g, mix))
    assert plan["namespace"] == {"echo": "delay"}
    assert plan["shared"] == {}
    assert plan["warnings"] == []


def test_plan_keeps_a_boundary_receive_global():
    """A receive read inside but sent from outside stays global -- the rig's
    zero-inlet, globally-driven engine pattern."""
    p = Patch()
    src = p.obj("osc~ 110")
    r = p.obj("r cutoff")
    filt = p.obj("lop~")
    out = p.obj("dac~")
    p.link(src, 0, filt, 0); p.link(r, 0, filt, 1); p.link(filt, 0, out, 0)
    # cutoff is sent from the parent
    lb = p.obj("loadbang"); m = p.msg("800"); s = p.obj("s cutoff")
    p.link(lb, 0, m, 0); p.link(m, 0, s, 0)

    plan = extraction_plan(p, [r, filt])
    assert "cutoff" in plan["shared"]
    assert "cutoff" not in plan["namespace"]


def test_plan_warns_on_a_delay_split_across_the_cut():
    """A delay allocated inside but named outside too cannot be namespaced, so
    two instances would collide -- surface it."""
    p, g, mix = _engine_with_delay()
    # a stray reader of the same delay OUTSIDE the region
    stray = p.obj("delread~ echo 300")
    dac2 = p.obj("dac~")
    p.link(stray, 0, dac2, 0)

    region = [n for n in p.pd.nodes
              if node_text(n) in ("*~ 0.8", "delwrite~ echo 200",
                                   "delread~ echo 125", "+~")]
    plan = extraction_plan(p, region)
    assert plan["warnings"], "expected a split-delay warning"
    assert "echo" in plan["warnings"][0]
    assert "echo" in plan["shared"]


def test_plan_respects_broadcast_names():
    p, g, mix = _engine_with_delay()
    plan = extraction_plan(p, between(p, g, mix), broadcast=["echo"])
    assert "echo" in plan["shared"]      # forced global
    assert "echo" not in plan["namespace"]


# --------------------------------------------------------------------------- #
# M2: never namespace a name we cannot fully rewrite
#
# Each of these would be a SILENT break: the rename succeeds, the patch loads,
# and the wire is simply gone.
# --------------------------------------------------------------------------- #

def test_message_box_send_keeps_a_name_global():
    """The parent drives the region only via `; drive 0.8`. Namespacing the
    region's `r drive` would cut it, and nothing would report an error."""
    p = Patch()
    g = p.obj("*~"); r = p.obj("r drive"); out = p.obj("dac~")
    p.link(r, 0, g, 1); p.link(g, 0, out, 0)
    lb = p.obj("loadbang"); m = p.msg("; drive 0.8")
    p.link(lb, 0, m, 0)

    plan = extraction_plan(p, [g, r])
    assert "drive" in plan["shared"]
    assert "drive" not in plan["namespace"]


def test_gui_send_symbol_keeps_a_name_global():
    p = Patch()
    g = p.obj("*~"); r = p.obj("r vol"); out = p.obj("dac~")
    p.link(r, 0, g, 1); p.link(g, 0, out, 0)
    p.pd.add_hslider(x_pos=10, y_pos=10, send="vol")

    plan = extraction_plan(p, [g, r])
    assert "vol" in plan["shared"] and "vol" not in plan["namespace"]


def test_array_backed_table_is_never_namespaced():
    """Renaming `tabread~ wave` without renaming the `#X array wave` would
    point it at a table that does not exist."""
    p = Patch()
    tr = p.obj("tabread~ wave"); out = p.obj("dac~")
    p.link(tr, 0, out, 0)
    arr = p.pd.add_array("wave", 512)

    plan = extraction_plan(p, [tr, arr])
    assert "wave" not in plan["namespace"]


def test_msg_send_targets_parsing():
    from pdbuild.extract import _msg_send_targets
    assert _msg_send_targets(r" \;  drive 0.8") == ["drive"]
    assert _msg_send_targets(r"\; pd dsp 1") == ["pd"]
    assert _msg_send_targets("1 3, 0 210 3") == []      # no send at all


# --------------------------------------------------------------------------- #
# Regressions from the adversarial pass. Every one of these was found by an
# agent trying to break extract(), and most were SILENT: a valid .pd file, a
# clean Pd console, and the wrong sound.
# --------------------------------------------------------------------------- #

def test_region_with_an_existing_port_box_is_refused(tmp_path):
    """CRITICAL/SILENT: Pd indexes ports by the x of every inlet/outlet box on
    the canvas. A box already in the region joins that sort and shifts the
    generated ports. Proven by moving one stray inlet~ from x=20 to x=500,
    which flipped similarity from 0.000 to 1.000 with an empty console."""
    from pdbuild.extract import extract
    p = Patch()
    stray = p.obj("inlet~", 20, 100)
    g = p.obj("*~ 0.5", 300, 140)
    out = p.obj("dac~", 300, 200)
    p.link(stray, 0, g, 0); p.link(g, 0, out, 0)
    with pytest.raises(ValueError, match="port boxes"):
        extract(p, [stray, g], "fx", str(tmp_path))


def test_extraction_is_independent_of_region_order(tmp_path):
    """CRITICAL/SILENT: the same node set listed in a different order produced
    a different-sounding patch, because copy order set connection order."""
    from pdbuild.extract import extract

    def build():
        q = Patch()
        a = q.obj("osc~ 220"); b = q.obj("*~ 0.5")
        c = q.obj("lop~ 900"); d = q.obj("dac~")
        q.chain(a, b, c); q.link(c, 0, d, 0)
        return q, b, c

    q1, b1, c1 = build()
    q2, b2, c2 = build()
    extract(q1, [b1, c1], "m", str(tmp_path / "a"))
    extract(q2, [c2, b2], "m", str(tmp_path / "b"))     # reversed
    assert (tmp_path / "a" / "m.pd").read_text() == (tmp_path / "b" / "m.pd").read_text()
    assert q1.render() == q2.render()


def test_a_node_listed_twice_is_only_copied_once(tmp_path):
    """A duplicate in the region list copied the node twice, orphaning one copy
    and re-allocating any name it owned."""
    from pdbuild.extract import extract
    p = Patch()
    a = p.obj("osc~ 110"); b = p.obj("*~ 0.5"); out = p.obj("dac~")
    p.link(a, 0, b, 0); p.link(b, 0, out, 0)
    extract(p, [b, b, b], "dup", str(tmp_path))
    assert (tmp_path / "dup.pd").read_text().count("*~ 0.5") == 1


def test_a_comment_is_never_promoted_to_an_object(tmp_path):
    """MAJOR/SILENT: a comment whose first word is a resource class was renamed
    AND rewritten from '#X text' to '#X obj', creating a real object."""
    from pdbuild.extract import extract
    p = Patch()
    src = p.obj("osc~ 110"); g = p.obj("*~ 0.5")
    dw = p.obj("delwrite~ echo 100"); dr = p.obj("delread~ echo 50")
    mx = p.obj("+~"); out = p.obj("dac~")
    cm = p.comment("s drive is the control", 20, 300)
    p.link(src, 0, g, 0); p.link(g, 0, dw, 0); p.link(g, 0, mx, 0)
    p.link(dr, 0, mx, 1); p.link(mx, 0, out, 0)

    extract(p, [g, dw, dr, mx, cm], "cm", str(tmp_path))
    text = (tmp_path / "cm.pd").read_text(encoding="utf-8")
    assert "#X text 20 300 s drive is the control;" in text
    assert "#X obj 20 300 s drive" not in text


def test_subpatch_is_not_mistaken_for_an_array():
    """[pd name] carries a `name` parameter just like an array does; treating
    it as a table produced a bogus collision warning."""
    p = Patch()
    inner = Patch(); inner.obj("inlet~"); inner.obj("outlet~")
    sub = p.pd.add_subpatch("voice", inner.pd, x_pos=20, y_pos=100)
    o = p.obj("osc~ 110"); d = p.obj("dac~")
    p.link(o, 0, sub, 0); p.link(sub, 0, d, 0)
    plan = extraction_plan(p, [sub])
    assert not any("voice" in w for w in plan["warnings"])


def test_table_names_are_never_namespaced():
    """Renaming a tabread~ without renaming its backing array points it at a
    table that does not exist -- and the wavetable silently vanishes."""
    p = Patch()
    tr = p.obj("tabread4~ wave"); g = p.obj("*~ 0.5"); out = p.obj("dac~")
    p.link(tr, 0, g, 0); p.link(g, 0, out, 0)
    plan = extraction_plan(p, [tr, g])
    assert "wave" not in plan["namespace"]


def test_a_name_already_scoped_with_dollar_zero_is_left_alone():
    p = Patch()
    dw = p.obj("delwrite~ $0-echo 100"); dr = p.obj("delread~ $0-echo 50")
    g = p.obj("*~"); out = p.obj("dac~")
    p.link(g, 0, dw, 0); p.link(dr, 0, out, 0)
    plan = extraction_plan(p, [dw, dr, g])
    assert not any(k.endswith("echo") for k in plan["namespace"]), plan["namespace"]


@pytest.mark.parametrize("text,expected", [
    ("s~ bus", [(1, "bus", "sigbus", "w")]),
    ("r~ bus", [(1, "bus", "sigbus", "r")]),
    ("send~ bus", [(1, "bus", "sigbus", "w")]),
    ("receive~ bus", [(1, "bus", "sigbus", "r")]),
    ("table wave", [(1, "wave", "table", "w")]),
    ("array define wave", [(2, "wave", "table", "w")]),
    ("delwrite~ 3voices 100", [(1, "3voices", "delay", "w")]),  # digit-leading
    ("delread~ 100", []),                                        # a time, not a name
])
def test_resource_uses_extended(text, expected):
    assert _resource_uses(text) == expected


# --------------------------------------------------------------------------- #
# Composability: outlet typing for abstraction instances / clone / [pd sub]
#
# is_signal_outlet keys off the `~` suffix and so mis-types the signal outlet
# of an abstraction instance as control -- which is what stopped extract() from
# running on its own output. resolve_signal_outlet reads the real port objects.
# --------------------------------------------------------------------------- #

def _write_abs(tmp_path, name, ports):
    """Write <name>.pd with the given port objects at increasing x.
    `ports` is a list of ('inlet~'|'inlet'|'outlet~'|'outlet')."""
    a = Patch(400, 300, 10)
    a.pd.nodes = []
    a.pd.connections = []
    ins = [p for p in ports if "inlet" in p]
    outs = [p for p in ports if "outlet" in p]
    for i, p in enumerate(ins):
        a.obj(p, 40 + 160 * i, 20)
    for i, p in enumerate(outs):
        a.obj(p, 40 + 160 * i, 240)
    a.pd.canvas = (20, 20, 400, 300, 10)
    a.save(tmp_path / f"{name}.pd")


def test_resolve_abstraction_instance_outlet(tmp_path):
    from pdbuild.extract import resolve_signal_outlet
    _write_abs(tmp_path, "eng", ["inlet~", "outlet~", "outlet"])  # sig out 0, ctrl out 1
    p = Patch()
    inst = p.pd.add("eng", x_pos=10, y_pos=10, num_inlets=1, num_outlets=2)
    assert resolve_signal_outlet(inst, 0, [str(tmp_path)]) is True
    assert resolve_signal_outlet(inst, 1, [str(tmp_path)]) is False


def test_resolve_clone_outlet(tmp_path):
    from pdbuild.extract import resolve_signal_outlet
    _write_abs(tmp_path, "eng", ["outlet~", "outlet"])
    p = Patch()
    for text in ("clone eng 4", "clone -s 1 eng 4"):
        cl = p.obj(text)
        assert resolve_signal_outlet(cl, 0, [str(tmp_path)]) is True
        assert resolve_signal_outlet(cl, 1, [str(tmp_path)]) is False


def test_resolve_subpatch_outlet():
    from pdbuild.extract import resolve_signal_outlet
    host = Patch()
    inner = Patch(); inner.pd.nodes = []; inner.pd.connections = []
    inner.obj("outlet~", 40, 20); inner.obj("outlet", 200, 20)
    sub = host.pd.add_subpatch("s", inner.pd, x_pos=10, y_pos=10)
    assert resolve_signal_outlet(sub, 0, []) is True
    assert resolve_signal_outlet(sub, 1, []) is False


def test_resolve_falls_back_for_vanilla_objects(tmp_path):
    from pdbuild.extract import resolve_signal_outlet
    p = Patch()
    assert resolve_signal_outlet(p.obj("osc~ 440"), 0, [str(tmp_path)]) is True
    assert resolve_signal_outlet(p.obj("metro 100"), 0, [str(tmp_path)]) is False
    # an abstraction whose file is NOT on the path: stay conservative (control)
    assert resolve_signal_outlet(p.obj("unknownabs"), 0, [str(tmp_path)]) is False


def test_resolve_ports_ordered_by_x(tmp_path):
    """A file that lists outlet~ AFTER outlet but at a smaller x: index follows
    x, not file order (the same rule Pd uses)."""
    from pdbuild.extract import resolve_signal_outlet
    a = Patch(400, 300, 10); a.pd.nodes = []; a.pd.connections = []
    a.obj("outlet", 200, 240)      # file first, but x=200 -> index 1
    a.obj("outlet~", 40, 240)      # file second, but x=40  -> index 0
    a.pd.canvas = (20, 20, 400, 300, 10)
    a.save(tmp_path / "x.pd")
    p = Patch(); inst = p.obj("x")
    assert resolve_signal_outlet(inst, 0, [str(tmp_path)]) is True   # the x=40 outlet~
    assert resolve_signal_outlet(inst, 1, [str(tmp_path)]) is False  # the x=200 outlet


# --------------------------------------------------------------------------- #
# Table uses at control rate, and the [array] verbs (0.9.1)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,expected", [
    ("tabread steps", [(1, "steps", "table", "r")]),
    ("tabread4 steps", [(1, "steps", "table", "r")]),
    ("tabwrite steps", [(1, "steps", "table", "w")]),
    ("tabosc4~ wave", [(1, "wave", "table", "r")]),
    ("array get wave", [(2, "wave", "table", "r")]),
    ("array set wave", [(2, "wave", "table", "w")]),
    ("array size wave", [(2, "wave", "table", "rw")]),
    ("array sum wave", [(2, "wave", "table", "r")]),
    ("array define wave 64", [(2, "wave", "table", "w")]),
])
def test_table_uses_at_control_rate_and_the_array_verbs(text, expected):
    """A step sequencer's [tabread steps] uses the table as surely as a
    wavetable's [tabread4~ wave]; extract only saw the signal-rate ones."""
    assert _resource_uses(text) == expected


def test_a_control_rate_reader_is_seen_crossing_the_cut():
    """The region reads a table defined outside it. Before 0.9.1 the plan did
    not list the table at all, as if the extracted part used nothing."""
    p = Patch()
    tb = p.obj("table steps 16")
    m = p.obj("metro 250"); tr = p.obj("tabread steps"); pr = p.obj("print step")
    p.link(m, 0, tr, 0); p.link(tr, 0, pr, 0)
    plan = extraction_plan(p, [m, tr, pr])
    assert plan["shared"].get("steps") == "table"
    assert "steps" not in plan["namespace"]
    assert tb is not None


def test_array_get_is_not_a_second_definition():
    """[array get wave] reads a table someone else defined. Counting every
    [array ...] as an allocator warned that two instances would 'collide' on
    a table they only read."""
    p = Patch()
    p.obj("table wave 64")
    rd = p.obj("tabread~ wave"); out = p.obj("dac~")
    p.link(rd, 0, out, 0)
    b = p.obj("bang"); ag = p.obj("array get wave"); pr = p.obj("print wave")
    p.link(b, 0, ag, 0); p.link(ag, 0, pr, 0)
    plan = extraction_plan(p, [b, ag, pr])
    assert not any("wave" in w for w in plan["warnings"]), plan["warnings"]
    assert plan["shared"].get("wave") == "table"


def test_array_define_inside_the_cut_still_warns():
    """The allocator itself inside, a reader outside: that one does collide."""
    p = Patch()
    ad = p.obj("array define wave 64")
    rd = p.obj("tabread~ wave"); out = p.obj("dac~")
    p.link(rd, 0, out, 0)
    plan = extraction_plan(p, [ad])
    assert any("wave" in w for w in plan["warnings"]), plan["warnings"]
