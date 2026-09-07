# Pd patch-generation cookbook

Field notes for generating Pure Data patches programmatically. Everything here
came out of real builds — a step sequencer, an outrun-style 303 acid synth, and a
four-file performance rig — and **every gotcha listed cost real debugging time.**

The recurring theme: Pd is a *live* environment being asked to behave like a
*compiled* one. Most bugs are not DSP bugs; they're **initialization and
ordering** bugs. Read §2 before writing anything.

> **See also:** the `pure-data` skill (`~/.claude/skills/pure-data/`) carries the
> same hard-won semantics as general working guidance, with deeper reference
> files on the file format, object I/O, and headless verification. This cookbook
> is the *pdbuild-flavoured* view: how these facts shape the code you write with
> the builder. They agree; if they ever diverge, trust a fresh render.

---

## 1. The `.pd` file format

A patch is a flat list of `;`-terminated records.

```
#N canvas 20 20 1240 840 10;      <- the window (x, y, w, h, font)
#X obj 30 40 osc~ 440;            <- object box
#X msg 30 70 1 3 \, 0 210 3;      <- message box
#X text 30 100 a comment;         <- comment
#X floatatom 30 130 8 0 0 0 - - xpad 0;
#X connect 0 0 1 0;               <- src_index outlet -> dst_index inlet
```

Other records you'll meet: `#X restore` (closes a subpatch), `#X coords`
(graph-on-parent), `#X array` + `#A` (array and its data), `#X declare`
(search paths / libraries).

### The index rule — the one that breaks everything

`#X connect` refers to boxes by their **creation index**: the order they appear
in the file, starting at 0. **Every `#X` box element takes an index** — objects,
messages, comments, floatatoms, GUI objects. Comments included.

> Add one comment in the middle of a patch and every connection after it points
> at the wrong object.

This is precisely why pdbuild hands you an index from every box method and never
asks you to compute one. If you write `.pd` by hand, this is what will get you.

### Escaping

Inside a message box **or a comment**, `,` and `;` are message separators and
must be written `\,` and `\;`.

```
#X msg 30 70 1 3 \, 0 210 3;      # ONE message box holding two messages
#X msg 30 90 \; pd dsp 1;         # sends "dsp 1" to the receiver named "pd"
```

An unescaped comma in a *comment* silently splits it, and Pd tries to evaluate
the tail:

```
#X text 20 10 acid303 - engine (edit here, play from acid_set.pd);
  -> error: canvas: no method for 'play'
```

That error message is very hard to trace back to a comment. pdbuild escapes both
characters for you in `msg()` and `comment()`.

A comma inside an **object box** is the same story: `expr if($f1>0, $f1, $f2)`
must be written `expr if($f1>0\, $f1\, $f2)` in the file or the comma splits
the object's arguments. `obj()` escapes it for you (0.8.0) on both builders.

### Dollar signs

**In a `.pd` file, every dollar-arg is written escaped: `\$1`, `\$0-name`.**
This is how Pd itself saves them, and it is not optional. An unescaped `$1` in
a file is evaluated *while the file is being read*, against nothing: the box
is built with `0` in its place and the console says `$1: argument number out
of range`. The symptom is an abstraction whose `[lop~ $2]` / `[*~ $3]` all
came out as `0` — silence, with no other error. (This zeroed every argument
of a Karplus-Strong string and silenced a whole voice inside an otherwise
sounding rig; it was caught only because the voice was soloed by injection.)

pdbuild writes the escape for you, **idempotently**, in `obj()`, `msg()` and
`comment()` on both `Patch` and `PdPatch` — `$1` and `\$1` land on the same
bytes, and an already-escaped `\,` is never doubled into `\ \,` (the other
half of this bug, which garbled a setup message). Write dollars naturally:

```python
a.obj("lop~ $2")                     # -> lop~ \$2   (creation arg 2)
a.obj("delwrite~ $0-ks 300")         # -> \$0-ks     (instance-local buffer)
p.msg("$5 $1 $2 $3 $4")              # -> \$5 \$1 …  (message dollars, expanded at message time)
```

