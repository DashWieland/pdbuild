r"""The original standalone `.pd` emitter — FROZEN.

Tracks object indices as you add objects and emits valid `.pd` text, so
`#X connect` indices are always correct — the #1 failure mode of machine-written
patches. Every element that renders as an `#X ...` line (objects, messages, AND
comments) increments the index, which is exactly how Pd counts them.

Superseded by `pdbuild.Patch`, which layers the same idioms over py2pd. Kept
because `instruments/` was built with it and those builds should stay
reproducible. Deliberately not being fixed or extended -- with one exception,
below.

**Known defect, left in place:** `floatatom()` emits `send` and `recv` into
each other's slots, so a number box built here is display-only and typing in
it does nothing. Nothing catches this by ear — a headless render never types
into a box. `Patch.floatatom()` gets it right, and `pdbuild.surface.display()`
gets it right on top of *this* class.

**The one change since freezing (0.8.0): escaping is complete and idempotent.**
In a `.pd` file a creation argument or message dollar must be written `\$1`
(an unescaped `$1` is evaluated while the file loads, against nothing: the
box gets `0` and the console says `argument number out of range` -- this
silently zeroed every argument of a Karplus-Strong abstraction), and a comma
inside an object box (`expr if(a, b, c)`) must be `\,`. `obj()`, `msg()` and
`comment()` now escape `$<digit>` and bare `,`/`;`, and never touch a `\$`,
`\,` or `\;` that is already escaped -- the double-escape that garbled a
setup message in the field is gone too. Every text a build script already
wrote correctly renders byte-identically (proven on all 16 committed
instrument files); the only bytes that change are ones that were broken.
"""

from __future__ import annotations

import re

__version__ = "0.1.0"
__all__ = ["PdPatch"]

# a message separator not already escaped, and a dollar-arg ($ + digit) not
# already escaped. `$f1`/`$v1` (expr variables) have no digit after the `$`.
_BARE_SEP = re.compile(r"(?<!\\)([,;])")
_BARE_DOLLAR = re.compile(r"(?<!\\)\$(?=\d)")


class PdPatch:
    """Accumulates `.pd` records and hands back the object index of each one.

    Positions are optional: omit x/y and the box is placed by an auto-layout
    cursor that flows down a column and wraps. Give explicit x/y for anything a
    human will interact with (GUI controls), and `cursor()` past that zone so the
    generated guts don't land on top of them.
    """

    def __init__(self, w: int = 1240, h: int = 840, font: int = 10):
        self.lines = [f"#N canvas 20 20 {w} {h} {font};"]
        self.n = 0
        self._cx, self._cy = 30, 40  # auto-layout cursor
        self._lb: int | None = None  # shared loadbang, created on demand

    # -- placement ---------------------------------------------------------
    def _xy(self, x, y):
        if x is None or y is None:
            x, y = self._cx, self._cy
            self._cy += 26
            if self._cy > 790:
                self._cy = 40
                self._cx += 150
        return x, y

    def cursor(self, x: int, y: int) -> None:
        """Move the auto-layout cursor (e.g. below a reserved GUI zone)."""
        self._cx, self._cy = x, y

    # -- boxes -------------------------------------------------------------
    def obj(self, text: str, x=None, y=None) -> int:
        """An object box: `obj("osc~ 440")`, `obj("bob~")`, `obj("tgl 22 0 ...")`.

        Creation-arg dollars (`$1`, `$0-name`) are written `\\$1` as the file
        format requires, and a bare `,` (`expr if(a, b, c)`) becomes `\\,`;
        text that is already escaped is left exactly as it is. `$v1`/`$f1` in
        `expr~`/`expr` are not dollar-args and survive intact.
        """
        x, y = self._xy(x, y)
        self.lines.append(f"#X obj {x} {y} {self._escape_obj(text)};")
        return self._bump()

    def msg(self, text: str, x=None, y=None) -> int:
        """A message box. Write ',' and ';' naturally — they're escaped for you,
        so `msg("1 3, 0 210 3")` is a vline~ attack/decay and `msg("; pd dsp 1")`
        sends to a named receiver. `$1` is written `\\$1`; already-escaped text
        is never escaped twice."""
        x, y = self._xy(x, y)
        self.lines.append(f"#X msg {x} {y} {self._escape(text)};")
        return self._bump()

    def floatatom(self, x: int, y: int, recv: str = "-", send: str = "-", w: int = 8) -> int:
        """A number box; with `recv` it displays whatever is sent to that name."""
        self.lines.append(f"#X floatatom {x} {y} {w} 0 0 0 - {send} {recv} 0;")
        return self._bump()

    def comment(self, text: str, x: int, y: int) -> int:
        """A comment. NOTE: comments take an index in Pd's connection numbering,
        and an unescaped ',' or ';' inside one is parsed as a message separator
        (Pd then tries to send the tail somewhere and errors) — escaped for you."""
        self.lines.append(f"#X text {x} {y} {self._escape(text)};")
        return self._bump()

    # -- initialization ----------------------------------------------------
    def loadbang(self, x=None, y=None) -> int:
        """The patch's shared `[loadbang]`, created on first use."""
        if self._lb is None:
            self._lb = self.obj("loadbang", x, y)
        return self._lb

    def init(self, target: int, value, x=None, y=None) -> int:
        """Wire `[loadbang] -> [value( -> target` so a GUI control emits at load.

        GUI controls (tgl/hradio/hsl/nbx) send **nothing** until a human touches
        them, leaving anything downstream — a gain `line~`, a `spigot` gate — at
        0. That is the most common cause of a silent patch, and it produces no
        error. Use this for every control something depends on.

        Returns the message-box index.
        """
        lb = self.loadbang()
        m = self.msg(str(value), x, y)
        self.connect(lb, 0, m, 0)
        self.connect(m, 0, target, 0)
        return m

    def init_all(self, mapping: dict) -> None:
        """`init_all({toggle: 0, radio: 0, slider: 0.65})`."""
        for target, value in mapping.items():
            self.init(target, value)

    # -- wiring ------------------------------------------------------------
    def connect(self, src: int, outlet: int, dst: int, inlet: int) -> None:
        self.lines.append(f"#X connect {src} {outlet} {dst} {inlet};")

    def chain(self, *ids: int) -> None:
        """Connect outlet 0 -> inlet 0 down a list of object ids."""
        for a, b in zip(ids, ids[1:]):
            self.connect(a, 0, b, 0)

    # -- output ------------------------------------------------------------
    def render(self) -> str:
        return "\n".join(self.lines) + "\n"

    def save(self, path) -> None:
        from pathlib import Path

        Path(path).write_text(self.render(), encoding="utf-8")

    # -- internals ---------------------------------------------------------
    def _bump(self) -> int:
        i = self.n
        self.n += 1
        return i

    @staticmethod
    def _escape(text: str) -> str:
        """Message / comment escaping: a bare separator becomes ` \\,` / ` \\;`
        (the spacing the frozen emitter always used, so existing instruments
        regenerate byte-for-byte), a bare `$<digit>` becomes `\\$`; anything
        already escaped is left alone."""
        text = _BARE_SEP.sub(r" \\\1", text)
        return _BARE_DOLLAR.sub(r"\\$", text)

    @staticmethod
    def _escape_obj(text: str) -> str:
        """Object-box escaping: like `_escape` but a bare separator becomes
        `\\,` with no added space, the way `expr if(a\\, b\\, c)` is written."""
        text = _BARE_SEP.sub(r"\\\1", text)
        return _BARE_DOLLAR.sub(r"\\$", text)
