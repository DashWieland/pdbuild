"""Shared plumbing: open patches in a real, headless Pd and read its console.

pdverify's renders answer "what does it sound like"; some claims are about
Pd itself instead -- does it accept a connection, what does an object print,
what does it write to disk -- and need the bare program. ``run_pd`` skips
the test when no Pd is installed, like the render tests do.
"""

from __future__ import annotations

import re
import subprocess

import pytest


def _pd_path() -> str | None:
    try:
        from pdverify.errors import PdNotFound
        from pdverify.pd_locate import discover
    except ImportError:
        return None
    try:
        return discover().path
    except PdNotFound:
        return None


PD = _pd_path()

# Pd's complaint about a connection it refuses, e.g.
#   "x.pd 6 0 2 2 (float->expr) connection failed"
FAILED_CONNECTION = re.compile(r"(\d+) (\d+) (\d+) (\d+) \([^)]*\) connection failed")


@pytest.fixture
def run_pd():
    """``run_pd(*patches, cwd=, realtime=False, timeout=60) -> console text``.

    Opens the patches in ``pd -nogui -noaudio`` (plus ``-batch`` unless
    ``realtime``) with ``cwd`` as the working directory. A batch run has no
    end of its own: the patches must send ``; pd quit``. Real time is for
    what batch cannot show, e.g. ``writesf~``, whose disk thread a batch run
    outruns.
    """
    if PD is None:
        pytest.skip("needs a Pd install")

    def run(*patches, cwd, realtime: bool = False, timeout: float = 60) -> str:
        args = [PD, "-nogui", "-noaudio"] + ([] if realtime else ["-batch"])
        for p in patches:
            args += ["-open", str(p)]
        proc = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                              errors="replace", timeout=timeout)
        return (proc.stdout or "") + (proc.stderr or "")

    def refused(console: str) -> set[tuple[int, int, int, int]]:
        """The ``(src, outlet, sink, inlet)`` connections Pd refused to make."""
        return {tuple(int(v) for v in m) for m in FAILED_CONNECTION.findall(console)}

    run.refused = refused
    return run
