"""pdbuild.preview -- look at a control panel you cannot open.

An agent building an instrument never sees the canvas. ``boxes()`` reads the
geometry out of a patch (a ``Patch``/``PdPatch``, the ``.pd`` text, or a file)
with every GUI widget at its real Pd size -- an ``hsl`` is its length x 16,
an ``hradio`` is cells x size, a toggle is size x size -- and ``layout_png()``
draws it with matplotlib (the ``[preview]`` extra), so a control panel can be
checked for overlaps, orphaned labels and buried controls by eye. Engine guts
(object and message boxes) are drawn faintly; ``xmax``/``ymax`` crop to the
GUI zone.

    from pdbuild.preview import layout_png
    layout_png(patch, "panel.png", xmax=740)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

__all__ = ["Box", "boxes", "layout_png", "overlaps"]

_CHAR_W = 6          # ~px per character at font 10
_BOX_H = 17          # object / message box height at font 10


@dataclass(frozen=True)
class Box:
    kind: str        # hsl vsl hradio vradio tgl bng nbx cnv floatatom symbolatom obj msg text
    x: int
    y: int
    w: int
    h: int
    text: str        # the box text (widgets: the object text; comments: the comment)
    name: str = ""   # widgets: the receive symbol; atoms: the receive slot
    index: int = 0   # the Pd object index

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


def boxes(source) -> list[Box]:
    """Every box in a patch with its canvas rectangle, in index order.

    ``source`` is a ``Patch``/``PdPatch`` (anything with ``render()``), the
    ``.pd`` text, or a path to a ``.pd`` file. Widget sizes are Pd's; object,
    message and comment boxes are estimated from their text length.
    """
    if hasattr(source, "render"):
        text = source.render()
    elif isinstance(source, Path) or (isinstance(source, str) and "#N canvas" not in source and "\n" not in source):
        text = Path(source).read_text(encoding="utf-8")
    else:
        text = str(source)

    out: list[Box] = []
    index = 0
    depth = 0
    for rec in _records(text):
        rec = rec.strip().rstrip(";").strip()
        if not rec:
            continue
        toks = rec.split()
        if toks[0] == "#N":
            depth += 1                                # a subpatch canvas opens
            continue
        if toks[0] == "#A":
            continue
        if toks[0] != "#X":
            continue
        kind = toks[1] if len(toks) > 1 else ""
        if kind == "restore":
            depth -= 1
            if depth == 0:
                index += 1                            # the [pd sub] box counts once on the parent
            continue
        if depth > 1:
            continue                                  # inside a subpatch: not on this canvas
        if kind in ("connect", "coords", "declare", "array"):
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


WIDGETS = ("hsl", "vsl", "hradio", "vradio", "tgl", "bng", "nbx", "floatatom", "symbolatom", "cnv")


def overlaps(items: list[Box], *, kinds: tuple = WIDGETS, guts: bool = True) -> list[tuple[Box, Box]]:
    """Pairs of boxes whose rectangles intersect where at least one is a
    widget (of ``kinds``) -- the "control buried under something" check, as
    data. With ``guts`` (default) an object or message box sitting on a
    control counts too (a `[s name]` drawn over a pad is buried just the
    same); comments are ignored, they are meant to sit by controls."""
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


def layout_png(source, out_png, *, xmax: int | None = None, ymax: int | None = None,
               scale: float = 1.0, title: str | None = None, guts: bool = True) -> Path:
    """Draw the canvas to ``out_png``: widgets at their real size, labelled
    with their receive symbol; comments as text; object/message boxes faint
    (``guts=False`` hides them). ``xmax``/``ymax`` crop to the GUI zone.
    Needs matplotlib (``pip install pdbuild[preview]``)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    items = boxes(source)
    if xmax is not None:
        items = [b for b in items if b.x < xmax]
    if ymax is not None:
        items = [b for b in items if b.y < ymax]
    if not guts:
        items = [b for b in items if b.kind not in ("obj", "msg")]
    width = xmax if xmax is not None else max([b.x1 for b in items] + [200]) + 20
    height = ymax if ymax is not None else max([b.y1 for b in items] + [120]) + 20

    fig, ax = plt.subplots(figsize=(width * scale / 100, height * scale / 100), dpi=100)
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
                ax.text(b.x + 2, b.y - 2, b.name, fontsize=5.5 * max(scale, 0.6), color="#204080", va="bottom")
        elif b.kind == "text":
            ax.text(b.x, b.y + 11, b.text, fontsize=6.5 * max(scale, 0.6), color="#000000", va="baseline")
        else:
            ax.add_patch(Rectangle((b.x, b.y), b.w, b.h, facecolor="none", edgecolor="#bbbbbb", linewidth=0.5))
            ax.text(b.x + 2, b.y + 12, b.text[:40], fontsize=5 * max(scale, 0.6), color="#999999", va="baseline")
    if title:
        ax.set_title(title, fontsize=8)
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=100, facecolor="white", bbox_inches=None)
    plt.close(fig)
    return out
