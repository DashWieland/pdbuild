"""Synth voices -- control in (a trigger and a pitch), audio out.

Where a signal processor transforms a signal, a voice *generates* one: it takes
a control trigger (when to play) and a pitch (what note), and returns the audio
output. Voices are built by composing the signal-processor tier -- oscillator,
filter, envelope, drive -- so they stay small and the pieces are reused.

Pitch is a frequency in Hz, given either as a constant or as a control port (a
sequencer's note outlet through [mtof], say). The trigger is a control port: a
bang or a 1, e.g. a [loadbang] for a one-shot or a step sequencer's gate.
"""

from __future__ import annotations

from ._util import wire
from .control import glide
from .effects import saturate
from .envelopes import ad_envelope
from .filters import resonant_lowpass


def _freq_signal(patch, pitch, glide_ms: float = 0.0):
    """A signal-rate frequency source at ``pitch``, whether ``pitch`` is a
    constant Hz value or a control port. A control port is de-zippered (and
    optionally portamento'd) into a signal so it can drive an oscillator inlet."""
    if isinstance(pitch, (int, float)):
        return patch.obj(f"sig~ {pitch}")
    return glide(patch, pitch, time_ms=max(glide_ms, 1.0))


def oscillator(patch, pitch, *, waveform: str = "saw", glide_ms: float = 0.0):
    """A bipolar oscillator at ``pitch``. ``waveform`` is 'saw', 'square', or
    'sine'. Returns the oscillator's audio output node."""
    freq = _freq_signal(patch, pitch, glide_ms)
    if waveform == "sine":
        osc = patch.obj("osc~")
        wire(patch, freq, osc, 0)
        return osc
    ph = patch.obj("phasor~")
    wire(patch, freq, ph, 0)
    if waveform == "saw":
        centred = patch.obj("-~ 0.5")            # 0..1 ramp -> bipolar saw
        patch.link(ph, 0, centred, 0)
        return centred
    if waveform in ("square", "pulse"):
        sq = patch.obj("expr~ ($v1 < 0.5) * 2 - 1")
        patch.link(ph, 0, sq, 0)
        return sq
    raise ValueError(f"unknown waveform {waveform!r} (saw|square|sine)")


def subtractive_voice(patch, gate, pitch, *, waveform: str = "saw",
                      cutoff: float = 1200.0, resonance: float = 2.0,
                      attack: float = 5.0, decay: float = 200.0,
                      glide_ms: float = 0.0, gain: float = 0.3):
    """The classic: oscillator -> resonant lowpass -> amp envelope.

    Retriggered on ``gate``; plays ``pitch``. Returns the audio output.
    """
    osc = oscillator(patch, pitch, waveform=waveform, glide_ms=glide_ms)
    filt = resonant_lowpass(patch, osc, cutoff=cutoff, resonance=resonance)
    env = ad_envelope(patch, gate, attack=attack, decay=decay)
    vca = patch.obj("*~")
    patch.link(filt, 0, vca, 0)
    patch.link(env, 0, vca, 1)
    out = patch.obj(f"*~ {gain}")
    patch.link(vca, 0, out, 0)
    return out


def acid_voice(patch, gate, pitch, *, waveform: str = "saw",
               cutoff: float = 350.0, env_depth: float = 2200.0,
               resonance: float = 3.5, decay: float = 180.0,
               glide_ms: float = 0.0, drive: float = 2.5, gain: float = 0.3):
    """A TB-303-flavoured voice: sawtooth -> resonant ladder whose cutoff is
    swept DOWN by an envelope on every note (the acid squelch) -> amp envelope
    -> soft-clip drive.

    ``cutoff`` is the floor the filter closes to; ``env_depth`` how far above it
    the note opens; ``resonance`` the emphasis (~0..4.5). Returns the audio out.
    """
    osc = oscillator(patch, pitch, waveform=waveform, glide_ms=glide_ms)

    # filter envelope: opens to cutoff+env_depth on the note, decays to cutoff
    fenv = ad_envelope(patch, gate, attack=3.0, decay=decay)
    swept = patch.obj(f"*~ {env_depth}")
    patch.link(fenv, 0, swept, 0)
    fcut = patch.obj(f"+~ {cutoff}")
    patch.link(swept, 0, fcut, 0)

    filt = patch.obj("bob~")                     # moog ladder, signal cutoff/res
    patch.link(osc, 0, filt, 0)
    patch.link(fcut, 0, filt, 1)
    res = patch.obj(f"sig~ {resonance}")
    patch.link(res, 0, filt, 2)

    aenv = ad_envelope(patch, gate, attack=3.0, decay=decay + 40.0)
    vca = patch.obj("*~")
    patch.link(filt, 0, vca, 0)
    patch.link(aenv, 0, vca, 1)

    return saturate(patch, vca, drive=drive, level=gain)


def fm_voice(patch, gate, pitch, *, ratio: float = 2.0, index: float = 5.0,
             attack: float = 3.0, decay: float = 250.0, glide_ms: float = 0.0,
             gain: float = 0.3):
    """Two-operator FM: a modulator at ``ratio`` x pitch, ``index`` deep, bends
    the carrier's frequency to grow sidebands -- bells, e-pianos, metallic tones.
    Returns the audio out (amp-enveloped).
    """
    freq = _freq_signal(patch, pitch, glide_ms)
    # modulator: osc~ at ratio*pitch, scaled by index*pitch (deviation in Hz)
    mod_freq = patch.obj(f"*~ {ratio}")
    patch.link(freq, 0, mod_freq, 0)
    mod = patch.obj("osc~")
    wire(patch, mod_freq, mod, 0)
    dev = patch.obj(f"*~ {index}")               # index scales the deviation...
    patch.link(mod, 0, dev, 0)
    dev_hz = patch.obj("*~")                      # ...by the pitch itself
    patch.link(dev, 0, dev_hz, 0)
    patch.link(freq, 0, dev_hz, 1)
    # carrier frequency = pitch + deviation
    car_freq = patch.obj("+~")
    patch.link(freq, 0, car_freq, 0)
    patch.link(dev_hz, 0, car_freq, 1)
    car = patch.obj("osc~")
    wire(patch, car_freq, car, 0)

    env = ad_envelope(patch, gate, attack=attack, decay=decay)
    vca = patch.obj("*~")
    patch.link(car, 0, vca, 0)
    patch.link(env, 0, vca, 1)
    out = patch.obj(f"*~ {gain}")
    patch.link(vca, 0, out, 0)
    return out
