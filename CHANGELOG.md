# Changelog

## 0.9.1 — graphs load back, extract sees every table use, more objects validated

These are the follow-ups 0.9.0 left open. Tests: 283 → 310. Every arity is
checked against Pd 0.56.2.

### `Patch.load()` reads graphs

- It needs the py2pd fork at 11baaf2 or later, which parses
  `#X restore x y graph`, keeps `#A` saved contents, and writes `#X coords`
  as Pd does.
- A graph `Patch.graph` wrote comes back as a `Graph`, with its records
  byte for byte. Any other graph, such as one made in Pd's GUI with its
  contents saved, stays py2pd's `Graph`, which keeps every record verbatim.
- `extract` and `preview` treat both kinds as graphs.
- Verified in Pd: a GUI graph with saved contents, loaded and saved again,
  still reads 0.75 at index 3.

### `extract` sees every table use

- `[tabread]`, `[tabread4]` and `[tabwrite]` (control rate) and `[tabosc4~]`
  are table uses. A step sequencer's `[tabread steps]` crossed a cut
  unseen, and the plan did not list the table at all.
- The `[array]` verbs have their own roles: `define` and `set` write, `size`
  reads and writes, and `get`, `sum` and the others read. Only
  `array define` allocates. Every `[array …]` used to count as a
  definition, so extracting an `[array get wave]` warned that instances
  would collide on a table they only read.

### `object_io` declares more objects

- `[del]` has 2 inlets and 1 outlet; py2pd knew `[delay]` but not the
  alias. `[table]` has none of either. Control-rate `[tabread4]` has 1 of
  each.
- The `[list …]` verbs:
  - `append`, `prepend` and a bare `[list]`: 2 inlets, 1 outlet;
  - `split`: 2 inlets, **3 outlets**;
  - `trim`, `length`, `fromsymbol` and `tosymbol`: 1 inlet, 1 outlet;
  - `store`: 2 inlets, 2 outlets.

  py2pd gave every verb one outlet, which refused a link from
  `[list split]`'s second and third outlets. An unknown verb doesn't
  create an object, so it is left undeclared.
- The `[array …]` verbs:
  - `define`: 1 inlet, 1 outlet;
  - `size`: 2 inlets, 1 outlet;
  - `get`, `sum` and `random`: 3 inlets, 1 outlet;
  - `set`: 3 inlets, 0 outlets;
  - `quantile`: 4 inlets, 1 outlet;
  - `max` and `min`: 3 inlets, 2 outlets (the value, then its index).

## 0.9.0 — graphs a player can see, multi-expression expr, takes that never overwrite

Lifted from the `overtone` build (its field log, lessons 6 and 7). That script
carried its own `ArrayGraph`, `mexpr()` and RECORD search because the library
lacked them. Tests: 229 → 283. Every claim about Pd behaviour is checked
against Pd 0.56.2 itself.

### `Patch.graph()` — a graph-on-parent array

`graph(name, size, x, y, w, h, ylo, yhi, *, style="points", hide_name=True,
editable=False)` writes the four records Pd itself saves (`#N canvas …
(subpatch)` / `#X array` / `#X coords` / `#X restore x y graph`). It is one
box on the parent: one connection index, no inlets or outlets. py2pd's
`add_array` writes a bare `#X array`, a table with nothing to see.

- Flags are `2 * style` (polygon 0, points 1, bezier 2), plus 8 to hide the
  name. The x range is fitted as Pd fits it: `size` for points, `size - 1`
  for polygon and bezier.
- **Pd does not clip an array to its graph**, so the range must hold every
  value. The docstring, the README and the cookbook all say so.
- `editable=False` sends `; name edit 0` from the loadbang, because in run
  mode Pd lets a mouse drag write into any array and does not save the edit
  state. A `$0-` name is locked through `[edit 0( → [s $0-name]`, since a
  message box expands `$0` to 0.
- Verified in Pd: a `; name` list and a `[tabwrite]` land, and `[array get]`
  reads them back. Pd re-saves the four records unchanged. Pd complains
  about a method arrays lack but not about `edit`. Two instances of an
  abstraction keep their own `$0-` graphs.
- `extract` counts a graph's table as allocated, like a bare `#X array`, so
  cutting one into an abstraction warns that instances would collide.
- Refused up front: an unknown style, an empty graph, `ylo == yhi`, and a
  name that would split the record (a space, `;`, `,`).
- Not yet: `Patch.load()` of a file holding a graph. py2pd raises
  `ParseError` on `#X restore x y graph` (documented).

### `object_io` — expr outlets, every count checked against Pd

