"""Selecting and extracting subgraphs of a patch.

`between(patch, sources, sinks)` selects every node on a path from any source
to any sink -- a cut through the signal flow, which is how you actually think
about an audio chain ("everything from the oscillator to the output").

Selection is deliberately separate from extraction. Choosing the seam is the
judgement call; moving the nodes is bookkeeping. Keeping them apart means you
can look at what a cut would take before committing to it.
"""

from __future__ import annotations

import copy
import os
from collections import OrderedDict
from typing import Iterable, Sequence

import re

from py2pd.api import Connection, Obj

from .patch import Patch, object_io

__all__ = [
    "between", "downstream", "upstream", "resolve", "crossing_edges",
    "extract", "extraction_plan", "is_signal_outlet",
]


# Objects ending in ~ whose outlets are ALL control, not signal. From the
# semantics scout, which probed each per-outlet against a live Pd (Pd refuses a
# signal outlet -> control inlet, which makes a reliable oracle).
_ALL_CONTROL_OUTLET_TILDE = {
    "snapshot~", "vsnapshot~", "env~", "bang~", "threshold~",
    "samplerate~", "fiddle~", "sigmund~", "bonk~",
}
# Objects ending in ~ with NO outlet at all -- must never sprout one.
_ZERO_OUTLET_TILDE = {
    "dac~", "print~", "writesf~", "send~", "throw~", "tabwrite~",
    "tabsend~", "block~", "switch~", "delwrite~",
}


def _readsf_channels(args: list[str]) -> int:
    return int(args[0]) if args and args[0].lstrip("-").isdigit() else 1


def is_signal_outlet(text: str, outlet: int) -> bool:
    """Whether the given outlet of an object carries a signal.

    A Pd connection carries signal iff its *source* outlet is a signal outlet,
    which is what decides `inlet~`/`outlet~` vs `inlet`/`outlet` at a cut -- the
    port type is inherited from the source, since Pd forbids a signal outlet
    from reaching a control inlet in the first place.

    Resolved by object class, not the `~` suffix alone: analysis objects
    (`snapshot~`, `env~`, ...) have control outlets, and the mixed objects have
    a control outlet trailing their signal ones.
    """
    parts = text.split()
    if not parts:
        return False
    cls, args = parts[0], parts[1:]
    if not cls.endswith("~"):
        return False
    if cls in _ZERO_OUTLET_TILDE or cls in _ALL_CONTROL_OUTLET_TILDE:
        return False
    if cls == "tabplay~":            # out 0 signal, out 1 control (done-bang)
        return outlet == 0
    if cls == "readsf~":             # outs 0..ch-1 signal, last out control
        return outlet < _readsf_channels(args)
    return True


# --- resolving abstraction/subpatch outlets by their actual port objects ---- #
#
# is_signal_outlet keys off the `~` suffix, which is right for vanilla objects
# but blind to abstraction instances ([acid303]), [clone name n], and [pd sub]:
# their signal outlets have no `~` in the parent's text, so they mis-type as
# control. That is what stops extract() being run on its own output. The ground
# truth is in the referenced abstraction -- its inlet/outlet objects, indexed by
# x-position exactly as Pd indexes the parent box's ports.

_PORT_SIGNAL = {"inlet~": True, "outlet~": True, "inlet": False, "outlet": False}


def _signature_from_ports(items) -> dict:
    """(text, x) of an abstraction's port objects -> per-index signal flags."""
    ins, outs = [], []
    for text, x in items:
        cls = text.split()[0] if text.split() else ""
        if cls in ("inlet", "inlet~"):
            ins.append((x, _PORT_SIGNAL[cls]))
        elif cls in ("outlet", "outlet~"):
            outs.append((x, _PORT_SIGNAL[cls]))
    ins.sort(key=lambda t: t[0])
    outs.sort(key=lambda t: t[0])
    return {"inlets": [s for _x, s in ins], "outlets": [s for _x, s in outs]}


def _abstraction_signature(name: str, search_dirs) -> dict | None:
    """Port signature of ``<name>.pd`` found on ``search_dirs``, or None."""
    from pathlib import Path
    from py2pd import parse_file
    for d in search_dirs:
        if not d:
            continue
        f = Path(d) / f"{name}.pd"
        if f.exists():
            els = parse_file(str(f)).elements
            items = [(getattr(e, "text", ""), getattr(getattr(e, "position", None), "x", 0))
                     for e in els if getattr(e, "text", None) is not None]
            return _signature_from_ports(items)
    return None


def _subpatch_signature(node) -> dict | None:
    """Port signature of an inline ``[pd name]`` subpatch node."""
    src = getattr(node, "src", None)
    if src is None:
        return None
    items = []
    for n in getattr(src, "nodes", []):
        p = getattr(n, "parameters", {}) or {}
        t = p.get("text")
        if t is not None:
            items.append((str(t), p.get("x_pos", 0)))
    return _signature_from_ports(items)


