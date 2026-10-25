# Changelog

## 0.10.0 — ambient modules

A fourth module tier, `pdbuild.modules.ambient`: the blocks of a calm,
generative ambient piece (drone, sparse plucked notes, a water-like noise
bed, reverb, seamless loops). A participant wanting one found nothing to
start from and wrote it in numpy. Vanilla Pd only (no externals, nothing from
`extra/`), and everything random is seeded, so a render is reproducible bit
for bit. Tests: 40 new in `tests/test_ambient.py`, each with a control that
would fail it. Every number below is measured in Pd 0.56.2.

### The modules

- **`reverb(patch, sig, *, size, damping, mix, decay_s, predelay_ms,
  diffusion, mod_ms) -> Stereo`**: predelay, four input allpass diffusers
  and an eight-line feedback delay network (Hadamard mixing, a damping
  `[lop~]` in each line, line lengths wobbled ±0.3 ms), tapped on two
  orthogonal rows for decorrelated L/R. `size` 0..1 sets RT60 =
  0.4 · 25^size s. Measured RT60 (Schroeder T20) 0.72 / 1.86 / 4.9 s at
  size 0.2 / 0.5 / 0.8 against 0.76 / 2.0 / 5.25 nominal. 20 s of noise at
  size 1, no damping: finite, settled (RMS 0.072 at 5–10 s and at 15–20 s).
  L/R |r| < 0.06. Damping 0.8 moves the late tail's centroid from 4.3 kHz
  to 330 Hz. The wet level is normalised by RT60 (noise in at 0.1 RMS: 0.080
  / 0.062 / 0.050 out for size 0.2 / 0.6 / 1.0). `mix=0` is the input,
  sample for sample. A `Stereo` or `[left, right]` input keeps a stereo dry
  path.
- **`drone(patch, pitch, *, partials, lfo_rates, lfo_depth, loop_s,
  glide_ms, gain)`**: sine partials `(ratio, gain)`, each on its own slow
  amplitude LFO (0.03–0.11 Hz by default, spread start phases). 99.99 % of
  the power on the 110 Hz series (A#2's: 0 %); level wanders 5.4 dB over 30 s
  and moves at most 0.06 dB between neighbouring 50 ms windows; `lfo_depth=0`
  is steady (0.01 dB). `loop_s` rounds every rate to whole cycles per loop:
  the second 8 s matches the first to 16-bit rounding.
- **`pluck(patch, trigger, pitch, *, decay, brightness, partials, voices,
  attack_ms, gain)`**: harmonics with exponential decays (a line in dB into
  `[dbtorms~]`; the upper partials die sooner), dealt round-robin to
  `voices`. `trigger=None` plays every pitch that arrives. Not
  Karplus-Strong, because a Pd delay loop is at least a block, which caps a
  plain-canvas string at 689 Hz; f0 measured 219.99 / 880.01 / 1500.01 Hz.
  It rises in 3.3 ms and is 40 dB down at 0.70 s (`decay=1`) and 2.00 s
  (`decay=3`). Brightness 0.1 → 0.8 moves the centroid 221 → 399 Hz. Four
  voices hold a three-note chord where one voice keeps only the last note.
- **`noise_bed(patch, *, lo, hi, swell_rate, swell_depth, flutter,
  flutter_hz, seed, loop_s, gain)`**: seeded `[noise~]` → 24 dB/oct
  Butterworth highpass and lowpass (`[biquad~]` sections whose coefficients
  are recomputed from `[samplerate~]` at load) → a sine swell × a seeded,
  lowpassed-noise flutter. 88 % of the energy in 300–3000 Hz, 97 % within a
  quarter beyond the edges. The edges sit at −2.6 to −2.8 dB at 44.1 and
  48 kHz alike. RMS = `gain`. In-band flatness 0.99 (a drone in the band:
  4e-6). The level envelope peaks at the swell rate. One seed gives the
  same bed with or without another `[noise~]` built before it.
- **`loop_lfo(patch, *, loop_s, cycles, phase, shape, lo, hi)`**: exactly
  `cycles` cycles per loop, sine / triangle / ramp over `lo..hi`. The phase
  is a ramp a `[metro]` restarts every loop through `[vline~]`, not a
  free-running `[phasor~]`. From a 32-bit capture over 60 s: under 2e-6 of
  a cycle after a minute (4e-7 for a 3-cycle ramp over a 7 s loop), where
  `[phasor~]` at the same 3/7 Hz is off by 1.0e-4.
- **`scale_walk(patch, clock, *, scale, tonic, seed, step_weights, density,
  lo, hi, start, density_recv) -> ScaleWalk`**: a seeded random walk over
  scale degrees on each clock tick, with probability `density`. `.midi`
  emits the note; `.degree` emits the degree, for `scale_degree` and its
  table bank. One `[random]` decides whether a tick plays and another picks
  the step, drawn only when a note plays, so the sparse walk is the dense
  walk's opening, note for note. Both are seeded on the first tick, which
  makes the walk independent of loadbang order and of other `[random]`s
  built before it. Over ~170 ticks, density 0.2 plays 40 notes and 0.8
  plays 139.

A composed test patch (scale_walk → pluck, a drone and a noise bed into the
reverb) renders unclipped at −26.9 dBFS RMS, with 14 onsets in 12 s, and two
renders are identical. pdverify calls it "A very dynamic, warm, bass-heavy
chord with no single clear pitch".

### Also

- `OBJECT_IO` declares `biquad~` (1, 1), `dbtorms~` (1, 1), `max~` /
  `min~` (2, 1) and `samplerate~` (1, 1), checked against Pd by the arity
  test.
- COOKBOOK: `biquad~` and band edges, seeding `[noise~]` / `[random]`,
  `rev1~`–`rev3~` living in `extra/`, the one-block floor on delay loops,
  FDN stability, `[phasor~]` drift, pdverify's 16-bit capture, bare Pd's
  48 kHz default, and why whole-spectrum flatness can't see band-limited
  noise. The `rev3~` row said 2 inlets; it has 6.

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
