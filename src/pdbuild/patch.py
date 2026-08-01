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

__all__ = ["Patch", "OBJECT_IO", "object_io"]


# Objects py2pd does not carry counts for. Only entries we are confident
# about: a wrong count here would raise on a connection that is actually
# fine, which is worse than not validating at all.
OBJECT_IO: dict[str, tuple[int, int]] = {
    # filters / DSP
    "bob~": (3, 1),        # audio, cutoff, resonance
    "rev3~": (2, 4),
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
    "noise~": (0, 1),
    "sig~": (1, 1),
    "cos~": (1, 1),
    "tabwrite~": (1, 0),
    # control
    "mtof": (1, 1),
    "ftom": (1, 1),
    "dbtorms": (1, 1),
    "rmstodb": (1, 1),
    # i/o
    "outlet~": (1, 0),
    "inlet~": (0, 1),
    "outlet": (1, 0),
    "inlet": (0, 1),
    # else
    "else/pad": (1, 2),    # list x y, click
}

_EXPR_VAR = re.compile(r"\$[fvs](\d+)")


def object_io(text: str) -> tuple[int, int] | None:
    """Inlet/outlet counts for ``text``, or None to let py2pd decide.

    Handles the objects whose arity depends on their arguments, which a flat
    table cannot express.
    """
    parts = text.split()
    if not parts:
        return None
    cls = parts[0]

    if cls in ("expr", "expr~", "fexpr~"):
        # one inlet per distinct $f1/$v1/$s1 variable, at least one
        idx = [int(m) for m in _EXPR_VAR.findall(text)]
        return (max(idx) if idx else 1, 1)
    if cls in ("pack", "pack~"):
        return (max(len(parts) - 1, 1), 1)

    return OBJECT_IO.get(cls)


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
        the extract roadmap.

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
        so py2pd's connection validation actually fires."""
        px, py = self._place(x, y)
        io = object_io(text)
        kw: dict[str, Any] = {}
        if io is not None:
            kw["num_inlets"], kw["num_outlets"] = io
        return self.pd.add(text, x_pos=px, y_pos=py, **kw)

    def msg(self, text: str, x: int | None = None, y: int | None = None):
        """A message box. ',' and ';' are escaped for you, so
        ``msg("1 3, 0 210 3")`` is a vline~ envelope and ``msg("; pd dsp 1")``
        sends to a named receiver."""
        px, py = self._place(x, y)
        return self.pd.add_msg(text, x_pos=px, y_pos=py)

    def comment(self, text: str, x: int | None = None, y: int | None = None):
        """A comment. It takes an index in Pd's connection numbering like any
        other box, and its ',' / ';' are escaped."""
        px, py = self._place(x, y)
        return self.pd.add_comment(text, x_pos=px, y_pos=py)

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