def resolve_signal_outlet(node, outlet: int, search_dirs=()) -> bool:
    """Whether ``node``'s ``outlet`` carries a signal, resolving abstraction
    instances, ``[clone]`` and ``[pd sub]`` against their real port objects.

    Falls back to `is_signal_outlet` for vanilla objects (and for an
    abstraction whose file is not on ``search_dirs`` -- unresolvable, so we
    keep the old conservative answer rather than guess).
    """
    if type(node).__name__ == "Subpatch":
        sig = _subpatch_signature(node)
        if sig is not None and outlet < len(sig["outlets"]):
            return sig["outlets"][outlet]
        return False

    text = node_text(node)
    parts = text.split()
    if not parts:
        return False
    cls = parts[0]

    # [clone <name> n] or [clone -s 1 <name> n]: the outlets are the abstraction's
    name = None
    if cls == "clone":
        for tok in parts[1:]:
            if not tok.startswith("-") and not _is_number(tok):
                name = tok
                break
    else:
        name = cls

    if name:
        sig = _abstraction_signature(name, search_dirs)
        if sig is not None:
            return sig["outlets"][outlet] if outlet < len(sig["outlets"]) else False

    return is_signal_outlet(text, outlet)


# Named resources: connections that cross by NAME, invisible to the #X connect
# graph. `argpos` is where the name sits in the whitespace-split text; role is
# 'w' (writer/allocator), 'r' (reader), or 'rw' (both, e.g. [value]).
_RESOURCE_CLASSES = {
    "s": ("send", "w"), "send": ("send", "w"),
    "r": ("send", "r"), "receive": ("send", "r"),
    "delwrite~": ("delay", "w"),
    "delread~": ("delay", "r"), "delread4~": ("delay", "r"), "vd~": ("delay", "r"),
    # signal buses. Role is FLOW direction (throw~ feeds catch~, send~ feeds
    # receive~); which end allocates is a separate question -- see _ALLOCATORS.
    "throw~": ("sigbus", "w"), "catch~": ("sigbus", "r"),
    "send~": ("sigbus", "w"), "s~": ("sigbus", "w"),
    "receive~": ("sigbus", "r"), "r~": ("sigbus", "r"),
    "tabwrite~": ("table", "w"), "tabsend~": ("table", "w"),
    "tabread~": ("table", "r"), "tabread4~": ("table", "r"),
    "tabreceive~": ("table", "r"), "tabplay~": ("table", "r"),
    "tabosc4~": ("table", "r"),
    # control rate: a step sequencer's [tabread steps] is as much a use of the
    # table as a wavetable's [tabread4~]
    "tabread": ("table", "r"), "tabread4": ("table", "r"), "tabwrite": ("table", "w"),
    "table": ("table", "w"),
    "value": ("value", "rw"), "v": ("value", "rw"),
}
# Which class actually ALLOCATES the named thing. A second one of these with
# the same name is what makes Pd say "multiply defined" -- and it is not always
# the writer: [catch~] allocates the bus that [throw~] merely feeds.
_ALLOCATORS = {
    "delwrite~", "catch~", "send~", "s~", "table",
}
# ...and of the [array <verb>] family only `define` allocates; get/set/size/
# sum/... use a table someone else defined (see _allocates)
# Kinds whose allocation is instance-visible at all. Sends and values are just
# names in a global namespace; sharing them across instances is normal.
_ALLOCATING = {"delay", "sigbus", "table"}
# Names of these kinds are never namespaced: a table is usually backed by an
# `#X array` or a graph we cannot rewrite, so renaming the reader alone would
# point it at a table that does not exist.
_NEVER_NAMESPACE = {"table"}


def _resource_uses(text: str) -> list[tuple[int, str, str, str]]:
    """Named resources an OBJECT BOX touches: (argpos, name, kind, role).

    Text-based and therefore renameable. Non-object uses (message sends, GUI
    send/receive symbols) are found by `_node_resources`, which marks them
    unrenameable so their names are never namespaced.
    """
    parts = text.split()
    if len(parts) < 2:
        return []
    # [array define foo] / [array get foo] put the name one token further along;
    # define and set write, size can set a size, the rest (get, sum, ...) read
    if parts[0] == "array" and len(parts) > 2:
        role = {"define": "w", "set": "w", "size": "rw"}.get(parts[1], "r")
        return [(2, parts[2], "table", role)]
    cls = parts[0]
    spec = _RESOURCE_CLASSES.get(cls)
    if spec is None:
        return []
    kind, role = spec
    name = parts[1]
    if not name:
        return []
    # A purely numeric argument is a count or a delay time, not a name --
    # but "3voices" is a legal Pd symbol and must still be tracked.
    if _is_number(name):
        return []
    return [(1, name, kind, role)]


