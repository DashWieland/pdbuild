"""pdbuild.preview -- widget geometry out of a patch, and a PNG of it."""

from __future__ import annotations

import pytest

from pdbuild import Patch, PdPatch
from pdbuild.preview import boxes, layout_png, overlaps
from pdbuild.surface import Control, column, display

PANEL = """#N canvas 20 20 900 600 10;
#X text 20 6 a title;
#X obj 20 50 hsl 150 16 100 220 0 0 empty tempo_ui empty -2 -8 0 10 #fcfcfc #000000 #000000 0 1;
#X obj 20 100 hradio 18 1 0 4 empty pattern_ui empty 0 -8 0 10 #fcfcfc #000000 #000000 0;
#X obj 20 150 tgl 20 0 empty run_ui empty 0 -8 0 10 #fcfcfc #cc4400 #000000 0 1;
#X obj 60 150 bng 22 250 50 0 empty go_ui empty 0 -8 0 10 #fcfcfc #00aa66 #000000;
#X obj 20 200 vsl 16 128 0 1 0 0 empty vv_ui empty 0 -9 0 10 #fcfcfc #000000 #000000 0 1;
#X obj 60 200 nbx 5 14 -1e+37 1e+37 0 0 empty val_ui empty 0 -8 0 10 #fcfcfc #000000 #000000 0 256;
#X floatatom 20 360 8 0 0 0 - lastmidi - 0;
#X obj 400 50 osc~ 440;
#X msg 400 80 1 3 \\, 0 210 3;
#X connect 8 0 9 0;
"""


def test_boxes_geometry_matches_pd_widget_sizes():
    bx = {b.name or b.kind: b for b in boxes(PANEL)}
    assert (bx["tempo_ui"].w, bx["tempo_ui"].h) == (150, 16)
    assert (bx["pattern_ui"].w, bx["pattern_ui"].h) == (72, 18)      # 4 cells x 18
    assert (bx["run_ui"].w, bx["run_ui"].h) == (20, 20)
    assert (bx["go_ui"].w, bx["go_ui"].h) == (22, 22)
    assert (bx["vv_ui"].w, bx["vv_ui"].h) == (16, 128)
    assert bx["val_ui"].kind == "nbx" and bx["val_ui"].h == 14
    assert bx["lastmidi"].kind == "floatatom" and bx["lastmidi"].x == 20
    assert bx["text"].text == "a title"
    assert bx["obj"].text == "osc~ 440" and bx["msg"].text.startswith("1 3")


def test_boxes_index_order_counts_every_box():
    items = boxes(PANEL)
    assert [b.index for b in items] == list(range(10))              # connect lines take no index
    assert items[-1].kind == "msg" and items[-1].index == 9


def test_boxes_reads_a_patch_object_and_a_file(tmp_path):
    p = Patch()
    column(p, 20, 50, [Control("tempo", "hsl", 168, "TEMPO", lo=100, hi=220)])
    display(p, "lastmidi", 20, 200)
    from_patch = boxes(p)
    path = tmp_path / "x.pd"
    path.write_text(p.render(), encoding="utf-8")
    from_file = boxes(path)
    assert [b.kind for b in from_patch] == [b.kind for b in from_file]
    assert any(b.kind == "hsl" and b.name == "tempo_ui" for b in from_patch)
    legacy = PdPatch()
    column(legacy, 20, 50, [Control("run", "tgl", 1, "RUN")])
    assert any(b.kind == "tgl" and b.name == "run_ui" for b in boxes(legacy))


def test_overlaps_finds_a_buried_control_and_nothing_else():
    clean = overlaps(boxes(PANEL))
    assert clean == []
    buried = PANEL.replace("#X obj 60 150 bng", "#X obj 25 152 bng")   # the bang now sits on the toggle
    pairs = overlaps(boxes(buried))
    assert len(pairs) == 1 and {pairs[0][0].kind, pairs[0][1].kind} == {"tgl", "bng"}
    # plumbing drawn over a control is buried too (a real pad_row bug); comments are not
    guts = PANEL.replace("#X obj 400 50 osc~ 440", "#X obj 30 52 s tempo")
    pairs = overlaps(boxes(guts))
    assert len(pairs) == 1 and pairs[0][1].kind == "obj"
    assert overlaps(boxes(guts), guts=False) == []
    assert overlaps(boxes(PANEL.replace("#X text 20 6", "#X text 30 52"))) == []


