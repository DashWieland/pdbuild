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
