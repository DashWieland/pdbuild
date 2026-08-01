"""Envelope generators -- control trigger in, signal-rate envelope out."""

from __future__ import annotations

from ._util import wire


def ad_envelope(patch, trigger, *, attack: float = 5.0, decay: float = 200.0,
                peak: float = 1.0):
    """An attack/decay envelope, retriggered whenever ``trigger`` fires.

    ``trigger`` is a control port (a bang, or any message -- e.g. a
    ``[loadbang]`` for a one-shot or a ``[metro]`` for repeats). Returns a
    ``[vline~]`` whose outlet 0 is the signal envelope: multiply it against a
    tone to shape the amplitude.

        env = ad_envelope(p, gate, attack=2, decay=300)
        voice = p.obj("*~"); p.link(osc, 0, voice, 0); p.link(env, 0, voice, 1)
    """
    # vline~ list syntax is (target, ramp-ms, delay-ms) triples: ramp to peak
    # over `attack`, then -- after `attack` ms -- ramp back to 0 over `decay`.
    m = patch.msg(f"{peak} {attack}, 0 {decay} {attack}")
    env = patch.obj("vline~")
    wire(patch, trigger, m, 0)
    patch.link(m, 0, env, 0)
    return env


def asr_envelope(patch, gate, *, attack: float = 5.0, release: float = 100.0,
                 peak: float = 1.0):
    """A gated attack/sustain/release envelope.

    ``gate`` is a control port carrying 1 (note on) then 0 (note off) -- e.g. a
    toggle or a step sequencer's gate. Rises to ``peak`` over ``attack`` on the
    1, falls to 0 over ``release`` on the 0. Returns the ``[vline~]``.
    """
    sel = patch.obj("sel 0 1")            # 1 -> outlet 1 (on), 0 -> outlet 0 (off)
    on = patch.msg(f"{peak} {attack}")
    off = patch.msg(f"0 {release}")
    env = patch.obj("vline~")
    wire(patch, gate, sel, 0)
    patch.link(sel, 1, on, 0)             # gate==1
    patch.link(sel, 0, off, 0)            # gate==0
    patch.link(on, 0, env, 0)
    patch.link(off, 0, env, 0)
    return env
