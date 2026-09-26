"""Patch.graph -- a graph-on-parent array, the table a player can see.

Text tests pin the four records and the index; Pd tests prove that Pd loads
them, that what is written into the graph can be read back, that Pd re-saves
the records unchanged, that the `edit 0` lock is a message Pd accepts, and
that an instance-local `$0-` graph stays local to its abstraction.
"""

from __future__ import annotations

import re

import pytest

from pdbuild import Graph, Patch
from pdbuild.extract import extraction_plan
from pdbuild.preview import boxes


def _graph_lines(text: str, name: str) -> list[str]:
    """The records of the graph holding ``name``, minus Pd's `#A` property lines."""
    lines = text.splitlines()
    at = next(i for i, l in enumerate(lines) if l.startswith(f"#X array {name} "))
    start = at - 1
    end = next(i for i in range(at, len(lines)) if lines[i].startswith("#X restore"))
    return [l for l in lines[start:end + 1] if not l.startswith("#A")]


# --------------------------------------------------------------------------- #
# the records
# --------------------------------------------------------------------------- #

def test_graph_emits_the_four_records():
    p = Patch()
    p.graph("tune", 24, 20, 300, 288, 264, 5.5, 16.5, editable=True)
    assert p.render().splitlines()[1:] == [
        "#N canvas 0 50 450 250 (subpatch) 0;",
        "#X array tune 24 float 10;",                   # points (2*1) + name hidden (8)
        "#X coords 0 16.5 24 5.5 288 264 1 0 0;",       # x 0..size, y top = yhi, bottom = ylo
        "#X restore 20 300 graph;",
    ]


@pytest.mark.parametrize("style,hide,flags,x_to", [
    ("points", True, 10, 24),
    ("points", False, 2, 24),
    ("polygon", False, 0, 23),       # a polygon's last vertex sits on the right edge
    ("polygon", True, 8, 23),
    ("bezier", False, 4, 23),
])
def test_graph_flags_and_x_range_follow_pd(style, hide, flags, x_to):
    g = Graph("t", 24, 0, 0, 100, 50, -1, 1, style=style, hide_name=hide)
    assert (g.flags, g.x_range) == (flags, x_to)
    assert f"#X array t 24 float {flags};" in str(g)
    assert f"#X coords 0 1 {x_to} -1 100 50 1 0 0;" in str(g)


def test_graph_takes_one_index_and_has_no_ports():
    """It is one box on the parent: the boxes after it are numbered past it,
    and it has nothing to connect to."""
    from py2pd import PdConnectionError
    p = Patch()
    p.comment("the tune", 20, 6)
    g = p.graph("tune", 8, 20, 30, 200, 100, 0, 10, editable=True)
    a, b = p.obj("osc~ 440"), p.obj("dac~")
    p.link(a, 0, b, 0)
    assert "#X connect 2 0 3 0;" in p.render()        # comment 0, graph 1, osc~ 2, dac~ 3
    with pytest.raises(PdConnectionError):
        p.link(a, 0, g, 0)
    with pytest.raises(PdConnectionError):
        p.link(g, 0, b, 0)
    assert p.unvalidated() == []


def test_graph_is_locked_from_the_loadbang_unless_editable():
    p = Patch()
    p.graph("tune", 8, 20, 30, 200, 100, 0, 10)
    idx = {b.text: b.index for b in boxes(p)}
    lock = idx["\\; tune edit 0"]
    assert lock == 1 + idx["graph tune"]                           # the lock message, right after
    assert f"#X connect {idx['loadbang']} 0 {lock} 0;" in p.render()
    free = Patch()
    free.graph("tune", 8, 20, 30, 200, 100, 0, 10, editable=True)
    assert "edit" not in free.render() and "loadbang" not in free.render()


def test_instance_local_graph_escapes_its_dollar_and_locks_through_an_object():
    """A message box expands $0 to 0 (Pd 0.56.2), so `; $0-tune edit 0` would
    go to `0-tune`; the lock goes through [s $0-tune] instead."""
    p = Patch()
    p.graph("$0-tune", 8, 20, 30, 200, 100, 0, 10)
    text = p.render()
    assert "#X array \\$0-tune 8 float 10;" in text
    assert "#X msg" in text and " edit 0;" in text and "s \\$0-tune;" in text
    assert "; \\$0-tune" not in text.replace("\\;", ";")


@pytest.mark.parametrize("kw", [dict(style="dots"), dict(size=0), dict(ylo=1, yhi=1),
                                dict(name="my tune"), dict(name="a;b"), dict(name="")])
def test_graph_rejects_what_pd_cannot_draw(kw):
    args = dict(name="t", size=8, x=0, y=0, w=100, h=50, ylo=0, yhi=1)
    args.update(kw)
    with pytest.raises(ValueError):
        Patch().graph(**args)


