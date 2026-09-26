"""pdbuild.preview -- look at a control panel you cannot open.

An agent building an instrument never sees the canvas. ``boxes()`` reads the
geometry out of a patch (a ``Patch``/``PdPatch``, the ``.pd`` text, or a file)
with every GUI widget at its real Pd size -- an ``hsl`` is its length x 16,
an ``hradio`` is cells x size, a toggle is size x size, a graph is the
rectangle its ``#X coords`` give it -- and ``layout_png()`` draws it with
matplotlib (the ``[preview]`` extra), so a control panel can be checked for
overlaps, orphaned labels and buried controls by eye. Engine guts (object and
message boxes) are drawn faintly; ``xmax``/``ymax`` crop to the GUI zone.

Graphs are the cheapest visual feedback Pd has, so they are drawn as Pd draws
them: the box, and the data if you supply it (``arrays={"tune": [...]}``, or
contents the file saved). Pd does not clip an array to its graph, and neither
does the preview: a value outside the range lands outside the box, in red.

    from pdbuild.preview import layout_png
    layout_png(patch, "panel.png", xmax=740, arrays={"tune": seed_tune})
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

__all__ = ["Box", "Plot", "boxes", "layout_png", "overlaps"]

_CHAR_W = 6          # ~px per character at font 10
_BOX_H = 17          # object / message box height at font 10


@dataclass(frozen=True)
class Plot:
    """One array drawn in a graph, as its ``#X array`` record declares it."""

    name: str
    size: int
    style: str = "polygon"        # "polygon" | "points" | "bezier"
    hide_name: bool = False
    values: tuple[float, ...] = ()   # contents the file carries (`#A` lines), if any


@dataclass(frozen=True)
class Box:
    kind: str        # hsl vsl hradio vradio tgl bng nbx cnv floatatom symbolatom obj msg text graph array
    x: int
    y: int
    w: int
    h: int
    text: str        # the box text (widgets: the object text; comments: the comment)
    name: str = ""   # widgets: the receive symbol; atoms: the receive slot; graphs: the (first) array
    index: int = 0   # the Pd object index
    plots: tuple[Plot, ...] = ()   # graphs: the arrays drawn in it
    bounds: tuple[float, float, float, float] | None = None
    # graphs: (x_from, y_top, x_to, y_bottom) in data units, from `#X coords`

    @property
    def x1(self) -> int:
        return self.x + self.w

    @property
    def y1(self) -> int:
        return self.y + self.h


def _records(text: str) -> Iterable[str]:
    """Pd records: ';'-terminated, possibly spanning lines; a '\\;' does not end one."""
    buf: list[str] = []
    for line in text.splitlines():
        buf.append(line)
        if re.search(r"(?<!\\);\s*$", line):
            yield " ".join(s.strip() for s in buf)
            buf = []
    if buf:
        yield " ".join(s.strip() for s in buf)


def _num(tok: str, default: float = 0.0) -> float:
    try:
        return float(tok)
    except ValueError:
        return default


def _widget_geometry(cls: str, args: list[str]) -> tuple[int, int, str] | None:
    """(w, h, receive) for an IEM GUI, or None if ``cls`` is not one."""
    g = lambda i: _num(args[i]) if i < len(args) else 0.0  # noqa: E731
    s = lambda i: args[i] if i < len(args) else ""          # noqa: E731
    if cls == "hsl":
        return int(g(0)), int(g(1)), s(7)
    if cls == "vsl":
        return int(g(0)), int(g(1)), s(7)
    if cls == "hradio":
        return int(g(0) * max(1, g(3))), int(g(0)), s(5)
    if cls == "vradio":
        return int(g(0)), int(g(0) * max(1, g(3))), s(5)
    if cls == "tgl":
        return int(g(0)), int(g(0)), s(3)
    if cls == "bng":
        return int(g(0)), int(g(0)), s(5)
    if cls == "nbx":
        return int(g(0) * 7 + 8), int(g(1)), s(7)
    if cls == "cnv":
        return int(g(1)), int(g(2)), s(4)
    return None


# the `#X array` flags field: bit 0 saves the contents, bits 1-2 the plot
# style, bit 3 hides the name (as Pd's own garray_save writes it)
_STYLES = {0: "polygon", 1: "points", 2: "bezier"}


