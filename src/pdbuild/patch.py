"""Patch -- our Pd idioms over py2pd's Patcher.

py2pd owns the file format, connection bookkeeping, parsing and validation.
This layer adds the things a *musical* patch needs that a general format
library has no reason to carry:

  * a placement cursor, because py2pd's layout manager puts one element per
    row and a 140-object engine becomes a 4000px column;
  * inlet/outlet counts for the DSP objects py2pd does not know, because an
    unknown object has ``num_inlets = None`` and validation then silently
    does not fire for it -- which is precisely the objects we use as sinks;
  * ``init()``, which wires ``loadbang -> [value( -> widget``. GUI controls
    emit nothing until a human touches them, so anything downstream sits at
    zero and the patch is silent with no error. This has bitten us twice.

Anything py2pd already does well is reached through ``.pd``, the underlying
Patcher -- GUI constructors, arrays, subpatches, SVG export, round-tripping.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from py2pd import Patcher
from py2pd.api import Node

__all__ = ["Patch", "Graph", "GRAPH_STYLES", "OBJECT_IO", "object_io"]


# Objects py2pd does not carry counts for. Only entries we are confident
# about: a wrong count here would raise on a connection that is actually
# fine, which is worse than not validating at all. Every entry is checked
# against Pd 0.56.2 by tests/test_patch.py::test_declared_arity_agrees_with_pd.
OBJECT_IO: dict[str, tuple[int, int]] = {
    # filters / DSP
    "bob~": (3, 1),        # audio, cutoff, resonance
    "rev3~": (6, 4),       # in L, in R, then level / liveness / crossover / damping
                           # (was (2, 4): a link to the level inlet was refused)
    "vline~": (3, 1),
    "phasor~": (2, 1),
    "vd~": (1, 1),
    "delwrite~": (1, 0),
    "delread~": (1, 1),
    "clip~": (3, 1),
    "clip": (3, 1),
    "lop~": (2, 1),
    "hip~": (2, 1),
    "bp~": (3, 1),
    "vcf~": (3, 2),
    "noise~": (1, 1),      # the inlet takes `seed <n>` (was (0, 1))
    "sig~": (1, 1),
    "cos~": (1, 1),
    "tabwrite~": (1, 0),
    # control
    "mtof": (1, 1),
    "ftom": (1, 1),
    "dbtorms": (1, 1),
    "rmstodb": (1, 1),
    "until": (2, 1),
    "makefilename": (1, 1),
    # i/o
    "outlet~": (1, 0),
    "inlet~": (1, 2),      # outlet 1: control data sent to the signal inlet (was (0, 1))
    "outlet": (1, 0),
    "inlet": (0, 1),
    # IEM GUIs (py2pd leaves these unknown, so a mis-wired control went unchecked)
    "hsl": (1, 1),
    "vsl": (1, 1),
    "hradio": (1, 1),
    "vradio": (1, 1),
    "tgl": (1, 1),
    "bng": (1, 1),
    "nbx": (1, 1),
    # else
    "else/pad": (1, 2),    # list x y, click
}

# An [expr] family inlet per variable number: $f/$i/$s (control), $v (expr~
# signal vector), $x (fexpr~ input sample). $y is an fexpr~ OUTPUT's past
# samples and opens no inlet. (Each verified on Pd 0.56.2 by connecting one
# past the last port, which Pd refuses: "... connection failed".)
_EXPR_VAR = re.compile(r"\$[fisvx](\d+)")

# The escapes py2pd's own escape() adds: `\,` `\;` and `\$<digit>`. Stripping
# them first makes obj()/msg()/comment() idempotent -- text a caller already
# escaped is not escaped again into `\\\$1` (which Pd reads as a literal
# backslash and a dollar-arg, i.e. garbage).
_OUR_ESCAPES = re.compile(r"\\([,;])|\\(\$)(?=\d)")


def _normalize_escapes(text: str) -> str:
    return _OUR_ESCAPES.sub(lambda m: m.group(1) or m.group(2), text)


def object_io(text: str) -> tuple[int, int] | None:
    """Inlet/outlet counts for ``text``, or None to let py2pd decide.

    Handles the objects whose arity depends on their arguments, which a flat
    table cannot express.

    ``[expr]`` / ``[expr~]`` / ``[fexpr~]`` take one inlet per variable number
    (the highest ``$f2``/``$i2``/``$s2``/``$v2``/``$x2`` gives two) and one
    outlet per expression: ``expr $f1 + $f2; $f1 * $f2`` (written ``\\;`` in
    the file) has two. A multi-expression expr fires its outlets **right to
    left**, the last expression first, so a ``[pack]`` fed from outlets
    0..n-1 packs them in order: outlet 0 reaches the hot inlet last.
    """
    parts = text.split()
    if not parts:
        return None
    cls = parts[0]

    if cls in ("expr", "expr~", "fexpr~"):
        body = text.split(None, 1)[1] if len(parts) > 1 else ""
        idx = [int(m) for m in _EXPR_VAR.findall(body)]
        # ';' separates expressions; in file text it is escaped '\;', which
        # leaves a stray backslash on the segment before it
        exprs = [e for e in body.split(";") if e.strip(" \\\t\r\n")]
        return (max(idx) if idx else 1, max(len(exprs), 1))
    if cls in ("pack", "pack~"):
        return (max(len(parts) - 1, 1), 1)
    if cls == "writesf~":
        # [writesf~ N]: one signal inlet per channel (the first takes the
        # open/start/stop messages too); N defaults to 1
        n = parts[1] if len(parts) > 1 else "1"
        return (max(int(float(n)), 1), 0) if _is_number(n) else None
    if cls == "file" and len(parts) > 1:
        return _FILE_IO.get(parts[1])

    return OBJECT_IO.get(cls)


# [file <verb>]: the arity depends on the verb; only the ones verified on Pd.
_FILE_IO: dict[str, tuple[int, int]] = {
    "patchpath": (1, 2),   # symbol -> "<patch dir>/<symbol>" (left); bang -> the directory
    "isfile": (1, 2),      # an existing file -> 1 (left); a missing one BANGS the right outlet
}


def _is_number(tok: str) -> bool:
    try:
        float(tok)
    except ValueError:
        return False
    return True


# Array plot styles, numbered as the `#X array` flags field counts them.
GRAPH_STYLES: dict[str, int] = {"polygon": 0, "points": 1, "bezier": 2}


def _atom(v) -> str:
    """A number as a .pd atom: integral values without a trailing '.0'."""
    f = float(v)
    return str(int(f)) if f.is_integer() else repr(f)


class Graph(Node):
    """A graph-on-parent array: a table the player can see on the canvas.

    Four records, the way Pd itself saves one::

        #N canvas 0 50 450 250 (subpatch) 0;
        #X array tune 24 float 10;             <- flags: 2*style, +8 hides the name
        #X coords 0 16.5 24 5.5 400 220 1 0 0; <- x from/to, y top/bottom values, w h
        #X restore 20 300 graph;

    It is ONE box on the parent canvas, so it takes one connection index there
    like any object; it has no inlets or outlets. (py2pd's ``add_array``
    writes a bare ``#X array``: a table, but nothing to look at.) Built by
    ``Patch.graph``; Pd 0.56.2 re-saves these four records unchanged.
    """

    def __init__(self, name: str, size: int, x: int, y: int, w: int, h: int,
                 ylo: float, yhi: float, *, style: str = "points",
                 hide_name: bool = True) -> None:
        if style not in GRAPH_STYLES:
            raise ValueError(f"unknown graph style {style!r}; one of {tuple(GRAPH_STYLES)}")
        if int(size) < 1:
            raise ValueError(f"a graph needs at least one point, got size {size}")
        if float(ylo) == float(yhi):
            raise ValueError("ylo and yhi must differ: they are the values at the bottom and top edges")
        self.parameters = {
            "x_pos": int(x), "y_pos": int(y), "name": name, "size": int(size),
            "w": int(w), "h": int(h), "ylo": ylo, "yhi": yhi,
            "style": style, "hide_name": bool(hide_name),
        }
        self.num_inlets = 0
        self.num_outlets = 0

    @property
    def flags(self) -> int:
        """The ``#X array`` flags field: 2 * style, + 8 to hide the name. (+1
        would save the contents into the file; ``Patch.graph`` never sets it,
        so what the engine writes at run time is not frozen in on a save.)"""
        p = self.parameters
        return 2 * GRAPH_STYLES[p["style"]] + (8 if p["hide_name"] else 0)

    @property
    def x_range(self) -> int:
        """The index at the right edge, fitted the way Pd fits a graph to its
        array: points draw each value one index wide (``size``); a polygon or
        bezier puts its last vertex on the edge (``size - 1``)."""
        n = self.parameters["size"]
        return n if self.parameters["style"] == "points" or n == 1 else n - 1

    def __str__(self) -> str:
        p = self.parameters
        name = re.sub(r"(?<!\\)\$(?=\d)", r"\\$", p["name"])   # $0-tune -> \$0-tune
        return (
            "#N canvas 0 50 450 250 (subpatch) 0;\n"
            f"#X array {name} {p['size']} float {self.flags};\n"
            f"#X coords 0 {_atom(p['yhi'])} {self.x_range} {_atom(p['ylo'])} {p['w']} {p['h']} 1 0 0;\n"
            f"#X restore {p['x_pos']} {p['y_pos']} graph;\n"
        )

    def __repr__(self) -> str:
        return f"Graph({self.parameters['name']!r}, {self.parameters['size']})"

    @property
    def dimensions(self) -> tuple[int, int]:
        return (self.parameters["w"], self.parameters["h"])


class Patch:
    """A py2pd Patcher plus a placement cursor and our Pd idioms."""

    def __init__(
        self,
        width: int = 1100,
        height: int = 800,
        font: int = 10,
        *,
        x: int = 20,
        y: int = 20,
        origin: tuple[int, int] = (20, 40),
        step: int = 26,
        bottom: int = 720,
        column: int = 150,
    ) -> None:
        self.pd = Patcher(x=x, y=y, width=width, height=height, font=font)
        self.x, self.y = origin
        self.step, self.bottom, self.column = step, bottom, column
        self._loadbang = None

    @classmethod
    def wrap(cls, patcher: Patcher, *, origin: tuple[int, int] = (20, 40),
             step: int = 26, bottom: int = 720, column: int = 150,
             source_dir=None) -> "Patch":
        """Adopt an existing py2pd Patcher, e.g. one loaded from a file."""
        self = cls.__new__(cls)
        self.pd = patcher
        self.x, self.y = origin
        self.step, self.bottom, self.column = step, bottom, column
        self._loadbang = None
        # Directory the patch came from, if any -- extract() searches it to
        # resolve the port types of abstraction instances the patch references.
        self.source_dir = str(source_dir) if source_dir is not None else None
        return self

    @classmethod
    def load(cls, path, **kw) -> "Patch":
        """Load an existing ``.pd`` file into an editable Patch.

        Goes through py2pd's ``parse_file`` -> ``to_builder``, which is
        lossless for the patches we care about (audio renders identically, the
        file round-trips essentially byte-for-byte). Patches using ``#X
        declare`` or graph-on-parent are not yet supported on this path -- see
        the extract roadmap. A file holding a graph (``Patch.graph``) is
        refused outright: py2pd raises ``ParseError: Invalid restore line``
        on its ``#X restore x y graph``.

        Records the file's directory as ``source_dir`` so a later ``extract``
        can find the sibling abstraction files this patch instantiates.
        """
        from pathlib import Path as _Path
        from py2pd import parse_file, to_builder
        kw.setdefault("source_dir", _Path(str(path)).resolve().parent)
        return cls.wrap(to_builder(parse_file(str(path))), **kw)

    # -- placement ---------------------------------------------------------
    def cursor(self, x: int, y: int) -> None:
        """Move the cursor -- e.g. past a reserved GUI zone, so generated
        guts do not land on top of the controls a human has to reach."""
        self.x, self.y = x, y

    def _place(self, x: int | None, y: int | None) -> tuple[int, int]:
        if x is not None and y is not None:
            return x, y
        px, py = self.x, self.y
        self.y += self.step
        if self.y > self.bottom:
            self.y = 40
            self.x += self.column
        return px, py

    # -- unique names ------------------------------------------------------
    def uid(self, prefix: str = "u") -> str:
        """A patch-unique name, e.g. for a module's delay-line buffer.

        Deterministic within a build (same construction order -> same names),
        so two delays in one patch never share a buffer, and re-running the
        build is reproducible. extract() namespaces these to ``$0-`` if the
        module later moves into an abstraction, so instances stay independent.
        """
        self._uid_n = getattr(self, "_uid_n", 0) + 1
        return f"{prefix}{self._uid_n}"

    # -- boxes -------------------------------------------------------------
    def obj(self, text: str, x: int | None = None, y: int | None = None):
        """An object box, with inlet/outlet counts declared where we know them
        so py2pd's connection validation actually fires.

        Creation-arg dollars (``$1``, ``$0-name``) are written ``\\$1`` in the
        file, as Pd requires, and a comma in ``expr if(a, b, c)`` is written
        ``\\,``. Text you already escaped is normalised first, so it is never
        escaped twice. ``$v1``/``$f1`` (expr variables) pass through.
        """
        px, py = self._place(x, y)
        text = _normalize_escapes(text)
        io = object_io(text)
        kw: dict[str, Any] = {}
        if io is not None:
            kw["num_inlets"], kw["num_outlets"] = io
        return self.pd.add(text, x_pos=px, y_pos=py, **kw)

    def msg(self, text: str, x: int | None = None, y: int | None = None):
        """A message box. ',' and ';' are escaped for you, so
        ``msg("1 3, 0 210 3")`` is a vline~ envelope and ``msg("; pd dsp 1")``
        sends to a named receiver; ``$1`` is written ``\\$1``. Idempotent:
        already-escaped text is not escaped again."""
        px, py = self._place(x, y)
        return self.pd.add_msg(_normalize_escapes(text), x_pos=px, y_pos=py)

    def comment(self, text: str, x: int | None = None, y: int | None = None):
        """A comment. It takes an index in Pd's connection numbering like any
        other box, and its ',' / ';' / ``$1`` are escaped (a bare dollar in a
        comment is evaluated at load and errors too). Idempotent."""
        px, py = self._place(x, y)
        return self.pd.add_comment(_normalize_escapes(text), x_pos=px, y_pos=py)

    def floatatom(self, x: int | None = None, y: int | None = None, *,
                  send: str = "-", receive: str = "-", width: int = 5):
        """A number box.

        ``send`` makes typing in the box do something; ``receive`` makes it
        display what is sent to that name. They are distinct slots -- getting
        them crossed yields a box that looks wired and is inert.
        """
        px, py = self._place(x, y)
        return self.pd.add_float(x_pos=px, y_pos=py, send=send,
                                 receive=receive, width=width)

    def abstraction(self, name: str, *, inlets: int = 0, outlets: int = 1,
                    x: int | None = None, y: int | None = None):
        """An instance of a sibling ``<name>.pd``."""
        px, py = self._place(x, y)
        return self.pd.add_abstraction(name, num_inlets=inlets,
                                       num_outlets=outlets, x_pos=px, y_pos=py)

    def graph(self, name: str, size: int, x: int, y: int, w: int, h: int,
              ylo: float, yhi: float, *, style: str = "points",
              hide_name: bool = True, editable: bool = False) -> Graph:
        """A graph-on-parent array: ``size`` values of the table ``name``
        drawn in a ``w`` x ``h`` box at ``(x, y)``, the bottom edge standing
        for ``ylo`` and the top for ``yhi``.

        The cheapest visual feedback Pd has: a tune, a step pattern, a lane of
        flags the player can watch. Write it like any table -- ``[tabwrite
        name]``, ``; name 0 v0 v1 ...``, ``[array set name]``. It takes one
        connection index in this patch and has no inlets or outlets.

        **Pd does not clip an array to its graph.** A value outside ``[ylo,
        yhi]`` is drawn outside the box, over whatever is next to it, so the
        range must contain every value the patch will ever write. (A lane of
        0/1 flags: ``ylo=0, yhi=1.5`` draws the 1s inside and lays the 0s on
        the bottom edge.)

        ``style`` is ``"points"`` (each value a dash one index wide),
        ``"polygon"`` or ``"bezier"``. ``hide_name`` hides the name Pd draws
        over the graph. ``editable=False`` (the default) sends ``; name edit
        0`` from the shared loadbang: in run mode Pd lets a mouse drag draw
        into any array, so a display would otherwise be an input that writes
        arbitrary -- fractional, out-of-scale -- values into the engine's
        table. The edit state is not saved in the file, hence the message.
        That message is plumbing and goes at the placement cursor, like
        ``init()``'s, so keep the cursor off the face (``Patch(origin=...)``
        past the panel). An instance-local name (``$0-tune``, for a graph
        inside an abstraction) is locked through ``[edit 0( -> [s $0-tune]``
        instead: a message box expands ``$0`` to 0 (Pd 0.56.2), so ``; $0-tune
        edit 0`` would go to ``0-tune``.
        """
        g = Graph(name, size, x, y, w, h, ylo, yhi, style=style, hide_name=hide_name)
        self.pd.nodes.append(g)
        if not editable:
            if "$" in name:
                lock = self.msg("edit 0")
                self.link(lock, 0, self.obj(f"s {name}"), 0)
            else:
                lock = self.msg(f"; {name} edit 0")
            self.link(self.loadbang(), 0, lock, 0)
        return g

    # -- wiring ------------------------------------------------------------
    def link(self, src, outlet: int, sink, inlet: int = 0) -> None:
        """``link(a, 0, b, 1)`` -- source-outlet-sink-inlet reading order.

        (py2pd's own ``link`` takes (source, sink, outlet, inlet); this keeps
        the wire reading left-to-right as it does in the patch.)
        """
        self.pd.link(src, sink, outlet, inlet)

    def chain(self, *nodes) -> None:
        """Connect outlet 0 -> inlet 0 down a run of objects."""
        for a, b in zip(nodes, nodes[1:]):
            self.pd.link(a, b, 0, 0)

    # -- initialization ----------------------------------------------------
    def loadbang(self):
        """The patch's shared ``[loadbang]``, created on first use."""
        if self._loadbang is None:
            self._loadbang = self.obj("loadbang")
        return self._loadbang

    def init(self, target, value):
        """Wire ``loadbang -> [value( -> target`` so a control emits at load.

        A toggle/radio/slider sends nothing until touched, leaving a gain
        ``line~`` or a ``spigot`` gate at zero: the whole patch renders silent
        and Pd reports no error. Use this for every control something
        downstream depends on. Returns the message box.
        """
        lb = self.loadbang()
        m = self.msg(str(value))
        self.link(lb, 0, m, 0)
        self.link(m, 0, target, 0)
        return m

    def init_all(self, mapping: dict) -> None:
        """``init_all({toggle: 0, radio: 0, slider: 0.65})``."""
        for target, value in mapping.items():
            self.init(target, value)

    # -- checking ----------------------------------------------------------
    def unvalidated(self) -> list[str]:
        """Objects with no declared I/O -- their connections are unchecked.

        Not an error, but worth printing after a build: anything listed here
        could be mis-wired and neither py2pd nor this layer would say so.
        """
        out = []
        for n in self.pd.nodes:
            if getattr(n, "num_inlets", 0) is None:
                out.append(n.parameters.get("text", repr(n)))
        return sorted(set(out))

    def validate(self, *, check_cycles: bool = False) -> list[str]:
        return self.pd.validate_connections(check_cycles=check_cycles)

    # -- output ------------------------------------------------------------
    def render(self) -> str:
        return str(self.pd)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.pd.save(str(p))
        return p

    def __repr__(self) -> str:
        return (f"Patch(nodes={len(self.pd.nodes)}, "
                f"connections={len(self.pd.connections)})")