Load-time expansion only triggers on `$` followed by a **digit** (`$0`, `$1`…).
So `expr~`/`expr` variables written `$v1`, `$f1`, `$i1` pass through untouched
and are safe to emit literally:

```python
p.obj("expr~ tanh($v1)")     # verified: loads and runs
p.obj("expr 15000/$f1")      # BPM -> 16th-note ms
```

In an **abstraction**, `$1..$n` are creation arguments and `$0` is a unique
per-instance id — use `$0-name` for instance-local send/receive/array names so
two copies don't collide. Under `[clone]`, `$1` is the instance number and the
user's arguments start at `$2`. A bare `$1` in a *comment* errors at load too.

---

## 2. Semantics that will bite you

### 2a. GUI controls emit **nothing** at load — the silent-patch trap

This one bit us **twice in a single build**, both times producing a completely
silent patch with no errors:

- Mute toggles fed gain chains ending in `[line~]`. A toggle that's never clicked
  outputs nothing, `line~` sat at **0**, and every channel was multiplied to
  silence.
- A drum-pattern radio fed `[== n] -> [spigot]` gates. The radio never emitted, so
  every spigot stayed **closed** and the drums never fired.

**Rule: anything downstream of a GUI control must be initialized explicitly.**
pdbuild has a helper for exactly this — use it and the trap disappears:

```python
p.init_all({mute_a: 0, mute_b: 0, pattern_radio: 0, master: 0.65})
```

which wires `[loadbang] → [value( → widget` (one shared loadbang). Sending a
float to an IEM widget both sets its visual state and makes it output — which is
what you want. Two integration tests in `tests/` pin this: one asserts the
un-initialized version really does render silent, the other that `init()` fixes
it.

Sending a float to an IEM widget both sets its visual state and makes it output —
which is what you want. (IEM widgets have an "init" flag that does this too, but
driving them from `loadbang` is explicit and easy to audit.)

Same class of bug, other flavours:

| Object | Trap |
|---|---|
| `[line~]`, `[vline~]` | start at **0** — silence until something sends a target |
| `[spigot]` | defaults **closed** |
| `[f ]` | outputs **only when banged**; a value in its cold inlet is invisible until then. Prime it with `loadbang` if downstream math depends on it |
| `[metro]` | start it with a nonzero float (`1`), not a bang |
| cold inlets generally | hold a value but never fire — set them *before* the hot inlet arrives |

### 2b. You cannot shadow a built-in class

Putting `dac~.pd` on the search path does **not** intercept `[dac~]` — Pd
resolves the registered built-in and never looks at the path. The render comes
out silent with no error. (Same for `[notein]`.)

If you need to intercept a built-in, **rewrite the class token** to a
differently-named abstraction (this is exactly how pdverify captures audio:
`dac~` → `pdverify_sink~`). Rewriting only the class field touches no indices and
no `#X connect` lines.

### 2c. Execution order — right to left, and it matters

- `[trigger]`/`[t]` fires its outlets **right to left**.
- `[unpack]` does too: the **highest-numbered outlet fires first**, outlet 0 last.
- **Fan-out from a single outlet to several destinations is UNDEFINED order.**
  If order matters, force it with `[t]`. (In practice Pd 0.56 fires a fan-out
  in connection order — do not build on it; the docs call it undefined.)
- `[notein]` fires **channel, then velocity, then note** (right to left).
  pdverify's `notein` shim used to fan out and deliver the channel last, so a
  patch that gated on the channel dropped every first note played by
  injection; fixed in pdverify 0.2.0 (channel first, as the real object).
  `surface.note_split()` repacks `(note vel ch)` and unpacks again, so the
  channel gates are set before the note passes whatever the source's order —
  use it for any channel-routed MIDI input.

This drives real design decisions. In a step sequencer each step must set the
pitch *before* the envelope triggers, and the glide time *before* the pitch. With
`[unpack]` firing right-to-left, that dictates the column order of your data:

