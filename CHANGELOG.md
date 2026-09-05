# Changelog

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