def _allocates(text: str) -> bool:
    """Does this box allocate the name it carries? [array get foo] does not:
    counting every [array ...] as an allocator made two readers of one table
    look like two definitions of it."""
    parts = text.split()
    if not parts:
        return False
    if parts[0] == "array":
        return len(parts) > 1 and parts[1] == "define"
    return parts[0] in _ALLOCATORS


def _is_number(tok: str) -> bool:
    try:
        float(tok)
    except ValueError:
        return False
    return True


def _msg_send_targets(content: str) -> list[str]:
    """Receivers a message box sends to via `; name ...`.

    In the file a leading semicolon is escaped, so the content reads
    `\\; cutoff 500`. Each segment after a `;` starts with the receiver name.
    Missing these would let a name look internal-only when the parent is in
    fact driving it, and namespacing would then silently cut the wire.
    """
    out = []
    parts = re.split(r"\\?;", content)
    for seg in parts[1:]:
        toks = seg.split()
        if toks and not toks[0][0].isdigit():
            out.append(toks[0])
    return out


def _node_resources(node) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """All named resources a node touches, split by whether we can rename them.

    Returns `(renameable, fixed)`, each a list of `(name, kind, role)`.
    `fixed` entries pin a name as un-namespaceable: we can see the reference
    but cannot rewrite it, so renaming the other end would break the link.
    """
    renameable: list[tuple[str, str, str]] = []
    fixed: list[tuple[str, str, str]] = []

    params = getattr(node, "parameters", {}) or {}
    text = params.get("text")
    if text is not None:
        text = str(text)
        for _p, name, kind, role in _resource_uses(text):
            renameable.append((name, kind, role))
        # Message boxes keep their body under the same 'text' key, so scan any
        # box for `; name ...` sends. An object box has no semicolon, so this
        # costs nothing and cannot misfire.
        for name in _msg_send_targets(text):
            fixed.append((name, "send", "w"))

    # GUI widgets and atom boxes carry send/receive symbols as parameters
    for key, role in (("send", "w"), ("receive", "r")):
        sym = params.get(key)
        if isinstance(sym, str) and sym not in ("", "-", "empty"):
            fixed.append((sym, "send", role))

    # An array declares a table name that tabread~/tabwrite~ reference. py2pd
    # models it as its own node type (and Patch.graph's Graph holds one). Match
    # on the type, not on the presence of a `name` parameter -- a [pd foo]
    # subpatch has one too, and treating that as a table produced bogus
    # collision warnings.
    if _declares_array(node):
        arr = params.get("name")
        if isinstance(arr, str) and arr:
            fixed.append((arr, "table", "rw"))

    return renameable, fixed


def _declares_array(node) -> bool:
    """A bare ``#X array`` (py2pd's Array) or a graph-on-parent array
    (``Patch.graph``): either allocates the table its ``name`` parameter names."""
    return type(node).__name__ in ("Array", "Graph")


def node_text(node) -> str:
    """The box's text, for objects and messages that have one."""
    params = getattr(node, "parameters", {}) or {}
    for key in ("text", "content"):
        if key in params:
            return str(params[key])
    return repr(node)


def _index_map(patch) -> dict[int, int]:
    """id(node) -> index. Identity, not equality: two `[+ 1]` boxes are
    distinct nodes that may well compare equal."""
    return {id(n): i for i, n in enumerate(patch.pd.nodes)}


def resolve(patch, selector) -> list[int]:
    """Turn a selector into node indices.

    Accepts a node object, an exact box text, or a `re.Pattern` matched
    against box text. A string that matches nothing raises -- a silent empty
    selection would produce a silently empty extraction.
    """
    idx = _index_map(patch)

    if id(selector) in idx:
        return [idx[id(selector)]]

    if isinstance(selector, re.Pattern):
        hits = [i for i, n in enumerate(patch.pd.nodes)
                if selector.search(node_text(n))]
        if not hits:
            raise ValueError(f"pattern {selector.pattern!r} matched no node")
        return hits

    if isinstance(selector, str):
        hits = [i for i, n in enumerate(patch.pd.nodes)
                if node_text(n) == selector]
        if not hits:
            near = [node_text(n) for n in patch.pd.nodes
                    if selector.split()[0] in node_text(n)][:5]
            hint = f" Did you mean one of {near}?" if near else ""
            raise ValueError(f"no node with text {selector!r}.{hint}")
        return hits

    raise TypeError(f"cannot resolve selector of type {type(selector).__name__}")


def _resolve_all(patch, selectors) -> set[int]:
    if selectors is None:
        return set()
    if isinstance(selectors, (str, re.Pattern)) or not isinstance(selectors, Iterable):
        selectors = [selectors]
    out: set[int] = set()
    for s in selectors:
        out.update(resolve(patch, s))
    return out