```
message per step:  "gate accent pitch slide"
unpack outlets:      0     1      2     3
fires:                                  ^ slide  (sets glide time)
                                  ^ pitch (into pack -> line~)
                          ^ accent
                     ^ gate   (LAST: triggers the envelope, pitch already set)
```

Get this backwards and every note starts on the *previous* pitch.

### 2d. Abstractions

- Outlet/inlet **order is by x-position** of the `[outlet]`/`[inlet]` objects, not
  creation order. Place them left-to-right deliberately.
- An abstraction is found if its `.pd` sits in the parent patch's directory (or
  on the search path). pdverify's renderer adds the patch's own directory to
  `-path`, so sibling abstractions resolve during headless verification.
- `loadbang` inside an abstraction fires when the abstraction loads.

---

## 3. Vanilla building blocks (verified working headless)

All confirmed to load and render under `pd -nogui -batch -noaudio` on 0.56.

| Object | Use | Notes |
|---|---|---|
| `osc~` / `phasor~` | sine / ramp | freq inlet accepts a **signal** (drive it from `line~` for glide) |
| `phasor~` → `-~ 0.5` | sawtooth | centre it or you feed the filter DC |
| `bob~` | Moog-ladder **resonant** lowpass | in / **cutoff (signal)** / **resonance (signal)**. >4 self-oscillates; keep ≲3.6. `oversample 3` message for stability at high cutoff+Q. The acid filter. |
| `lop~` / `hip~` / `bp~` | 1-pole LP / HP / bandpass | `lop~` cutoff is **control-rate only** — to LFO it, sample the LFO with `[snapshot~]` on a `[metro]` |
| `vline~` | sample-accurate envelopes | `"1 3, 0 210 3"` = ramp to 1 over 3 ms, then from 3 ms ramp to 0 over 210 ms. The AD workhorse |
| `line~` | control→signal ramps | `"target time"`; also the portamento/glide engine |
| `expr~ tanh($v1)` | soft saturation | drive into it with `*~` for "meat" |
| `clip~ -1 1` | safety limiter | put one before `dac~`, always |
| `delwrite~` / `delread~` / `vd~` | delay | `vd~`'s delay time is a **signal** → LFO it for chorus. `delread~` is a fixed tap |
| `rev3~` | reverb | **2-in / 4-out**; args = level, liveness(%), crossover(Hz), damping(%) |
| `noise~` | drums/texture | + `bp~` = snare, + `hip~ 7000` = hat |
| `mtof` | MIDI → Hz | |
| `else/pad` (ELSE) | X-Y control surface | outlet emits `list x y` **and** `click` → split with `[route list click]`; coords range = the creation-arg `dim` |

**Drum voices, cheaply:** kick = `vline~` pitch sweep (110→45 Hz) into `osc~`,
times an amp `vline~`, into `tanh` for punch. Snare = `noise~` → `bp~ 1900 3` ×
short env. Hat = `noise~` → `hip~ 7000` × very short env.

---

## 4. Patterns that work

### Broadcast clock
One master `[metro]` → counter → `[mod 16]` → `[s xstep]`. Every module does
`[r xstep]` and sequences itself. Everything stays locked, and modules stay
independent. `pdbuild.modules.swing_clock` is this with swing: the metro's
interval is set *during its own tick* (it applies to the next tick — verified)
to `pulsems * (pos == 0 ? 1 + swing : 1 − swing/(size−1))`, so the first pulse
of each beat group leans and the group keeps its length. It broadcasts
`pulse`, `pulsepos`, `bar` (before `pulse` on pulse 0), `halfpulse`, `pulsems`,
and restarts on a `reset` receive — which `step_tables` bangs on a preset
change, and which `melody_loop` uses to zero its pass counter, so the loop
stays aligned to bar 1 (a loop that "repeated" while playing its second half
first was a real bug; only a check anchored to musical time caught it).

### Surface / engine split
Put each engine in its own abstraction exposing only `[outlet~]`. The top-level
patch holds **only** what a human touches (pad, toggles, radio, sliders) plus a
mixer. This is what turns a wall of 140 objects into a playable instrument.

