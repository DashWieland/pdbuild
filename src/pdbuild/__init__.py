"""pdbuild -- authoring Pure Data patches for instruments.

`Patch` is the current API: our Pd idioms layered over **py2pd**, which owns
the file format, connection bookkeeping, parsing and validation. We stopped
maintaining our own emitter once a port of the four-file `acid_set` rig onto
py2pd rendered identically to it (similarity 1.000, peaks within 0.00 dB).

`PdPatch` is the original standalone emitter, kept working and tested because
the earlier instruments were built with it. Frozen -- new work uses `Patch`.

See README.md for the guide and COOKBOOK.md for the accumulated
Pd-generation field notes.
"""

from __future__ import annotations

from . import preview, surface
from .extract import (
    between,
    crossing_edges,
    downstream,
    extract,
    extraction_plan,
    upstream,
)
from .legacy import PdPatch
from .patch import OBJECT_IO, Patch, object_io

__version__ = "0.7.0"
__all__ = [
    "Patch", "PdPatch", "OBJECT_IO", "object_io",
    "between", "downstream", "upstream", "crossing_edges",
    "extract", "extraction_plan",
    "surface", "preview",
]