def _signal_buffer_edges(patch) -> list[tuple[int, int]]:
    """Implicit writer->reader edges for signal buffers that carry audio by
    name rather than by cord: `delwrite~`/`delread~`/`vd~` and `throw~`/`catch~`.

    A delay line is part of the signal flow, so following it lets a path search
    reach a `delread~` taproot that has no explicit input -- otherwise the delay
    is silently split across a cut.
    """
    writers: dict[str, list[int]] = {}
    readers: dict[str, list[int]] = {}
    for i, n in enumerate(patch.pd.nodes):
        for _pos_i, name, kind, role in _resource_uses(node_text(n)):
            if kind not in ("delay", "sigbus"):
                continue
            if role in ("w", "rw"):
                writers.setdefault(name, []).append(i)
            if role in ("r", "rw"):
                readers.setdefault(name, []).append(i)
    edges = []
    for name, ws in writers.items():
        for w in ws:
            for r in readers.get(name, []):
                edges.append((w, r))
    return edges


def _adjacency(patch, follow_buffers: bool = True
               ) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """(forward, backward) index -> neighbouring indices."""
    fwd: dict[int, set[int]] = {}
    bwd: dict[int, set[int]] = {}
    for c in patch.pd.connections:
        a, b = c.source, c.sink
        fwd.setdefault(a, set()).add(b)
        bwd.setdefault(b, set()).add(a)
    if follow_buffers:
        for a, b in _signal_buffer_edges(patch):
            fwd.setdefault(a, set()).add(b)
            bwd.setdefault(b, set()).add(a)
    return fwd, bwd


def _reachable(adj: dict[int, set[int]], seeds: set[int]) -> set[int]:
    seen = set(seeds)
    stack = list(seeds)
    while stack:
        for nxt in adj.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def downstream(patch, sources, *, follow_buffers: bool = True) -> list:
    """Every node reachable by following connections forward from `sources`."""
    fwd, _ = _adjacency(patch, follow_buffers)
    hits = _reachable(fwd, _resolve_all(patch, sources))
    return [patch.pd.nodes[i] for i in sorted(hits)]


def upstream(patch, sinks, *, follow_buffers: bool = True) -> list:
    """Every node that can reach `sinks` by following connections forward."""
    _, bwd = _adjacency(patch, follow_buffers)
    hits = _reachable(bwd, _resolve_all(patch, sinks))
    return [patch.pd.nodes[i] for i in sorted(hits)]


def between(patch, sources, sinks, *, include_sources: bool = True,
            include_sinks: bool = True, follow_buffers: bool = True) -> list:
    """Nodes lying on any path from any source to any sink.

    The intersection of "reachable forward from a source" and "reaches a
    sink". A node hanging off a source that never reaches a sink is *not*
    on a path and is left behind -- which is the point: it keeps a cut from
    dragging in whatever happens to be downstream of the same oscillator.

    Delay lines and `throw~`/`catch~` are followed by default (`follow_buffers`)
    so a cut through the signal chain also takes the delay taps that carry it --
    without that, a `delread~` with no explicit input falls outside the path and
    the delay is split across the boundary.

    Endpoints are included by default. Excluding them gives the strict
    interior, for when the endpoints should stay on the parent.
    """
    fwd, bwd = _adjacency(patch, follow_buffers)
    src = _resolve_all(patch, sources)
    snk = _resolve_all(patch, sinks)

    selected = _reachable(fwd, src) & _reachable(bwd, snk)
    if not include_sources:
        selected -= src
    if not include_sinks:
        selected -= snk
    return [patch.pd.nodes[i] for i in sorted(selected)]


def crossing_edges(patch, nodes: Sequence) -> dict[str, list[tuple]]:
    """Classify every connection against a proposed cut.

    Returns `internal` / `inbound` / `outbound` / `external`, each a list of
    `(source_node, outlet, sink_node, inlet)`. Inspect this before extracting:
    the inbound and outbound lists become the abstraction's inlets and
    outlets, so they *are* its public interface.
    """
    idx = _index_map(patch)
    inside = {idx[id(n)] for n in nodes}
    out: dict[str, list[tuple]] = {
        "internal": [], "inbound": [], "outbound": [], "external": [],
    }
    for c in patch.pd.connections:
        a, b = c.source, c.sink
        edge = (patch.pd.nodes[a], c.outlet_index,
                patch.pd.nodes[b], c.inlet_index)
        if a in inside and b in inside:
            out["internal"].append(edge)
        elif b in inside:
            out["inbound"].append(edge)
        elif a in inside:
            out["outbound"].append(edge)
        else:
            out["external"].append(edge)
    return out


def _pos(node, axis, default):
    return node.parameters.get(axis, default)


_PORT_CLASSES = ("inlet", "inlet~", "outlet", "outlet~")