def test_pad_row_plumbing_clears_the_row():
    """REGRESSION (seen in a preview): the plumbing column landed on the 5th pad."""
    from pdbuild.surface import Pad, pad_row
    p = Patch()
    pad_row(p, 20, 400, [Pad(f"p{i}", "momentary") for i in range(5)] + [Pad("x", "cycle", n=4)])
    assert overlaps(boxes(p)) == []


# --------------------------------------------------------------------------- #
# graphs, subpatches, and the index every one of them takes
# --------------------------------------------------------------------------- #

NESTED = """#N canvas 20 20 900 600 10;
#N struct pt float x float y;
#X obj 20 10 loadbang;
#N canvas 0 50 450 300 sub 0;
#X obj 10 10 inlet;
#N canvas 0 50 450 300 deeper 0;
#X obj 10 10 f;
#X restore 10 40 pd deeper;
#X restore 20 40 pd sub;
#N canvas 0 50 450 250 (subpatch) 0;
#X array tune 8 float 3;
#A 0 8 9 10 9;
#A 4 12 10 9 8;
#A resize 8;
#A color 0;
#X coords 0 16.5 8 5.5 200 110 1 0 0;
#X restore 20 80 graph;
#X array bare 4 float 0;
#N canvas 0 50 450 300 panel 0;
#X coords 0 -1 1 1 85 60 1 0 0;
#X restore 300 80 pd panel;
#X msg 20 220 42;
#X obj 20 250 print IDX;
#X msg 120 220 \\; pd quit;
"""


def test_boxes_gives_each_subpatch_graph_and_array_one_index():
    """REGRESSION: a `[pd sub]` or a graph closed back to depth 1, not 0, and
    took no index, so every box after one was numbered one short (and a bare
    `#X array` took none either). Pd counts each as one box."""
    items = boxes(NESTED)
    assert [b.index for b in items] == list(range(len(items)))
    assert [b.kind for b in items] == ["obj", "obj", "graph", "array", "obj", "msg", "obj", "msg"]
    sub, gop = items[1], items[4]
    assert sub.text == "pd sub" and (sub.x, sub.y) == (20, 40)
    assert gop.text == "pd panel" and (gop.w, gop.h) == (85, 60)        # a GOP box is its rectangle
    assert items[5].text == "42" and items[5].index == 5


def test_boxes_indices_are_the_ones_pd_uses(tmp_path, run_pd):
    """Wire the patch with the indices boxes() reports: Pd must deliver."""
    idx = {b.text: b.index for b in boxes(NESTED)}
    lb, msg, show, quit_ = idx["loadbang"], idx["42"], idx["print IDX"], idx["\\; pd quit"]
    wires = (f"#X connect {lb} 0 {msg} 0;\n"
             f"#X connect {msg} 0 {show} 0;\n"
             f"#X connect {lb} 0 {quit_} 0;\n")
    (tmp_path / "n.pd").write_text(NESTED + wires, encoding="utf-8")
    console = run_pd(tmp_path / "n.pd", cwd=tmp_path)
    assert "IDX: 42" in console and "connection failed" not in console, console


def test_boxes_reads_a_graph_with_its_plots_bounds_and_saved_contents():
    g = next(b for b in boxes(NESTED) if b.kind == "graph")
    assert (g.x, g.y, g.w, g.h) == (20, 80, 200, 110)
    assert g.bounds == (0.0, 16.5, 8.0, 5.5) and g.name == "tune"
    (plot,) = g.plots
    assert (plot.name, plot.size, plot.style, plot.hide_name) == ("tune", 8, "points", False)
    assert plot.values == (8, 9, 10, 9, 12, 10, 9, 8)               # two #A chunks; resize/color ignored