def test_extract_sees_the_table_a_graph_allocates():
    """A graph allocates its table the way a bare `#X array` does. Cut into
    an abstraction with its reader, the name cannot be namespaced (the graph's
    record is not rewritten), so two instances would share one table, and
    extraction_plan now says so; before, the graph was invisible to it."""
    p = Patch()
    g = p.graph("tune", 8, 20, 30, 200, 100, 0, 10, editable=True)
    r = p.obj("tabread~ tune")
    gain = p.obj("*~ 0.5")
    p.link(r, 0, gain, 0)
    plan = extraction_plan(p, [g, r, gain])
    assert "tune" in plan["shared"] and "tune" not in plan["namespace"]
    assert any("'tune'" in w and "collide" in w for w in plan["warnings"]), plan["warnings"]


# --------------------------------------------------------------------------- #
# in Pd
# --------------------------------------------------------------------------- #

def test_pd_loads_the_graph_holds_what_is_written_and_resaves_it_unchanged(tmp_path, run_pd):
    """Write into the graph two ways (a `; name` message, [tabwrite]), read
    it back with [array get]; have Pd save the patch and compare its records
    with ours. A method arrays lack is sent too, as the control: Pd does
    complain about that one, so its silence about `edit` means `edit 0` took."""
    p = Patch(origin=(400, 20))
    p.comment("before the graphs", 20, 6)
    p.graph("gpts", 8, 20, 30, 200, 100, 0, 10)                                  # locked
    p.graph("gpoly", 8, 20, 150, 200, 100, -1, 1, style="polygon", hide_name=False, editable=True)
    t = p.obj("t b b b b")
    p.link(p.loadbang(), 0, t, 0)
    fill = p.msg("; gpts 0 1 2 3 4 5 6 7 8 ; gpts nosuchmethod 1")
    p.link(t, 3, fill, 0)
    tw = p.obj("t b b")
    p.link(t, 2, tw, 0)
    ix, val, wr = p.msg("5"), p.msg("9.5"), p.obj("tabwrite gpts")
    p.link(tw, 1, ix, 0); p.link(ix, 0, wr, 1)
    p.link(tw, 0, val, 0); p.link(val, 0, wr, 0)
    get, show = p.obj("array get gpts"), p.obj("print GET")
    p.link(t, 1, get, 0); p.link(get, 0, show, 0)
    save = p.msg(f"; pd-g.pd savetofile saved.pd {tmp_path.as_posix()}; pd quit")
    p.link(t, 0, save, 0)
    p.save(tmp_path / "g.pd")

    console = run_pd(tmp_path / "g.pd", cwd=tmp_path)
    assert "GET: 1 2 3 4 5 9.5 7 8" in console                     # both writes landed
    assert "connection failed" not in console                      # the graphs took one index each
    assert re.findall(r"no method for '(\w+)'", console) == ["nosuchmethod"], console
    saved = (tmp_path / "saved.pd").read_text(encoding="utf-8")
    for name in ("gpts", "gpoly"):
        assert _graph_lines(saved, name) == _graph_lines(p.render(), name)


def test_pd_keeps_an_instance_local_graph_to_its_instance(tmp_path, run_pd):
    """Two instances of an abstraction showing `$0-g`: each graph holds its own
    values, and the lock ([edit 0( -> [s $0-g]) reaches it without an error."""
    a = Patch(origin=(300, 20))
    a.graph("$0-g", 4, 20, 20, 100, 60, 0, 10)
    inl = a.obj("inlet", 300, 200)
    t = a.obj("t b f", 300, 230)
    a.link(inl, 0, t, 0)
    w, s = a.msg("0 $1 $1 $1 $1", 300, 260), a.obj("s $0-g", 300, 285)
    a.link(t, 1, w, 0); a.link(w, 0, s, 0)
    get, show = a.obj("array get $0-g", 300, 310), a.obj("print GOT", 300, 335)
    a.link(t, 0, get, 0); a.link(get, 0, show, 0)
    a.save(tmp_path / "gshow.pd")

    m = Patch(origin=(300, 20))
    one = m.abstraction("gshow", inlets=1, outlets=0, x=20, y=20)
    two = m.abstraction("gshow", inlets=1, outlets=0, x=20, y=120)
    tt = m.obj("t b b b")
    m.link(m.loadbang(), 0, tt, 0)
    v3, v7, q = m.msg("3"), m.msg("7"), m.msg("; pd quit")
    m.link(tt, 2, v3, 0); m.link(v3, 0, one, 0)
    m.link(tt, 1, v7, 0); m.link(v7, 0, two, 0)
    m.link(tt, 0, q, 0)
    m.save(tmp_path / "main.pd")

    console = run_pd(tmp_path / "main.pd", cwd=tmp_path)
    assert "GOT: 3 3 3 3" in console and "GOT: 7 7 7 7" in console, console
    assert "error" not in console.lower(), console