def _rename_in_text(text: str, rename: dict[str, str]) -> str:
    """Prefix the named-resource argument of a resource object, in place."""
    toks = text.split()
    for pos, name, _kind, _role in _resource_uses(text):
        if name in rename:
            toks[pos] = rename[name]
    return " ".join(toks)


def _canonical_region(patch, region) -> list:
    """De-duplicate a region and put it in patch order.

    Two things depend on this. A node named twice would otherwise be copied
    into the abstraction twice, orphaning one copy and re-allocating any name
    it owns. And because the copy order decides the order internal connections
    are emitted, an unordered region made the output depend on the order the
    caller happened to list it in -- the same node set, reversed, produced a
    different-sounding patch.
    """
    index = {id(n): i for i, n in enumerate(patch.pd.nodes)}
    seen: dict[int, object] = {}
    for n in region:
        if id(n) not in index:
            raise ValueError("region contains nodes that are not in the patch")
        seen.setdefault(id(n), n)
    return [n for _i, n in sorted(
        ((index[id(n)], n) for n in seen.values()), key=lambda p: p[0])]


def _region_port_boxes(region) -> list[str]:
    """Existing inlet/outlet boxes inside a proposed region.

    Pd indexes an abstraction's ports by the x-position of *every* inlet/outlet
    box on its canvas. A box that was already in the region arrives at its
    original x and joins that sort, shifting the generated ports' indices --
    which silently rewires the interface and produces no Pd error at all.
    """
    found = []
    for n in region:
        txt = node_text(n).split()
        if txt and txt[0] in _PORT_CLASSES:
            found.append(node_text(n))
    return found


def extraction_plan(patch, region, *, broadcast: Sequence[str] = ()) -> dict:
    """Describe what extracting `region` would do, without changing anything.

    Returns the boundary classification plus how every named resource
    (send/receive, delay line, table) is handled:

    - `namespace`: names used only inside the region -> rewritten to `$0-name`
      so a second instance gets its own. This is what makes an extracted engine
      polyphony-safe; an un-namespaced delay line is exactly why the
      hand-authored acid303 cannot be instantiated twice.
    - `shared`: names that also appear outside the region, or are listed in
      `broadcast` -- left global, so instances share them (a common clock, a
      control surface's sends). This is the rig's own pattern: zero-inlet
      engines driven by global receives.
    - `warnings`: hazards worth surfacing, chiefly an allocating resource
      (delay/table/throw~) written inside but split across the cut, which will
      collide across instances because it cannot be namespaced safely.

    Inspect this before committing, the same way `crossing_edges` lets you look
    at a cut before taking it.
    """
    region = _canonical_region(patch, region)
    region_ids = {id(n) for n in region}
    broadcast = set(broadcast)

    uses: "OrderedDict[str, dict]" = OrderedDict()

    def _record(rname, kind, role, inside, renameable, allocates):
        rec = uses.setdefault(rname, {
            "kind": kind, "iw": 0, "ir": 0, "ow": 0, "or": 0,
            "fixed": False, "alloc_inside": 0})
        if kind in _ALLOCATING:          # an allocating kind wins the label
            rec["kind"] = kind
        side = "i" if inside else "o"
        if role in ("w", "rw"):
            rec[side + "w"] += 1
        if role in ("r", "rw"):
            rec[side + "r"] += 1
        if not renameable:
            rec["fixed"] = True
        if allocates and inside:
            rec["alloc_inside"] += 1

    for n in patch.pd.nodes:
        inside = id(n) in region_ids
        allocates = _allocates(node_text(n))
        renameable, fixed = _node_resources(n)
        for rname, kind, role in renameable:
            _record(rname, kind, role, inside, True, allocates)
        for rname, kind, role in fixed:
            _record(rname, kind, role, inside, False, _declares_array(n))

    namespace: dict[str, str] = {}
    shared: dict[str, str] = {}
    warnings: list[str] = []
    for rname, rec in uses.items():
        touches_inside = rec["iw"] or rec["ir"]
        if not touches_inside:
            continue                     # purely external -- not our concern
        touches_outside = rec["ow"] or rec["or"]

        allocated_inside = rec["alloc_inside"] > 0

        if rname in broadcast:
            shared[rname] = rec["kind"]
        elif touches_outside:
            shared[rname] = rec["kind"]
            if allocated_inside and rname not in broadcast:
                warnings.append(
                    f"'{rname}' ({rec['kind']}) is allocated inside the region "
                    f"but the name is also used outside it, so it stays global; "
                    f"two instances of '{{name}}' will collide on it. Move the "
                    f"whole {rec['kind']} inside the cut, or accept a single "
                    f"shared instance.")
        elif rec["fixed"] or rec["kind"] in _NEVER_NAMESPACE or "$0" in rname:
            # Un-rewritable: seen through a message send, a GUI send symbol or
            # an array declaration; a table, whose backing array we cannot
            # rename; or a name that is already instance-local. Renaming only
            # the ends we can reach would break the link silently.
            shared[rname] = rec["kind"]
            if allocated_inside and "$0" not in rname:
                warnings.append(
                    f"'{rname}' ({rec['kind']}) is allocated inside the region "
                    f"but cannot be safely renamed (it is a table, or is "
                    f"referenced by a message send, GUI send symbol or array), "
                    f"so it stays global; two instances of '{{name}}' will "
                    f"collide on it.")
        else:
            namespace[rname] = rec["kind"]

    edges = crossing_edges(patch, region)
    return {
        "internal": edges["internal"], "inbound": edges["inbound"],
        "outbound": edges["outbound"], "external": edges["external"],
        "namespace": namespace, "shared": shared, "warnings": warnings,
    }


