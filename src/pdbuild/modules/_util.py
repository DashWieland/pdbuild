"""Shared plumbing for module builders.

A module is an ordinary function: it takes a `Patch`, an input *port*, and some
parameters; adds the block's objects; wires the input; and returns the output
node (whose outlet 0 carries the result). Modules compose -- the output of one
is the input of the next -- and a chain of them can be pulled into a reusable
abstraction with `pdbuild.extract`.

A *port* is either a node (its outlet 0 is meant) or a ``(node, outlet)`` pair,
so a module can be fed from any outlet without a heavyweight Port type.
"""

from __future__ import annotations


def as_port(x) -> tuple:
    """Normalise a node-or-(node, outlet) into (node, outlet)."""
    if isinstance(x, tuple):
        node, outlet = x
        return node, int(outlet)
    return x, 0


def wire(patch, source, sink, inlet: int = 0) -> None:
    """Link a source port into ``sink``'s ``inlet``."""
    node, outlet = as_port(source)
    patch.link(node, outlet, sink, inlet)