### Route control through named sends/receives — not direct wires
Surface control → `[s cutoff]`; engine → `[r cutoff]`. Costs nothing and buys:

- the same parameter is drivable by **GUI, message, or automation**;
- **headless testability** — pdverify can `control.send("drumpat", 2)` to drive a
  rig that has no mouse. Mutes wired this way let you render each module alone.

If a parameter is worth exposing, expose it as a receive.

### `_ui` receives + message twins — the control-surface standard
Route the *widget* through a receive too. The standard, in `pdbuild.surface`:

```
engine reads        [r tempo]
widget emits        [s tempo]      (from its outlet)
widget listens on   tempo_ui       (its receive symbol)
```

Anything — the GUI itself, a hardware CC, a pad, a script, a test — sets a
control by sending to `tempo_ui`; the widget updates on the panel **and**
re-emits to `tempo`. There is one source of truth per control (the panel
always shows what the engine has), and every control is injection-testable:
`control.send("tempo_ui", 180)` in pdverify is exactly a hand on the slider.
A MIDI map is then just `(cc, name, lo, hi) -> <name>_ui`, and its `[r fakecc]`
twin makes the whole hardware layer testable headless except the physical
last hop. Pads that flip or cycle a control read `[r name]` first so they act
against the *current* value. Every widget gets its loadbang init (§2a) — it is
part of the control, not something to remember.

```python
from pdbuild.surface import Control, column, display, pad_row, Pad, cc_map
column(p, 20, 50, [Control("tempo", "hsl", default=168, lo=100, hi=220, label="TEMPO")])
display(p, "lastmidi", 20, 300)                                   # a read-out: shows [s lastmidi]
cc_map(p, [(74, "tempo", 100, 220)], x=600, y=40)                  # knob 1 -> tempo_ui, + fakecc
```

Read-out boxes are the one thing a render cannot check: `display(name,
send="probe")` puts a name in the box's send slot; `[r probe] -> [print]` in a
test then proves the box shows what it was sent (the runtime probe).

### Smoothed control signals (no zipper noise)
```python
r  = p.obj("r cutoff")        # 0..1
m  = p.obj("* 1200"); a = p.obj("+ 200")
pk = p.obj("pack f 25")       # ramp over 25 ms
ln = p.obj("line~")           # -> a smooth signal
```

### Step data as per-step messages
`[sel 0 1 ... 15]` → one message box per step holding that step's parameters →
`[unpack f f f f]`. Compact, no arrays needed, and the whole pattern is visible
in the file. Choose the column order to satisfy §2c.

### Switchable patterns without arrays
`[r pattern]` → `[== n]` → `[spigot]` per pattern; gate the step through the
spigot into that pattern's `[sel ...]` chains. Remember to **initialize the
selector** (§2a) or every spigot stays shut.

### Always
- `clip~ -1 1` before `dac~`.
- `hip~ ~25` to kill DC/subsonic — an un-driven `osc~` sits at 0 Hz, which is a
  DC offset, not silence.
- Keep feedback gains < ~0.6 and clip inside the loop.

---

## 5. Build → verify loop (how to build what you can't hear)

pdverify is what makes blind construction viable. The techniques that paid off:

1. **Gates first.** `silent / clipped / nan-inf` catch nearly every structural
   bug instantly. A silent render almost always means §2a, not DSP.
2. **Read the Pd console.** `render()` surfaces it. Load errors like
   `couldn't create` or `canvas: no method for 'play'` point at missing
   externals or a comment-escaping bug.
3. **Isolate modules.** Wire mutes as receives, then render with all-but-one
   muted and compare peak levels. This is how we found the drums were never
   firing (they measured −20 dBFS of *mute-ramp bleed*, not drums).
4. **Sweep the parameter space.** Render a grid across a control surface and
   print peak/centroid per cell. A 5×5 sweep proved a reported "dead spot in the
   middle of the X-Y pad" was **not** in the synth (every cell was audible) —
   which correctly redirected the hunt to the pad's coordinate reporting.
