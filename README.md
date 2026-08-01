# pdbuild

Authoring Pure Data patches for instruments, from Python.

pdbuild is the **construction** half of our Pd toolchain;
[pdverify](../pdverify/) is the **perception** half. Together: build a patch,
render it headless, hear what it actually does, fix it, repeat.

- **Field notes:** [COOKBOOK.md](COOKBOOK.md) — everything we've learned about
  generating `.pd` files, and every gotcha that cost us debugging time.
- **Where it's going:** [../ROADMAP.md](../ROADMAP.md)

## Why it exists

Connections in a `.pd` file are declared by **object index**
(`#X connect 12 0 13 1`), so inserting one object silently renumbers everything
after it. That bookkeeping is the single biggest failure mode of machine-written
Pd. (It matches the published finding that *metaprogramming* beats direct patch
emission for LLM-generated audio patches — arXiv:2409.00856.)

**[py2pd](https://github.com/shakfu/py2pd) already solves that**, along with
parsing, round-tripping and connection validation — so we stopped maintaining
our own emitter. We verified the swap rather than assuming it: the four-file
`acid_set` rig was rebuilt on py2pd and A/B'd against the original with
pdverify, and came out **identical** (similarity 1.000, peaks within 0.00 dB).

What's left is what a general format library has no reason to carry: **Pd
practice**. That's this package.

```
pdbuild.Patch    Pd idioms      cursor layout, init(), object I/O table
      |
    py2pd        format layer   emission, indices, parsing, validation
```

## Install & test

```console
$ pip install -e ./pdbuild
$ python -m pytest pdbuild/tests -q    # 50 tests, <1s
```

The suite asserts against **rendered `.pd` text**, because emitting correct text
is the whole job. Several tests are labelled `REGRESSION` — they pin bugs that
actually shipped into an instrument.

## Quickstart

```python
from pdbuild import Patch

p = Patch()
osc  = p.obj("osc~ 440")
gain = p.obj("*~ 0.2")
dac  = p.obj("dac~")
p.chain(osc, gain)             # outlet 0 -> inlet 0
p.link(gain, 0, dac, 0)
p.link(gain, 0, dac, 1)
p.save("sine.pd")
```

Then verify it actually makes the sound you intended:

```python
from pdverify import analyze, render
print(analyze(render("sine.pd").audio).summary())
# "A warm pure tone, pitched near A4."
```

## API

`Patch` holds a py2pd `Patcher` at `.pd` — reach through it for anything py2pd
already does well (GUI constructors, arrays, subpatches, SVG, round-tripping).

| Method | What it does |
|---|---|
| `Patch(w, h, font, x=, y=)` | New canvas, with real geometry. |
| `obj(text, x=None, y=None)` | Object box, with inlet/outlet counts declared where we know them so validation fires. |
| `msg(text, x=None, y=None)` | Message box. `,` and `;` escaped for you. |
| `comment(text, x, y)` | Comment. **Takes a connection index** like any box. |
| `floatatom(x, y, send=, receive=, width=)` | Number box. `send` and `receive` are **distinct slots**. |
| `abstraction(name, inlets=, outlets=)` | An instance of a sibling `<name>.pd`. |
| `link(src, outlet, sink, inlet)` | Wire two boxes, reading left to right. |
| `chain(*nodes)` | Wire outlet 0 → inlet 0 down a run. |
| `loadbang()` | The patch's shared `[loadbang]`, created on first use. |
| `init(target, value)` | Wire `loadbang → [value( → target`. **Use for every GUI control something depends on.** |
| `init_all({target: value, …})` | Bulk version, one shared loadbang. |
| `cursor(x, y)` | Move the placement cursor. |
| `unvalidated()` | Objects with no declared I/O — their connections are unchecked. |
| `validate()` | py2pd's connection check. |
| `render()` / `save(path)` | Emit the `.pd`. |

Box methods return **node handles**. Pass them straight to `link`; you never
compute an index yourself, which is the whole point.

### `init()` — the one that prevents silent patches

A `tgl`, `hradio`, `hsl` or `nbx` sends **nothing** until a human touches it, so
anything downstream (a gain `line~`, a `spigot` gate) sits at 0 and the patch is
silent — with no error. This killed a rig twice in one build.

```python
tgl   = p.pd.add_toggle(x_pos=30, y_pos=80)
radio = p.pd.add_hradio(x_pos=30, y_pos=322, number=4)
p.init_all({tgl: 1, radio: 0})     # they now emit at load
```

### `floatatom()` — send and receive are not interchangeable

`send` makes typing in the box *do* something; `receive` makes it *display*
what is sent to that name. Crossing them yields a box that looks wired and is
inert — and **no headless render will catch it**, because a render never types
into a box. Both the legacy emitter and py2pd had this bug; the acid rig's BPM
control never worked because of it.

### `unvalidated()` — know what isn't being checked

py2pd validates a connection only when it knows the sink's inlet count, and it
returns `None` for objects it doesn't know — which was most of the DSP objects
we lean on. `Patch` declares counts for those (`bob~`, `rev3~`, `else/pad`,
`vline~`, `pack`, `expr`, …), and `unvalidated()` reports anything still
unchecked. Print it after a build.

### Layout

Omit `x, y` and boxes flow down cursor columns. Give **explicit** positions to
anything a human touches (pads, toggles, sliders), then `cursor()` past that
zone — otherwise the generated guts land on top of the controls. (Learned the
hard way: the X-Y pad ended up buried under a column of objects.)

## Extracting an abstraction

Build something that works, *then* find the seam. `between()` selects the nodes
on any path between two points — how you actually think about a signal chain —
and `extract()` moves them into a sibling `.pd`, generating the interface at the
boundary and rewriting the parent.

```python
from pdbuild import Patch, between, extraction_plan, extract

patch = Patch.load("instruments/outrun_303.pd")  # or build one in-process
region = between(patch, patch.gain, patch.mix)   # everything from gain to mix
print(extraction_plan(patch, region))            # look before you commit
extract(patch, region, "echofx", "instruments/")
```

The parent becomes `[osc~ 110] → [echofx] → [dac~]`; `echofx.pd` holds the guts
with `inlet~`/`outlet~` at the cut. `Patch.load` reads an existing `.pd` from
disk, so you can refactor a finished flat patch — not just one you built in the
same session.

**Proven on the real thing:** the flat 140-object `outrun_303.pd` refactored to
`[else/pad] → [acid303] → [dac~]`, a 0-inlet engine driven by the pad's global
sends, rendering identically to the original. See
`integration/extract_outrun.py`.

**The acceptance test is your ears.** Render before and after and compare — a
refactor that changes the sound is a failed refactor. A transposed port produces
a perfectly valid `.pd` file, so the audio comparison is the *only* thing that
catches it.

### What it handles

- **Boundary classification** — every crossing becomes an inlet or outlet;
  several crossings from one source outlet dedup into a single port.
- **Port typing** — `inlet~`/`outlet~` vs `inlet`/`outlet`, resolved from the
  *source* outlet by object class (not the `~` suffix: `snapshot~` and `env~`
  have control outlets, `tabplay~` outlet 1 is a done-bang).
- **Port ordering** — Pd assigns port index by the inlet/outlet object's **x
  position**, not file order, so ports are laid out at a stable increasing x.
- **Signal buffers** — `between()` follows `delwrite~`→`delread~` and
  `throw~`→`catch~`, so a cut through the chain takes the delay taps with it. A
  `delread~` has no explicit input and would otherwise fall outside the path.
- **`$0-` namespacing** — names used *only* inside the region are rewritten so a
  second instance gets its own. This is what makes an extracted engine
  instantiable twice; an un-namespaced delay line is exactly why the
  hand-authored `acid303` cannot be.

### What stays global

Names shared with the parent — a control surface's sends, a common clock — are
left alone, so instances share them. That is the rig's own idiom: **zero-inlet
engines driven by global receives.** Pass `broadcast=[...]` to force a name to
stay global even when it looks internal.

A name is only namespaced when every reference to it can be rewritten. If it is
also touched by a message-box `; name ...` send, a GUI send symbol, or an `#X
array`, it stays global — renaming one end and not the other would cut the wire
with no error at all.

### Shared-init sources

A `[loadbang]` (or anything in `duplicate=`) that inits both the engine and the
parent is **copied** to each side rather than threaded through a port — a source
with no inlets is identical on every instance, so the engine self-initialises and
the parent keeps its own init. Without it the abstraction sprouts a spurious
outlet just to fire the parent's setup.

### Limits worth knowing

`extraction_plan(...)["warnings"]` reports the hazards, chiefly a delay line or
table allocated inside the cut but named outside it: it cannot be namespaced, so
two instances will collide. Boundary sends/receives are deliberately *not*
promoted to inlets — the global-receive idiom is the proven pattern.

**Name collisions.** The abstraction `name` must not shadow an object already on
Pd's search path — `[voice]`, `[voices]`, and many ELSE names are real objects,
and an abstraction that shadows one *silently never loads*. pdbuild is Pd-free
and cannot check this; verify against a live Pd (the render step catches it — the
extracted patch won't match the original).

### Composing on its own output

`extract()` runs on patches it produced. An abstraction instance, `[clone]`, or
`[pd sub]` has signal outlets with no `~` in its name, so port typing resolves
them against the referenced abstraction's real port objects rather than the
suffix. It finds the `<name>.pd` files on `search_dirs`, plus `out_dir` and — for
a patch opened with `Patch.load` — the directory it came from. So you can extract
an engine, reload, and extract again, nesting abstractions, and the audio stays
identical.

## `PdPatch` — the legacy emitter

The original standalone builder, kept working and tested because `instruments/`
was built with it and those builds should stay reproducible. **Frozen** — new
work uses `Patch`. It carries a known floatatom send/receive defect, documented
in place rather than fixed, so the old instruments still regenerate byte-identically.

## The workflow this is built for

1. **Build** the patch with pdbuild.
2. **Render + analyze** with pdverify — silent? clipping? NaN? what pitch, what
   character?
3. **Isolate** — mute parts by message and measure each module alone.
4. **Sweep** — render a grid across a parameter space to find dead spots.
5. **Fix and repeat.**

That loop is what makes it possible to build an instrument you cannot hear. See
the cookbook's *Build → verify loop* section for the concrete techniques.