def _array_record(toks: list[str]) -> dict:
    """``#X array name size float flags`` -> a mutable plot draft."""
    flags = int(_num(toks[5])) if len(toks) > 5 else 0
    return {
        "name": toks[2].replace("\\$", "$") if len(toks) > 2 else "",
        "size": max(int(_num(toks[3])), 0) if len(toks) > 3 else 0,
        "style": _STYLES.get((flags & 6) >> 1, "polygon"),
        "hide_name": bool(flags & 8),
        "values": None,
    }


def _array_contents(draft: dict, args: list[str]) -> None:
    """Apply an ``#A <onset> v0 v1 ...`` line (Pd writes saved contents in
    chunks of 1000). ``#A resize`` / ``color`` / ``width`` are properties, not data."""
    if not args:
        return
    try:
        onset = int(float(args[0]))
    except ValueError:
        return
    vals = draft["values"] if draft["values"] is not None else [0.0] * draft["size"]
    for k, tok in enumerate(args[1:]):
        if 0 <= onset + k < len(vals):
            vals[onset + k] = _num(tok)
    draft["values"] = vals


def _child_box(toks: list[str], child: dict, index: int) -> Box:
    """The box a closed child canvas leaves on its parent: a graph, or a
    ``[pd name]`` subpatch (its graph-on-parent rectangle if it has one)."""
    x = int(_num(toks[2])) if len(toks) > 2 else 0
    y = int(_num(toks[3])) if len(toks) > 3 else 0
    what = toks[4:]
    c = child.get("coords")
    if what[:1] == ["graph"]:
        plots = tuple(Plot(d["name"], d["size"], d["style"], d["hide_name"], tuple(d["values"] or ()))
                      for d in child.get("plots", []))
        w, h = (int(c[4]), int(c[5])) if c and len(c) >= 6 else (200, 140)
        if c and len(c) >= 4:
            bounds = (c[0], c[1], c[2], c[3])
        else:
            bounds = (0.0, 1.0, float(max(plots[0].size, 1)) if plots else 100.0, -1.0)
        return Box("graph", x, y, w, h, " ".join(["graph"] + [p.name for p in plots]),
                   plots[0].name if plots else "", index, plots, bounds)
    t = " ".join(what)
    if c and len(c) >= 7 and c[6] >= 1:               # graph-on-parent: its rectangle
        return Box("obj", x, y, int(c[4]), int(c[5]), t, "", index)
    return Box("obj", x, y, len(t) * _CHAR_W + 6, _BOX_H, t, "", index)