- `[expr]`, `[expr~]` and `[fexpr~]` have **one outlet per `;`-separated
  expression** (written `\;` in files). The count was 1, so validation
  refused a link from outlet 1. The outlets fire right to left, so a `[pack]`
  fed from outlets 0..n-1 packs in order.
- Inlets count `$i` and `$x` variables as well as `$f`, `$s` and `$v`; `$y`
  (an fexpr~ output's past) opens none.
- Declared: `until`, `makefilename`, `writesf~ N` (N inlets), `file patchpath`
  and `file isfile` (1 in, 2 out).
- **Fixed, found by checking the table against Pd:** `rev3~` has 6 inlets,
  not 2 (L, R, then level, liveness, crossover and damping; a link to the
  level inlet was refused). `noise~` has 1 inlet (`seed`), not 0. `inlet~`
  has 1 inlet and 2 outlets (control data on the second), not 0 and 1.
  `tests/test_patch.py::test_declared_arity_agrees_with_pd` connects one past
  every declared port, and Pd must refuse exactly those (`connection failed`).

### `pdbuild.preview` draws graphs

- `boxes()` returns a graph as `kind="graph"`, carrying its `plots` (name,
  size, style, hidden name, any `#A` contents saved in the file) and its data
  `bounds`. `overlaps()` counts graphs as part of the face.
- `layout_png(…, arrays={name: values})` draws the values where Pd would:
  points as dashes, polygons as lines, unclipped, with out-of-range values
  in red outside the box.
- **Fixed:** a `[pd sub]` or a graph took no index in `boxes()`. It closed
  back to depth 1 rather than 0, so every box after one was numbered one
  short. A bare `#X array` took none either; Pd counts each as one box. A
  test wires a patch with `boxes()`' indices and Pd delivers. A
  graph-on-parent subpatch is its rectangle, and `#N struct` no longer opens
  a canvas.
- **Fixed:** `layout_png` drew inside matplotlib's default subplot margins,
  shrinking the canvas about 0.78× and offsetting it. It now draws 1 px per
  canvas unit, with a title in its own band above.

### `surface.record_takes()` — RECORD that never overwrites

`record_takes(patch, source_l, source_r, *, recv="record", prefix="take_",
x, y)`: on `record 1` it finds the first free `take_NNN.wav` (001..999) beside
the patch and records 24-bit stereo into it with `[writesf~ 2]`. `record 0`
stops. The path is printed. The search is overtone's: `[until]` →
`[makefilename]` → `[file patchpath]` → `[file isfile]`. **`[file isfile]`
bangs its right outlet for a missing path and never outputs 0**, so the
right outlet means "free".

**The earlier rigs overwrite takes.** `lila_rig`, `ember` and `tend` number
their takes from `take_001` at every launch. The first RECORD of a new
session therefore overwrites the previous session's `take_001.wav`, and so
on up. They are frozen and left as they are; new instruments should use
`record_takes`.

It is verified in real time, since a batch run ends before `writesf~`'s disk
thread has opened the file and no take appears. Two launches in a folder
with a space in its name, which already holds a `take_002.wav`, write 001
and then 003. They leave 002 byte for byte, and each take is 0.55 s of the
440 Hz test tone, on `Patch` and on `PdPatch`.

### Tests

`tests/conftest.py` adds a `run_pd` fixture for claims about Pd rather than
the sound. It opens patches in a headless Pd, in batch or in real time,
returns the console, and reports which connections Pd refused.

## 0.8.0 — control surfaces, a layout preview, the control-tier modules, complete escaping

Everything here was invented inside one instrument's build script (`lila_rig`,
~1700 lines, 40 verified checks) and is now library code with its own tests.

### `pdbuild.surface` — the control-surface idiom

The standard for every control: **the engine reads `[r name]`; the widget
emits `[s name]` from its outlet and listens on `name_ui`.** Anything — GUI,
hardware CC, pad, test script — sets a control by sending to `name_ui`; the
widget updates and re-emits; one source of truth, and every control is
injection-testable (its "message twin"). Works on both `Patch` and `PdPatch`.

- `control()` / `column()`: an IEM widget (`hsl vsl hradio vradio tgl bng
  nbx`) with receive `<name>_ui`, `[s name]`, the **loadbang init message**
  and a label, laid out in columns with the plumbing to the right.
- `display(name)`: a number box that *shows* `[s name]` — the receive slot
  right on both builders (it compensates `PdPatch.floatatom`'s swapped
  arguments); optional `send=` for the runtime probe.