def extract(patch, region, name, out_dir, *, doc: str = "", font: int = 10,
            instance_pos=None, broadcast: Sequence[str] = (), on_warning="warn",
            duplicate: Sequence[str] = ("loadbang",), search_dirs: Sequence = ()):
    """Pull `region` out of `patch` into a sibling abstraction `<name>.pd`.

    Writes the abstraction to `out_dir`, rewrites `patch` in place to replace
    the region with a single `[<name>]` box, and returns that box.

    The rewrite is a rebuild, not a mutation: fresh node and connection lists
    with a fresh index map. py2pd connections are raw indices into the node
    list and nothing renumbers them, so editing the list in place silently
    misroutes cords -- rebuilding sidesteps that entirely.

    Named resources are handled per `extraction_plan`: fully-internal
    send/receive, delay-line and table names are namespaced to `$0-name` so the
    abstraction is polyphony-safe, while names shared with the parent (or listed
    in `broadcast`) stay global. `on_warning` is 'warn' (default), 'raise', or
    'ignore'.

    A duplicable source that straddles the cut (a `[loadbang]` initialising
    both sides, per `duplicate`) is copied to each side rather than wired
    through a port, so the engine self-initialises and the parent keeps its own.

    Signal/control port typing is inherited from the source outlet. Vanilla
    objects resolve by class; abstraction instances, `[clone]` and `[pd sub]`
    resolve against their real port objects, so `extract()` composes on its own
    output -- `search_dirs` (plus `out_dir` and a loaded patch's `source_dir`)
    is where the referenced `<name>.pd` files are found. Boundary send/receive
    are kept global rather than promoted to inlets -- the rig's own idiom.

    CAUTION -- the `name` must not collide with an object already on Pd's search
    path. `[voice]`, `[voices]`, and many ELSE names are real objects; an
    abstraction that shadows one **silently never loads** (Pd prefers the object
    it already knows) and the patch renders wrong with no error. There is no way
    to check this without running Pd, so pdbuild cannot warn here -- verify the
    name against a live Pd (the render step catches it: the extracted patch will
    not match the original).
    """
    if not list(region):
        raise ValueError("cannot extract an empty region")
    region = _canonical_region(patch, region)
    region_ids = {id(n) for n in region}
    parent_index = {id(n): i for i, n in enumerate(patch.pd.nodes)}

    # Where to find the .pd of any abstraction the patch instantiates, so its
    # outlet types resolve: the caller's dirs, the dir we write into, and the
    # dir a loaded patch came from.
    eff_dirs = list(search_dirs) + [out_dir]
    src_dir = getattr(patch, "source_dir", None)
    if src_dir:
        eff_dirs.append(src_dir)

    def _sig(node, outlet) -> bool:
        return resolve_signal_outlet(node, outlet, eff_dirs)

    existing_ports = _region_port_boxes(region)
    if existing_ports:
        raise ValueError(
            f"region already contains port boxes {existing_ports}. Pd indexes "
            f"an abstraction's ports by the x-position of every inlet/outlet "
            f"box on its canvas, so these would join the sort and shift the "
            f"generated ports -- silently rewiring the interface with no error. "
            f"Exclude them from the region, or re-cut so the existing ports "
            f"stay on the parent.")

    plan = extraction_plan(patch, region, broadcast=broadcast)
    if plan["warnings"]:
        msgs = [w.replace("{name}", name) for w in plan["warnings"]]
        if on_warning == "raise":
            raise ValueError("; ".join(msgs))
        elif on_warning == "warn":
            import warnings as _w
            for m in msgs:
                _w.warn(m, stacklevel=2)
    rename = {n: f"$0-{n}" for n in plan["namespace"]}

    # Classify boundaries on the ORIGINAL indices, before any rebuild.
    edges = crossing_edges(patch, region)

    # Duplicable sources -- objects with no inlets that fire the same thing on
    # every instance, [loadbang] above all. When one straddles the cut we copy
    # it to the other side instead of threading a port through, so the engine
    # keeps its own init and the parent keeps its own. A copy is behaviourally
    # identical precisely because the object takes no input.
    dup_set = set(duplicate)

    def _dupable(node) -> bool:
        t = node_text(node).split()
        return bool(t) and t[0] in dup_set

    # region nodes that drive the parent, and parent nodes that drive the region
    dup_out = {id(s) for s, _o, _k, _i in edges["outbound"] if _dupable(s)}
    dup_in = {id(s) for s, _o, _k, _i in edges["inbound"] if _dupable(s)}

    # ---- channels: dedup crossings into logical ports -------------------- #
    inbound: "OrderedDict[tuple, dict]" = OrderedDict()
    for src, outlet, snk, inlet in edges["inbound"]:
        if id(src) in dup_in:
            continue                              # handled by a copy inside
        key = (id(src), outlet)
        g = inbound.setdefault(key, {
            "src": src, "outlet": outlet, "targets": [],
            "signal": _sig(src, outlet),
        })
        g["targets"].append((snk, inlet))

    outbound: "OrderedDict[tuple, dict]" = OrderedDict()
    for src, outlet, snk, inlet in edges["outbound"]:
        if id(src) in dup_out:
            continue                              # handled by a copy on the parent
        key = (id(src), outlet)
        g = outbound.setdefault(key, {
            "src": src, "outlet": outlet, "sinks": [],
            "signal": _sig(src, outlet),
        })
        g["sinks"].append((snk, inlet))

    # Port index is assigned by x-position, left-to-right (Pd re-sorts inlet
    # objects by x -- file order is irrelevant). We therefore choose the order
    # ourselves and lay the ports out at that x. Signal ports first, then by
    # the endpoint's original parent index, so extraction is deterministic.
    def order_key(g):
        return (0 if g["signal"] else 1, parent_index[id(g["src"])], g["outlet"])

    in_ports = sorted(inbound.values(), key=order_key)
    out_ports = sorted(outbound.values(), key=order_key)
    for i, g in enumerate(in_ports):
        g["ordinal"] = i
    for j, g in enumerate(out_ports):
        g["ordinal"] = j

    # ---- build the abstraction ------------------------------------------ #
    abst = Patch(font=font)
    abst.pd.nodes = []
    abst.pd.connections = []
    abst.comment(f"{name} - {doc}" if doc else name, 20, 10)

    abs_index: dict[int, int] = {}
    for n in region:
        txt = node_text(n)
        # Only an object box may be rebuilt as an Obj. A comment or message
        # whose first word happens to be a resource class ("s ...", "table ...")
        # would otherwise be silently promoted from #X text to #X obj.
        needs_rename = (
            rename and isinstance(n, Obj)
            and any(rn in rename for _p, rn, _k, _r in _resource_uses(txt)))
        if needs_rename:
            # rebuild the box so escape() runs on the new "$0-name" ($0 must
            # emit as \$0, which Obj.__init__ handles; a raw text edit would not)
            renamed = _rename_in_text(txt, rename)
            new = Obj(_pos(n, "x_pos", 20), _pos(n, "y_pos", 40), renamed)
            io = object_io(renamed)
            if io is not None:
                new.num_inlets, new.num_outlets = io
            abst.pd.nodes.append(new)
        else:
            abst.pd.nodes.append(copy.deepcopy(n))
        abs_index[id(n)] = len(abst.pd.nodes) - 1

    # Port bands clear the body's bounding box, measured over the copied
    # region only (not the header comment). Coordinates MUST stay >= 0: py2pd
    # treats a negative x or y as "not absolute" and silently auto-places the
    # box, which would scramble the x-order that decides port index.
    region_ys = [_pos(abst.pd.nodes[i], "y_pos", 40) for i in abs_index.values()]
    top_y = max(2, (min(region_ys) if region_ys else 45) - 45)
    bot_y = (max(region_ys) if region_ys else 400) + 45

    for g in in_ports:
        cls = "inlet~" if g["signal"] else "inlet"
        abst.obj(cls, 40 + 160 * g["ordinal"], top_y)
        g["port_idx"] = len(abst.pd.nodes) - 1
    for g in out_ports:
        cls = "outlet~" if g["signal"] else "outlet"
        abst.obj(cls, 40 + 160 * g["ordinal"], bot_y)
        g["port_idx"] = len(abst.pd.nodes) - 1

    # A parent-side duplicable source (a [loadbang] feeding into the region)
    # gets a private copy inside the abstraction, so the engine self-initialises.
    dup_in_abs: dict[int, int] = {}
    for did in dup_in:
        src = patch.pd.nodes[parent_index[did]]
        abst.pd.nodes.append(copy.deepcopy(src))
        dup_in_abs[did] = len(abst.pd.nodes) - 1

    # Emit the abstraction's cords in the ORIGINAL connection order. Pd's
    # fan-out order from one outlet follows connection order, so grouping them
    # by category (internal first, then ports) silently reorders a fan-out and
    # can change what the patch sounds like.
    emitted_out: set = set()
    for c in patch.pd.connections:
        src, snk = patch.pd.nodes[c.source], patch.pd.nodes[c.sink]
        s_in, k_in = id(src) in region_ids, id(snk) in region_ids
        if s_in and k_in:
            abst.pd.connections.append(Connection(
                abs_index[id(src)], c.outlet_index,
                abs_index[id(snk)], c.inlet_index))
        elif k_in and id(src) in dup_in:             # from the inside copy
            abst.pd.connections.append(Connection(
                dup_in_abs[id(src)], c.outlet_index,
                abs_index[id(snk)], c.inlet_index))
        elif k_in:                                   # inbound -> from an inlet
            g = inbound[(id(src), c.outlet_index)]
            abst.pd.connections.append(Connection(
                g["port_idx"], 0, abs_index[id(snk)], c.inlet_index))
        elif s_in and id(src) in dup_out:            # its parent targets live on the parent
            continue
        elif s_in:                                   # outbound -> to an outlet
            key = (id(src), c.outlet_index)
            if key not in emitted_out:               # one cord per outlet port
                emitted_out.add(key)
                g = outbound[key]
                abst.pd.connections.append(Connection(
                    abs_index[id(src)], c.outlet_index, g["port_idx"], 0))

    # canvas sized to bound body + bands
    xs = [_pos(n, "x_pos", 20) for n in abst.pd.nodes]
    ys = [_pos(n, "y_pos", 40) for n in abst.pd.nodes]
    width = max(max(xs) + 120 if xs else 400, 400)
    height = max(max(ys) + 60 if ys else 300, 200)
    abst.pd.canvas = (20, 20, width, height, font)

    os.makedirs(out_dir, exist_ok=True)
    abst.save(os.path.join(out_dir, name + ".pd"))

    # ---- rebuild the parent --------------------------------------------- #
    if instance_pos is None:
        rx = int(sum(_pos(n, "x_pos", 20) for n in region) / len(region))
        ry = int(sum(_pos(n, "y_pos", 40) for n in region) / len(region))
    else:
        rx, ry = instance_pos

    new_nodes: list = []
    new_index: dict[int, int] = {}
    for n in patch.pd.nodes:
        if id(n) in region_ids:
            continue
        new_index[id(n)] = len(new_nodes)
        new_nodes.append(n)

    inst = Obj(rx, ry, name, num_inlets=len(in_ports), num_outlets=len(out_ports))
    inst_idx = len(new_nodes)
    new_nodes.append(inst)

    # A region-side duplicable source (the engine's [loadbang] that also drives
    # parent init) gets a private copy on the parent, placed near the instance.
    dup_out_parent: dict[int, int] = {}
    for k, did in enumerate(dup_out):
        orig = patch.pd.nodes[parent_index[did]]
        clone = Obj(rx + 40, ry + 26 * (k + 1), node_text(orig))
        dup_out_parent[did] = len(new_nodes)
        new_nodes.append(clone)

    # Same rule on the parent: walk the original connection list in order so a
    # fan-out that straddles the cut keeps its relative ordering.
    new_conns: list = []
    emitted_in: set = set()
    for c in patch.pd.connections:
        src, snk = patch.pd.nodes[c.source], patch.pd.nodes[c.sink]
        s_in, k_in = id(src) in region_ids, id(snk) in region_ids
        if s_in and k_in:
            continue                                 # moved into the abstraction
        if not s_in and not k_in:
            new_conns.append(Connection(
                new_index[id(src)], c.outlet_index,
                new_index[id(snk)], c.inlet_index))
        elif k_in and id(src) in dup_in:             # inside copy drives it now
            continue
        elif k_in:                                   # inbound -> instance inlet
            key = (id(src), c.outlet_index)
            if key not in emitted_in:                # one cord per inlet port
                emitted_in.add(key)
                new_conns.append(Connection(
                    new_index[id(src)], c.outlet_index,
                    inst_idx, inbound[key]["ordinal"]))
        elif id(src) in dup_out:                     # from the parent copy
            new_conns.append(Connection(
                dup_out_parent[id(src)], c.outlet_index,
                new_index[id(snk)], c.inlet_index))
        else:                                        # outbound -> instance outlet
            g = outbound[(id(src), c.outlet_index)]
            new_conns.append(Connection(
                inst_idx, g["ordinal"], new_index[id(snk)], c.inlet_index))

    patch.pd.nodes = new_nodes
    patch.pd.connections = new_conns

    # safety net -- the library provides none for hand-built connection lists
    for c in patch.pd.connections:
        assert 0 <= c.source < len(new_nodes) and 0 <= c.sink < len(new_nodes), \
            "extract produced an out-of-range connection index"

    return inst