5. **Pick the right measurement for the question.** A timbre *fingerprint* is
   time-averaged and **cannot detect note-order changes** — comparing two
   generative melodies with `compare()` gave a meaningless 0.97, twice. Measure
   what you actually claim; pdverify 0.2.0 carries the measurands this needed:
   - *does the loop repeat* → `music.loop_similarity(audio, loop_s)` (beat-wise;
     a locked loop ~1.0, a reordered pass ~0.6) / `expect.repeats(loop_s)`;
   - *what note is that pluck* → `Report.f0_hz` / `expect.f0("D2")` — the
     harmonic root, not the loudest partial (routinely the 2nd–5th harmonic of
     a Karplus-Strong string); `has_partial(tol_cents=)` and
     `loudest_partial(note, kmax)` as the fallbacks;
   - *how long is the bar* → solo a once-per-bar voice and read
     `Report.ioi_mean_s` / `expect.ioi(seconds)`; *is it swung* → `ioi_cv`;
     *what does the envelope repeat at* → `period_s` / `expect.period`;
   - *does the tail ring on after the band stops* → `analyze(audio,
     window=(5.5, 7.5))`, `music.window_rms_dbfs`, or `expect.within(t0, t1,
     …)` on any expectation; skip the loadbang-default startup window the
     same way.
6. **Balance by numbers.** Render each part alone, read peak dBFS, and set gains
   from that instead of guessing.
7. **Solo every voice by injection.** "The whole patch makes sound" hid a
   silent voice (its abstraction's arguments had all loaded as 0). With every
   level on a `_ui` receive, soloing is one `control.send` per voice.
8. **Look at the panel.** `pdbuild.preview.layout_png(patch, "panel.png",
   xmax=740)` draws the GUI zone at Pd's widget sizes; `preview.overlaps()`
   lists controls sitting on each other. Run it *in the build* whenever a
   column grows — ember's PADS row was placed by a fixed offset under one
   column and ended up under another column's last slider.
9. **Measure the bus, not the voice.** A shaker designed at 6.5 kHz measured
   −45 dBFS at the output while the calabash next to it measured −19: two
   master filter stages (`bob~` ladders at a nominal 20 kHz) shave the top
   octave, and no per-voice reasoning predicts that. Bang each voice alone
   with the clock stopped (`control.bang("shaker")`, `run=0`) and read the
   windowed RMS *at the output*.
10. **Same notes, different render — the differential measurement.** Pd's
   `random`/`noise~` seeds are fixed by creation order, so two renders of one
   patch with only an effect control changed play identical notes. Then the
   per-window 1/3-octave *difference* between the renders is the effect and
   nothing else: a phaser showed as −20 dB notches walking from 400 Hz to
   8 kHz where fingerprint similarity had shrugged (0.92). The same
   determinism cuts the other way: adding a `noise~` earlier in the file
   re-seeds every later one, so "loudest partial" checks flip between builds
   for no musical reason — use `f0_hz` / band shares.
11. **Onsets have a global floor.** `onset_times` counts rises through 30 % of
   the render's *peak* envelope; a quieter answering voice (−41 dBFS after an
   −80 dB gap) or a shaker between drum hits is not an onset however clearly
   it sounds. For "did a note happen at t" use a local jump (short-window RMS
   ≥ 20 dB over the preceding 250 ms).

### What verification can't do
It confirms *health* (silent/clip/NaN), *tuning*, and *gross character*
(bass-heavy, resonant, dynamic, evolving). It cannot judge whether something
**sounds good**. Taste still needs ears — build the loop so a human can be handed
a `.wav` quickly.

---

## 6. Layout

Function doesn't care about coordinates; humans do.

- Reserve a **GUI zone** at the top, place controls explicitly, then move the
  auto-layout cursor *below* it (`p.cursor(20, 320)`), or the generated guts land
  on top of your pad. (This happened — the X-Y pad ended up buried.)
- Auto-flowed columns are fine for engine internals nobody reads. The real
  answer for human-readable patches is §4's surface/engine split: hide the guts
  in an abstraction so nobody has to read them.