- `pad_row()`: momentary / toggle / cycle pads whose flip and cycle logic
  reads the control's *current* value, so pads and panel never disagree.
- `cc_map()`: a `[ctlin]` router `(cc, name, lo, hi)` → `<name>_ui`, a
  `[r fakecc]` twin, and a LAST-CC broadcast for remapping.
- `note_split()`: `[notein]` repacked `(note vel ch)` then unpacked, so the
  channel gates are set before the note passes whatever order the outlets
  fired in.

Rendered tests prove the contracts: the init value reaches the engine and
`control.send("freq_ui", 440)` moves it; a CC through the router lands on the
widget; notes split by channel; a display box really re-emits what it was
sent (`PROBE: 42` in the console) on both builders.

### `pdbuild.preview` — look at a panel you cannot open

`boxes()` reads every box's canvas rectangle out of a patch, text or file
with widgets at Pd's real sizes; `overlaps()` finds buried controls;
`layout_png()` draws it with matplotlib (`pip install pdbuild[preview]`).

### Control-tier modules (`pdbuild.modules`)

- `swing_clock(groups=(3,3,3,3))`: one `[metro]` retimed on its own tick —
  `pulsems * (pos == 0 ? 1 + swing : 1 − swing/(size−1))` — broadcasting
  `pulse`, `pulsepos`, `bar`, `halfpulse`, `pulsems`; a `reset` receive; an
  optional tempo-factor receive for accelerando. Verified: IOI CV < 0.05 at
  swing 0, > 0.15 at 0.4 with the same mean; bar = sum(groups) pulses for
  12/8, 5/8 and 7/8.
- `step_tables(presets)`: per-pulse tables loaded by one message per preset,
  the **intensity-tier gate** (a hit plays when `0 < level ≤ INTENSITY`),
  preset switching restarts the bar; `gated_value()` reads a value row before
  its gate fires. Verified: 5 / 7 / 9 hits per tier; a mid-bar preset change
  restarts the bar; a bass row plays C4 G4 C5 G4.
- `melody_loop(phrases)`: the Turing-machine loop — a table of degrees /
  REST / HOLD seeded from a phrase bank, a priority mask for DENSITY (strong
  beats first, masked steps become holds), per-pass MUTATE from a
  scale-aware generator, rests as release bangs, velocity accents by rank, a
  strong flag for a second voice — and the pass counter zeroed by the reset
  together with the clock (the lila_rig v3 bug). Verified with pdverify's
  `loop_similarity`: repeats at MUTATE 0, diverges at 1; DENSITY 0.2 → 1 adds
  onsets; a reset mid-bar-2 brings bar 1 back.
- `euclid(k, n)` / `euclid_rows(n)`, `scale_tables(scales)` and
  `scale_degree(degree)` (degree → fractional MIDI from a scale table + tonic
  + neutral-degree offset — maqam quarter tones). Verified: Bayati degree 1
  lands E half-flat at HALF-FLAT 0.5, E♭ at 0, E at 1; the octave; degree −1.

### Escaping — complete and idempotent, in both builders

In a `.pd` file a creation argument or message dollar must be written `\$1`
(an unescaped `$1` is evaluated while the file loads, against nothing: the
box gets `0` and the console says `argument number out of range` — this
silently zeroed every argument of a Karplus-Strong abstraction), and a comma
inside an object box (`expr if(a, b, c)`) must be `\,`.

- `PdPatch` (frozen): `obj()` now escapes `$<digit>` and bare `,`/`;`;
  `msg()`/`comment()` escape `$<digit>` too; nothing already escaped is
  touched (the double-escape that garbled a setup message is gone). The
  legacy ` \,` spacing is kept, so **all 16 committed instrument files
  regenerate byte-identically**; only bytes that were broken change. This is
  the one behaviour change since the class was frozen; the floatatom slot
  defect stays documented (`surface.display()` is the fix path).
- `Patch`: py2pd escaped correctly but doubled an already-escaped `\$1`;
  `obj()`/`msg()`/`comment()` normalise first, so both spellings land on the
  same bytes.
- `OBJECT_IO` knows the IEM GUIs, so a control wired to a bogus inlet fails
  validation.

## 0.7.0 — module library: the synth-voices tier

`oscillator`, `subtractive_voice`, `acid_voice`, `fm_voice`; drums `kick`,
`snare`, `hat`. Verified for pitch and timbre.

## 0.6.0 — module library: the signal-processor tier

`ad_envelope`, `asr_envelope`, `glide`, `smooth`, `lowpass`, `highpass`,
`bandpass`, `resonant_lowpass`, `saturate`, `delay`, `chorus`.