# --------------------------------------------------------------------------- #
# loading a graph back (0.9.1): py2pd raised ParseError on `#X restore x y graph`
# --------------------------------------------------------------------------- #

def _graphed_patch() -> Patch:
    p = Patch(400, 300)
    osc = p.obj("osc~ 220"); dac = p.obj("dac~")
    p.link(osc, 0, dac, 0)
    p.graph("tune", 24, x=20, y=120, w=200, h=80, ylo=0, yhi=16)
    p.graph("lane", 8, x=240, y=120, w=120, h=40, ylo=0, yhi=1.5, style="polygon", hide_name=False)
    step = p.obj("tabread tune"); p.link(p.obj("f 3"), 0, step, 0)
    return p


def test_a_saved_graph_loads_back_as_a_graph_byte_for_byte(tmp_path):
    p = _graphed_patch()
    p.save(tmp_path / "g.pd")
    q = Patch.load(tmp_path / "g.pd")
    graphs = [n for n in q.pd.nodes if type(n).__name__ == "Graph"]
    assert len(graphs) == 2 and all(isinstance(g, Graph) for g in graphs)   # ours, not py2pd's
    built = [str(n) for n in p.pd.nodes if isinstance(n, Graph)]
    assert [str(g) for g in graphs] == built                                  # the records, exactly
    # the rest too, bar py2pd's single-space normalisation of message boxes on
    # load ("  \;  x edit 0" comes back "\;  x edit 0"), which Pd tokenises alike
    squash = lambda t: re.sub(r" +", " ", t)  # noqa: E731
    assert squash(q.render()) == squash(p.render())


def test_a_graph_made_in_pds_gui_loads_verbatim(tmp_path):
    """Put > Array with 'save contents' on: flags 3 and #A data. Not what
    Patch.graph writes, so it stays py2pd's Graph, every record kept."""
    gui = ("#N canvas 0 50 450 300 10;\n"
           "#N canvas 0 50 450 250 (subpatch) 0;\n#X array tune 8 float 3;\n"
           "#A 0 0 0.25 0.5 0.75\n1 0.75 0.5 0.25;\n"
           "#X coords 0 1 8 -1 200 140 1 0 0;\n#X restore 60 40 graph;\n")
    (tmp_path / "gui.pd").write_text(gui, encoding="utf-8")
    q = Patch.load(tmp_path / "gui.pd")
    (node,) = q.pd.nodes
    assert type(node).__name__ == "Graph" and not isinstance(node, Graph)
    assert "#A 0 0 0.25 0.5 0.75\n1 0.75 0.5 0.25;" in q.render()


def test_extract_sees_the_table_a_loaded_graph_allocates(tmp_path):
    _graphed_patch().save(tmp_path / "g.pd")
    q = Patch.load(tmp_path / "g.pd")
    reader = next(n for n in q.pd.nodes if getattr(n, "parameters", {}).get("text") == "tabread tune")
    plan = extraction_plan(q, [reader])
    assert plan["shared"].get("tune") == "table"


def test_pd_reads_a_gui_graphs_saved_contents_after_load_and_save(tmp_path, run_pd):
    """The proof that matters: a graph with saved contents, loaded and saved
    again by pdbuild, still holds its values in Pd."""
    src = ("#N canvas 0 50 450 300 10;\n#X obj 20 20 loadbang;\n"
           "#N canvas 0 50 450 250 (subpatch) 0;\n#X array tune 8 float 3;\n"
           "#A 0 0 0.25 0.5 0.75\n1 0.75 0.5 0.25;\n"
           "#X coords 0 1 8 -1 200 140 1 0 0;\n#X restore 60 40 graph;\n"
           "#X obj 20 200 t b b;\n#X msg 20 230 3;\n#X obj 20 260 tabread tune;\n"
           "#X obj 20 290 print val;\n#X msg 120 230 \; pd quit;\n"
           # [t b b]: right outlet reads index 3, then the left one quits
           "#X connect 0 0 2 0;\n#X connect 2 1 3 0;\n#X connect 3 0 4 0;\n"
           "#X connect 4 0 5 0;\n#X connect 2 0 6 0;\n")
    (tmp_path / "src.pd").write_text(src, encoding="utf-8")
    Patch.load(tmp_path / "src.pd").save(tmp_path / "again.pd")
    console = run_pd(tmp_path / "again.pd", cwd=tmp_path)
    assert "val: 0.75" in console, console