def test_boxes_reads_what_patch_graph_writes():
    p = Patch(origin=(600, 20))
    p.graph("tune", 24, 20, 30, 288, 264, 5.5, 16.5)
    p.graph("wave", 64, 340, 30, 200, 120, -1, 1, style="polygon", hide_name=False)
    tune, wave = [b for b in boxes(p) if b.kind == "graph"]
    assert (tune.w, tune.h, tune.bounds) == (288, 264, (0.0, 16.5, 24.0, 5.5))
    assert tune.plots[0].style == "points" and tune.plots[0].hide_name
    assert wave.plots[0].style == "polygon" and wave.bounds == (0.0, 1.0, 63.0, -1.0)


def test_overlaps_sees_graphs():
    """A graph is part of the face: a slider laid on it is buried, and so is a
    lock message left at a cursor that was never moved off the panel."""
    p = Patch()                                          # cursor at the default origin (20, 40)
    p.graph("tune", 8, 20, 30, 200, 100, 0, 10)          # its `edit 0` lock lands at (20, 40): on it
    pairs = overlaps(boxes(p))
    assert any(a.kind == "graph" and b.kind == "msg" and "edit 0" in b.text for a, b in pairs)
    clean = Patch(origin=(600, 40))
    clean.graph("tune", 8, 20, 30, 200, 100, 0, 10)
    assert overlaps(boxes(clean)) == []
    column(clean, 100, 60, [Control("x", "hsl", 0, "X")])  # a slider across the graph
    assert any({a.kind, b.kind} == {"graph", "hsl"} for a, b in overlaps(boxes(clean)))


def test_layout_png_draws_graphs_and_the_data_where_pd_would(tmp_path):
    """The box, the in-range values inside it, and a value outside the range
    drawn outside the box (Pd does not clip), in red."""
    pytest.importorskip("matplotlib")
    import matplotlib.image as mpimg

    p = Patch(origin=(600, 40))
    p.graph("g", 10, 20, 20, 200, 100, 0, 10)
    out = layout_png(p, tmp_path / "g.png", xmax=300, ymax=250, arrays={"g": [5] * 9 + [-5]})
    rgb = mpimg.imread(str(out))[..., :3]
    assert rgb.shape[:2] == (250, 300)
    assert rgb[18:23, 60:180].min() < 0.35, "the graph's top edge is not drawn"
    assert rgb[68:73, 40:180].min() < 0.2, "the in-range values (5 -> y 70) are not drawn"
    red = rgb[166:175, 202:218]                          # -5 -> y 170, below the box (bottom at 120)
    assert ((red[..., 0] > 0.7) & (red[..., 1] < 0.35)).any(), "the out-of-range value is not drawn in red"
    assert rgb[130:160, 30:190].min() > 0.97, "ink between the box and the stray value"
    plain = mpimg.imread(str(layout_png(p, tmp_path / "empty.png", xmax=300, ymax=250)))[..., :3]
    assert plain[68:73, 40:180].min() > 0.9, "no data supplied, yet values were drawn"


def test_layout_png_draws_the_widgets_where_they_are(tmp_path):
    mpl = pytest.importorskip("matplotlib")
    import matplotlib.image as mpimg

    out = layout_png(PANEL, tmp_path / "panel.png", xmax=300, ymax=400)
    assert out.exists() and out.stat().st_size > 1000
    img = mpimg.imread(str(out))
    assert img.shape[0] == 400 and img.shape[1] == 300           # 1 px per canvas unit
    rgb = img[..., :3]
    # the slider at (20..170, 50..66) is drawn; an empty region is white
    slider = rgb[52:64, 30:160]
    empty = rgb[300:380, 200:290]
    assert slider.mean() < 0.97, "slider region is blank"
    assert empty.min() > 0.97, "empty region has ink"
    # cropped to xmax: the engine guts at x=400 are not drawn
    assert rgb.shape[1] == 300
