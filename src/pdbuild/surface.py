"""pdbuild.surface -- control surfaces: GUI widgets wired as named sends.

The idiom, now the standard for every instrument (it carried lila_rig's 40
controls and its MiniLab mapping):

    the engine reads        [r name]
    the widget emits        [s name]          from its outlet
    the widget listens on   name_ui           its receive symbol

Anything -- the GUI itself, a hardware CC, a pad, a test script -- sets a
control by sending to ``name_ui``; the widget updates on the panel AND
re-emits to ``name``; the engine has one source of truth. Every control is
therefore injection-testable ("message twin"): ``control.send("tempo_ui",
180)`` in pdverify drives the rig exactly the way a hand on the panel does,
and the panel shows it. The loadbang init message -- the #1 cause of a silent
patch -- is part of every control, not an afterthought.

Works with both builders, ``Patch`` (py2pd) and the frozen ``PdPatch``:
everything here goes through ``obj`` / ``msg`` / ``comment`` / ``loadbang``
and ``link`` (Patch) or ``connect`` (PdPatch).

    from pdbuild import Patch
    from pdbuild.surface import Control, column, display, pad_row, Pad, cc_map, note_split

    p = Patch()
    column(p, 20, 50, [
        Control("run", "tgl", default=1, label="RUN"),
        Control("tempo", "hsl", default=168, lo=100, hi=220, label="TEMPO (BPM)"),
        Control("pattern", "hradio", default=0, n=4, label="PATTERN"),
    ])
    display(p, "lastmidi", 20, 300)           # a number box that SHOWS [s lastmidi]
    pad_row(p, 20, 340, [Pad("stutter", "momentary"), Pad("pattern", "cycle", n=4), Pad("drone", "toggle")])
    cc_map(p, [(74, "tempo", 100, 220), (71, "swing", 0, 0.45)], x=600, y=40)
    note_split(p, pad_channel=10, x=600, y=300)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

__all__ = [
    "Control", "ControlHandle", "control", "column", "widget_text",
    "display", "Pad", "PadHandle", "pad_row", "CCMap", "cc_map", "NoteSplit", "note_split",
    "ui_name",
]

KINDS = ("hsl", "vsl", "hradio", "vradio", "tgl", "bng", "nbx")


def ui_name(name: str) -> str:
    """The receive symbol that sets the control ``name``: ``<name>_ui``."""
    return f"{name}_ui"


# --------------------------------------------------------------------------- #
# builder adapter -- Patch.link vs PdPatch.connect
# --------------------------------------------------------------------------- #

def _wire(patch, src, outlet, dst, inlet=0) -> None:
    if hasattr(patch, "link"):
        patch.link(src, outlet, dst, inlet)
    else:
        patch.connect(src, outlet, dst, inlet)


def _is_legacy(patch) -> bool:
    return not hasattr(patch, "link")


def _init(patch, widget, value, x: int, y: int):
    """``[loadbang] -> [value( -> widget``: the widget emits at load."""
    m = patch.msg(str(value), x, y)
    _wire(patch, patch.loadbang(), 0, m, 0)
    _wire(patch, m, 0, widget, 0)
    return m


# --------------------------------------------------------------------------- #
# widgets
# --------------------------------------------------------------------------- #

def widget_text(kind: str, *, receive: str = "empty", lo: float = 0, hi: float = 1, n: int = 4,
                size: int | None = None, color: str | None = None, send: str = "empty") -> str:
    """The IEM GUI object text for ``kind`` with ``receive`` as its receive
    symbol. Parameter lists are Pd 0.56's saved forms (verified to load
    headless); the init flag is 0 -- initialisation is the explicit loadbang
    message, so it is auditable in the file."""
    r, s = receive, send
    if kind == "hsl":
        w = size or 150
        return f"hsl {w} 16 {lo} {hi} 0 0 {s} {r} empty -2 -8 0 10 #fcfcfc #000000 #000000 0 1"
    if kind == "vsl":
        h = size or 128
        return f"vsl 16 {h} {lo} {hi} 0 0 {s} {r} empty 0 -9 0 10 #fcfcfc #000000 #000000 0 1"
    if kind == "hradio":
        c = size or 18
        return f"hradio {c} 1 0 {n} {s} {r} empty 0 -8 0 10 #fcfcfc #000000 #000000 0"
    if kind == "vradio":
        c = size or 18
        return f"vradio {c} 1 0 {n} {s} {r} empty 0 -8 0 10 #fcfcfc #000000 #000000 0"
    if kind == "tgl":
        c = size or 20
        return f"tgl {c} 0 {s} {r} empty 0 -8 0 10 #fcfcfc {color or '#cc4400'} #000000 0 1"
    if kind == "bng":
        c = size or 22
        return f"bng {c} 250 50 0 {s} {r} empty 0 -8 0 10 #fcfcfc {color or '#00aa66'} #000000"
    if kind == "nbx":
        w = size or 5
        return f"nbx {w} 14 {lo} {hi} 0 0 {s} {r} empty 0 -8 0 10 #fcfcfc #000000 #000000 0 256"
    raise ValueError(f"unknown widget kind {kind!r}; one of {KINDS}")


def widget_size(kind: str, *, n: int = 4, size: int | None = None) -> tuple[int, int]:
    """(width, height) in canvas pixels, as drawn by Pd -- for layout."""
    if kind == "hsl":
        return (size or 150), 16
    if kind == "vsl":
        return 16, (size or 128)
    if kind == "hradio":
        return (size or 18) * n, (size or 18)
    if kind == "vradio":
        return (size or 18), (size or 18) * n
    if kind == "tgl":
        return (size or 20), (size or 20)
    if kind == "bng":
        return (size or 22), (size or 22)
    if kind == "nbx":
        return (size or 5) * 7 + 8, 18
    raise ValueError(f"unknown widget kind {kind!r}")


@dataclass
class Control:
    """The spec of one control. ``default`` is the loadbang init value (None
    = no init, e.g. a bang); ``lo``/``hi`` the slider or number range; ``n``
    the number of radio cells; ``size`` the widget size (slider length, cell
    or button size, nbx digits)."""

    name: str
    kind: str
    default: float | None = 0
    label: str | None = None
    lo: float = 0.0
    hi: float = 1.0
    n: int = 4
    size: int | None = None
    color: str | None = None


@dataclass
class ControlHandle:
    name: str
    kind: str
    receive: str                 # <name>_ui
    widget: object               # the GUI box (node / index)
    send: object                 # the [s name] box
    init_msg: object | None      # the loadbang value message, if any
    label: object | None
    x: int
    y: int
    width: int
    height: int


def control(patch, name: str, kind: str, *, x: int, y: int, default: float | None = 0,
            label: str | None = None, lo: float = 0.0, hi: float = 1.0, n: int = 4,
            size: int | None = None, color: str | None = None,
            plumb_dx: tuple[int, int] = (158, 196), label_at: tuple[int, int] | None = None) -> ControlHandle:
    """One control: the widget (receive ``<name>_ui``) at ``(x, y)``, ``[s name]``
    from its outlet, the loadbang init message, and a label comment.

    The plumbing (``[s]`` and the init message) sits ``plumb_dx`` to the right
    of the widget so the panel itself is only things to touch. Sliders and
    radios get their label under them (a slider's own label field renders
    tiny), toggles and bangs to their right.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown control kind {kind!r}; one of {KINDS}")
    spec = Control(name, kind, default, label, lo, hi, n, size, color)
    return _control(patch, spec, x, y, plumb_dx, label_at)


def _control(patch, c: Control, x: int, y: int, plumb_dx, label_at) -> ControlHandle:
    rcv = ui_name(c.name)
    widget = patch.obj(widget_text(c.kind, receive=rcv, lo=c.lo, hi=c.hi, n=c.n, size=c.size, color=c.color), x, y)
    w, h = widget_size(c.kind, n=c.n, size=c.size)
    compact = c.kind in ("tgl", "bng")
    py = y if compact else y - 18                       # plumbing row
    s = patch.obj(f"s {c.name}", x + plumb_dx[0], py)
    _wire(patch, widget, 0, s, 0)
    init_msg = _init(patch, widget, c.default, x + plumb_dx[1], py) if c.default is not None else None
    lbl = None
    if c.label:
        lx, ly = label_at if label_at else ((x + w + 8, y + 2) if compact else (x, y + h + 4))
        lbl = patch.comment(c.label, lx, ly)
    return ControlHandle(c.name, c.kind, rcv, widget, s, init_msg, lbl, x, y, w, h)


def column(patch, x: int, y: int, controls: Sequence[Control | tuple], *, step: int = 58,
           compact_step: int = 40, plumb_dx: tuple[int, int] = (158, 196)) -> list[ControlHandle]:
    """A vertical column of controls from ``(x, y)`` down: sliders / radios /
    number boxes ``step`` apart, toggles and bangs ``compact_step`` apart.
    Items are ``Control`` specs or ``(name, kind, default, label)`` /
    ``(name, kind, default, label, extra)`` tuples where ``extra`` is
    ``(lo, hi)`` for a slider or ``n`` for a radio. Returns the handles."""
    handles: list[ControlHandle] = []
    for item in controls:
        c = _as_control(item)
        handles.append(_control(patch, c, x, y, plumb_dx, None))
        y += compact_step if c.kind in ("tgl", "bng") else step
    return handles


def _as_control(item) -> Control:
    if isinstance(item, Control):
        return item
    name, kind, default, label, *rest = item
    extra = rest[0] if rest else None
    c = Control(name, kind, default, label)
    if kind in ("hsl", "vsl", "nbx") and extra is not None:
        c.lo, c.hi = extra
    elif kind in ("hradio", "vradio") and extra is not None:
        c.n = int(extra)
    return c


# --------------------------------------------------------------------------- #
# read-outs
# --------------------------------------------------------------------------- #

def display(patch, name: str, x: int, y: int, *, width: int = 7, send: str | None = None):
    """A number box that SHOWS whatever is sent to ``name`` (its receive slot).

    Gets the slots right on both builders: ``Patch.floatatom`` takes
    ``receive=``; the frozen ``PdPatch.floatatom`` writes its ``send=`` argument
    into Pd's receive slot (its documented defect), so it is called with
    ``send=name`` here. ``send`` (optional) names the box's own send slot --
    the runtime probe: put a name there, add ``[r thatname] -> [print]``, drive
    the patch, and the console proves the read-out really updates.
    """
    if _is_legacy(patch):
        return patch.floatatom(x, y, recv=(send or "-"), send=name, w=width)
    return patch.floatatom(x, y, receive=name, send=(send or "-"), width=width)


# --------------------------------------------------------------------------- #
# pads: momentary / toggle / cycle, always against the CURRENT value
# --------------------------------------------------------------------------- #

@dataclass
class Pad:
    """A performance pad. ``kind``:

    * ``momentary`` -- a toggle control held while pressed (``name`` is the
      control; hardware sends 1 / 0 to ``<name>_ui``);
    * ``toggle`` -- a bang that flips the control ``name`` (reads ``[r name]``,
      sends ``== 0`` to ``<name>_ui``); fired from ``flip_<name>``;
    * ``cycle`` -- a bang that advances ``name`` through ``n`` values
      (``(current + 1) mod n`` to ``<name>_ui``); fired from ``next_<name>``.
    """

    name: str
    kind: str
    n: int = 2
    label: str | None = None
    color: str | None = None
    trigger: str | None = None

    def trigger_name(self) -> str:
        if self.trigger:
            return self.trigger
        if self.kind == "momentary":
            return ui_name(self.name)
        if self.kind == "toggle":
            return f"flip_{self.name}"
        if self.kind == "cycle":
            return f"next_{self.name}"
        raise ValueError(f"unknown pad kind {self.kind!r}; one of momentary/toggle/cycle")


@dataclass
class PadHandle:
    name: str
    kind: str
    trigger: str                 # the receive that fires the action (bang it, or 1/0 for momentary)
    widget: object
    nodes: list = field(default_factory=list)


def pad_row(patch, x: int, y: int, pads: Sequence[Pad], *, spacing: int = 42,
            plumb_dx: int = 158, logic_at: tuple[int, int] | None = None) -> list[PadHandle]:
    """A horizontal row of pads at ``(x, y)`` (also the on-screen twin of a
    hardware pad bank). Each pad's GUI feeds its trigger send; the flip /
    cycle logic reads the control's CURRENT value via ``[r name]`` so pads and
    panel never disagree. Labels go in one comment under the row. Returns
    handles whose ``trigger`` is the receive a MIDI pad or a test bangs."""
    handles: list[PadHandle] = []
    lx, ly = logic_at if logic_at else (x + plumb_dx, y)
    for i, pad in enumerate(pads):
        px = x + i * spacing
        trig = pad.trigger_name()
        nodes: list = []
        if pad.kind == "momentary":
            widget = patch.obj(widget_text("tgl", receive=ui_name(pad.name), color=pad.color or "#aa00aa"), px, y)
            s = patch.obj(f"s {pad.name}", lx, ly)
            _wire(patch, widget, 0, s, 0)
            nodes += [s, _init(patch, widget, 0, lx + 38, ly)]
        elif pad.kind in ("toggle", "cycle"):
            widget = patch.obj(widget_text("bng", receive=f"{trig}_ui", color=pad.color), px, y)
            s = patch.obj(f"s {trig}", lx, ly)
            _wire(patch, widget, 0, s, 0)
            r_trig = patch.obj(f"r {trig}", lx + 60, ly)
            cur = patch.obj("f", lx + 60, ly + 22)
            r_cur = patch.obj(f"r {pad.name}", lx + 100, ly)
            _wire(patch, r_trig, 0, cur, 0)
            _wire(patch, r_cur, 0, cur, 1)
            if pad.kind == "cycle":
                inc = patch.obj("+ 1", lx + 60, ly + 44)
                mod = patch.obj(f"mod {pad.n}", lx + 60, ly + 66)
                out = patch.obj(f"s {ui_name(pad.name)}", lx + 60, ly + 88)
                _wire(patch, cur, 0, inc, 0)
                _wire(patch, inc, 0, mod, 0)
                _wire(patch, mod, 0, out, 0)
                nodes += [s, r_trig, cur, r_cur, inc, mod, out]
            else:
                flip = patch.obj("== 0", lx + 60, ly + 44)
                out = patch.obj(f"s {ui_name(pad.name)}", lx + 60, ly + 66)
                _wire(patch, cur, 0, flip, 0)
                _wire(patch, flip, 0, out, 0)
                nodes += [s, r_trig, cur, r_cur, flip, out]
        else:
            raise ValueError(f"unknown pad kind {pad.kind!r}; one of momentary/toggle/cycle")
        handles.append(PadHandle(pad.name, pad.kind, trig, widget, nodes))
        ly += 110
    labels = [pad.label for pad in pads if pad.label]
    if labels:
        patch.comment("  ".join(labels), x, y + 26)
    return handles


# --------------------------------------------------------------------------- #
# MIDI: a CC router with a message twin, and channel-split notes
# --------------------------------------------------------------------------- #

@dataclass
class CCMap:
    stream: str                  # the internal (cc value) stream receive
    fake_recv: str               # send "cc value" here to drive the map from a test
    last_cc: tuple[str, str]     # sends carrying the last cc number / value seen
    targets: dict                # name -> (cc, lo, hi)
    nodes: list = field(default_factory=list)


def cc_map(patch, entries: Sequence[tuple], *, x: int, y: int, fake_recv: str = "fakecc",
           stream: str = "ccstream", last_cc: tuple[str, str] = ("lastcc", "lastccval")) -> CCMap:
    """A ``[ctlin]`` router. ``entries`` are ``(cc, name, lo, hi)``: CC ``cc``
    scales 0..127 into ``lo..hi`` and lands on ``<name>_ui`` -- so hardware
    moves the panel control and the engine follows through the widget (one
    source of truth). ``[r fakecc]`` is the twin: ``control.send("fakecc", 74,
    64)`` in a test exercises the whole map except the physical last hop.
    The last CC number / value seen are broadcast (``lastcc`` / ``lastccval``)
    for a LAST CC display, so any knob can be identified for remapping.
    Data-driven: keep the entries in one table at the top of a build script.
    """
    nodes: list = []
    ct = patch.obj("ctlin", x, y)
    pk = patch.obj("pack f f", x, y + 24)
    _wire(patch, ct, 0, pk, 0)                          # value (hot) ...
    _wire(patch, ct, 1, pk, 1)                          # ... cc number (cold, arrives first: right to left)
    sw = patch.msg("$2 $1", x, y + 48)                  # -> "cc value"
    _wire(patch, pk, 0, sw, 0)
    s1 = patch.obj(f"s {stream}", x, y + 72)
    _wire(patch, sw, 0, s1, 0)
    rf = patch.obj(f"r {fake_recv}", x + 90, y + 48)
    s2 = patch.obj(f"s {stream}", x + 90, y + 72)
    _wire(patch, rf, 0, s2, 0)
    rs = patch.obj(f"r {stream}", x, y + 104)
    t = patch.obj("t l l", x, y + 128)
    _wire(patch, rs, 0, t, 0)
    up = patch.obj("unpack f f", x + 160, y + 152)      # right outlet first: the display
    _wire(patch, t, 1, up, 0)
    sl = patch.obj(f"s {last_cc[0]}", x + 160, y + 176)
    sv = patch.obj(f"s {last_cc[1]}", x + 230, y + 176)
    _wire(patch, up, 0, sl, 0)
    _wire(patch, up, 1, sv, 0)
    route = patch.obj("route " + " ".join(str(int(cc)) for cc, *_ in entries), x, y + 152)
    _wire(patch, t, 0, route, 0)
    nodes += [ct, pk, sw, s1, rf, s2, rs, t, up, sl, sv, route]
    targets: dict = {}
    for i, (cc, name, lo, hi) in enumerate(entries):
        cx = x + i * 70
        d = patch.obj("/ 127", cx, y + 200)
        m = patch.obj(f"* {hi - lo}", cx, y + 224)
        a = patch.obj(f"+ {lo}", cx, y + 248)
        s = patch.obj(f"s {ui_name(name)}", cx, y + 272)
        _wire(patch, route, i, d, 0)
        _wire(patch, d, 0, m, 0)
        _wire(patch, m, 0, a, 0)
        _wire(patch, a, 0, s, 0)
        targets[name] = (int(cc), lo, hi)
        nodes += [d, m, a, s]
    return CCMap(stream, fake_recv, last_cc, targets, nodes)


@dataclass
class NoteSplit:
    keys_send: str               # "note velocity" lists from every channel but the pad channel
    pads_send: str               # "note velocity" lists from the pad channel
    notein: object
    nodes: list = field(default_factory=list)


def note_split(patch, *, pad_channel: int = 10, x: int, y: int, keys_send: str = "midi_in",
               pads_send: str = "pad_in") -> NoteSplit:
    """``[notein]`` split by channel into keys and pads, deterministically.

    The three outlets are re-packed ``[pack f f 1]`` (note, velocity, channel)
    and unpacked again, so the channel is read and the gates are set BEFORE
    the note passes -- whatever order the outlets fired in. The trailing
    ``1`` is the channel default, so a source that never sends a channel
    routes to the keys. (pdverify's notein shim fires in real-notein order
    since 0.2.0; this makes the routing independent of it either way.)
    """
    ni = patch.obj("notein", x, y)
    pk = patch.obj("pack f f 1", x, y + 24)
    _wire(patch, ni, 0, pk, 0)
    _wire(patch, ni, 1, pk, 1)
    _wire(patch, ni, 2, pk, 2)
    un = patch.obj("unpack f f f", x, y + 48)
    _wire(patch, pk, 0, un, 0)
    chq = patch.obj(f"== {pad_channel}", x + 120, y + 72)
    _wire(patch, un, 2, chq, 0)
    tf = patch.obj("t f f", x + 120, y + 96)
    _wire(patch, chq, 0, tf, 0)
    pad_gate = patch.obj("spigot", x + 60, y + 144)
    key_gate = patch.obj("spigot", x, y + 144)
    _wire(patch, tf, 1, pad_gate, 1)
    not_pad = patch.obj("== 0", x + 120, y + 120)
    _wire(patch, tf, 0, not_pad, 0)
    _wire(patch, not_pad, 0, key_gate, 1)
    nv = patch.obj("pack f f", x, y + 96)               # note (hot) + velocity (cold, arrives first)
    _wire(patch, un, 0, nv, 0)
    _wire(patch, un, 1, nv, 1)
    _wire(patch, nv, 0, key_gate, 0)
    _wire(patch, nv, 0, pad_gate, 0)
    sk = patch.obj(f"s {keys_send}", x, y + 168)
    sp = patch.obj(f"s {pads_send}", x + 60, y + 168)
    _wire(patch, key_gate, 0, sk, 0)
    _wire(patch, pad_gate, 0, sp, 0)
    return NoteSplit(keys_send, pads_send, ni, [pk, un, chq, tf, pad_gate, key_gate, not_pad, nv, sk, sp])