def boxes(source) -> list[Box]:
    """Every box in a patch with its canvas rectangle, in index order.

    ``source`` is a ``Patch``/``PdPatch`` (anything with ``render()``), the
    ``.pd`` text, or a path to a ``.pd`` file. Widget sizes are Pd's; object,
    message and comment boxes are estimated from their text length. A graph
    is a ``kind="graph"`` box carrying its ``plots`` and data ``bounds``; a
    ``[pd sub]`` is an ``obj`` box; each takes one index, as in Pd (so does a
    bare ``#X array``, returned as a zero-size ``kind="array"`` box).
    """
    if hasattr(source, "render"):
        text = source.render()
    elif isinstance(source, Path) or (isinstance(source, str) and "#N canvas" not in source and "\n" not in source):
        text = Path(source).read_text(encoding="utf-8")
    else:
        text = str(source)

    out: list[Box] = []
    index = 0
    depth = 0             # open canvases
    top = None            # the depth of the patch's own canvas (0 for a headerless fragment)
    child: dict | None = None   # the child canvas being read: its arrays and coords
    for rec in _records(text):
        rec = rec.strip().rstrip(";").strip()
        if not rec:
            continue
        toks = rec.split()
        if top is None:
            top = 1 if rec.startswith("#N canvas") else 0
        head = toks[0]
        if head == "#N":
            if toks[1:2] == ["canvas"]:               # (not `#N struct`: no canvas)
                depth += 1
                if depth == top + 1:
                    child = {"plots": [], "coords": None}
            continue
        if head == "#A":                              # array contents / properties
            if depth == top + 1 and child and child["plots"]:
                _array_contents(child["plots"][-1], toks[1:])
            continue
        if head != "#X":
            continue
        kind = toks[1] if len(toks) > 1 else ""
        if kind == "restore":
            depth -= 1
            if depth == top:                          # a child canvas closed: ONE box here
                out.append(_child_box(toks, child or {}, index))
                index += 1
                child = None
            continue
        if depth > top:                               # inside a child canvas: not on this one
            if depth == top + 1 and child is not None:
                if kind == "array":
                    child["plots"].append(_array_record(toks))
                elif kind == "coords":
                    child["coords"] = [_num(t) for t in toks[2:]]
            continue
        if kind in ("connect", "coords", "declare"):
            continue
        if kind == "array":
            # a bare array on this canvas (py2pd's add_array): a table with
            # nothing to draw, but it takes a connection index (verified)
            d = _array_record(toks)
            out.append(Box("array", 0, 0, 0, 0, f"array {d['name']} {d['size']}", d["name"], index))
            index += 1
            continue
        x, y = int(_num(toks[2])), int(_num(toks[3]))
        body = toks[4:]
        if kind == "obj":
            cls = body[0] if body else ""
            geo = _widget_geometry(cls, body[1:])
            if geo:
                w, h, rcv = geo
                out.append(Box(cls, x, y, w, h, " ".join(body), rcv, index))
            else:
                t = " ".join(body)
                out.append(Box("obj", x, y, len(t) * _CHAR_W + 6, _BOX_H, t, "", index))
        elif kind == "msg":
            t = " ".join(body)
            out.append(Box("msg", x, y, len(t) * _CHAR_W + 10, _BOX_H, t, "", index))
        elif kind == "text":
            t = " ".join(body).replace("\\,", ",").replace("\\;", ";")
            out.append(Box("text", x, y, min(len(t), 60) * _CHAR_W, _BOX_H * (1 + len(t) // 60), t, "", index))
        elif kind in ("floatatom", "symbolatom"):
            w = int(_num(body[0], 5)) if body else 5
            rcv = body[5] if len(body) > 5 else ""
            out.append(Box(kind, x, y, w * 7 + 4, _BOX_H, kind, rcv, index))
        else:
            out.append(Box(kind, x, y, 20, _BOX_H, " ".join(body), "", index))
        index += 1
    return out


WIDGETS = ("hsl", "vsl", "hradio", "vradio", "tgl", "bng", "nbx", "floatatom", "symbolatom", "cnv", "graph")


def overlaps(items: list[Box], *, kinds: tuple = WIDGETS, guts: bool = True) -> list[tuple[Box, Box]]:
    """Pairs of boxes whose rectangles intersect where at least one is a
    widget (of ``kinds``; graphs included) -- the "control buried under
    something" check, as data. With ``guts`` (default) an object or message
    box sitting on a control counts too (a `[s name]` drawn over a pad, or a
    graph's ``edit 0`` message left at a cursor on the face, is buried just
    the same); comments are ignored, they are meant to sit by controls."""
    ws = [b for b in items if b.kind in kinds]
    others = [b for b in items if b.kind in ("obj", "msg")] if guts else []
    bad = []
    for i, a in enumerate(ws):
        for b in ws[i + 1:] + others:
            if a.x < b.x1 and b.x < a.x1 and a.y < b.y1 and b.y < a.y1:
                bad.append((a, b))
    return bad


_FILL = {
    "hsl": "#c8dcf0", "vsl": "#c8dcf0", "hradio": "#d8e8c8", "vradio": "#d8e8c8",
    "tgl": "#f0c8b4", "bng": "#b4e6c8", "nbx": "#f0e6b4", "floatatom": "#f0e6b4",
    "symbolatom": "#f0e6b4", "cnv": "#eeeeee",
}
_INK, _OUT_OF_RANGE, _LABEL = "#000000", "#dd0000", "#204080"


def _plot_xy(b: Box, i: float, v: float) -> tuple[float, float]:
    """Data (index, value) -> canvas pixels, unclipped, as Pd maps it."""
    x_from, y_top, x_to, y_bottom = b.bounds
    px = b.x + (i - x_from) / ((x_to - x_from) or 1.0) * b.w
    py = b.y + (y_top - v) / ((y_top - y_bottom) or 1.0) * b.h
    return px, py


def _draw_plot(ax, b: Box, plot: Plot, values: Sequence[float], scale: float) -> None:
    n = min(len(values), plot.size) if plot.size else len(values)
    lo, hi = sorted((b.bounds[1], b.bounds[3]))
    vals = [float(v) for v in list(values)[:n]]
    lw = 1.6 * max(scale, 0.6)
    if plot.style == "points":                        # each value a dash one index wide
        for i, v in enumerate(vals):
            (xa, ya), (xb, _) = _plot_xy(b, i, v), _plot_xy(b, i + 1, v)
            ax.plot([xa, xb], [ya, ya], color=_INK if lo <= v <= hi else _OUT_OF_RANGE,
                    linewidth=lw, solid_capstyle="butt")
        return
    pts = [_plot_xy(b, i, v) for i, v in enumerate(vals)]  # polygon (bezier drawn the same)
    ax.plot([p[0] for p in pts], [p[1] for p in pts], color=_INK, linewidth=lw * 0.6)
    outside = [p for p, v in zip(pts, vals) if not lo <= v <= hi]
    if outside:
        ax.scatter([p[0] for p in outside], [p[1] for p in outside], color=_OUT_OF_RANGE, s=6, zorder=3)


def layout_png(source, out_png, *, xmax: int | None = None, ymax: int | None = None,
               scale: float = 1.0, title: str | None = None, guts: bool = True,
               arrays: Mapping[str, Sequence[float]] | None = None) -> Path:
    """Draw the canvas to ``out_png``: widgets at their real size, labelled
    with their receive symbol; graphs as their box, labelled with the array
    name, with the values of ``arrays[name]`` plotted in it (else any
    contents the file saved); comments as text; object/message boxes faint
    (``guts=False`` hides them). ``xmax``/``ymax`` crop to the GUI zone.

    A graph's data is drawn unclipped, where Pd would draw it: a value
    outside the graph's range lands outside the box, in red. Where do the
    values come from? The build's own seed data, or the engine's testimony in
    a render (``[array get tune] -> [print tune]``, parsed from the console).
    Needs matplotlib (``pip install pdbuild[preview]``)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    items = [b for b in boxes(source) if b.kind != "array"]
    if xmax is not None:
        items = [b for b in items if b.x < xmax]
    if ymax is not None:
        items = [b for b in items if b.y < ymax]
    if not guts:
        items = [b for b in items if b.kind not in ("obj", "msg")]
    width = xmax if xmax is not None else max([b.x1 for b in items] + [200]) + 20
    height = ymax if ymax is not None else max([b.y1 for b in items] + [120]) + 20
    small = 5.5 * max(scale, 0.6)

    # the axes fill the figure: `scale` px per canvas unit, so a pixel of the
    # PNG is a place on the canvas (a title gets its own band above it)
    band = 20 if title else 0
    fig = plt.figure(figsize=(width * scale / 100, (height * scale + band) / 100), dpi=100)
    ax = fig.add_axes((0, 0, 1, height * scale / (height * scale + band)))
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.set_aspect("equal")
    ax.axis("off")
    for b in items:
        if b.kind in _FILL:
            ax.add_patch(Rectangle((b.x, b.y), b.w, b.h, facecolor=_FILL[b.kind], edgecolor="#333333", linewidth=0.8))
            if b.kind in ("hradio", "vradio"):
                m = re.match(r"\w+ (\d+) \S+ \S+ (\d+)", b.text)
                if m:
                    cell, n = int(m.group(1)), int(m.group(2))
                    for i in range(1, n):
                        if b.kind == "hradio":
                            ax.plot([b.x + i * cell, b.x + i * cell], [b.y, b.y1], color="#333333", linewidth=0.5)
                        else:
                            ax.plot([b.x, b.x1], [b.y + i * cell, b.y + i * cell], color="#333333", linewidth=0.5)
            if b.name and b.name not in ("empty", "-"):
                ax.text(b.x + 2, b.y - 2, b.name, fontsize=small, color=_LABEL, va="bottom")
        elif b.kind == "graph":
            ax.add_patch(Rectangle((b.x, b.y), b.w, b.h, facecolor="none", edgecolor=_INK, linewidth=0.9))
            names = ", ".join(p.name for p in b.plots)
            if names:
                ax.text(b.x + 2, b.y - 2, names, fontsize=small, color=_LABEL, va="bottom")
            for plot in b.plots:
                values = arrays[plot.name] if arrays and plot.name in arrays else plot.values
                if len(values) and b.bounds:
                    _draw_plot(ax, b, plot, values, scale)
        elif b.kind == "text":
            ax.text(b.x, b.y + 11, b.text, fontsize=6.5 * max(scale, 0.6), color="#000000", va="baseline")
        else:
            ax.add_patch(Rectangle((b.x, b.y), b.w, b.h, facecolor="none", edgecolor="#bbbbbb", linewidth=0.5))
            ax.text(b.x + 2, b.y + 12, b.text[:40], fontsize=5 * max(scale, 0.6), color="#999999", va="baseline")
    if title:
        fig.text(0.01, 1 - band / 2 / (height * scale + band), title, fontsize=8, va="center")
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=100, facecolor="white", bbox_inches=None)
    plt.close(fig)
    return out
