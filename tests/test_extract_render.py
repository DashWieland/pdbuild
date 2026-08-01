"""Milestone 1: a signal-only extraction must not change the sound.

These tests render real audio, so they need Pd and pdverify. They skip
cleanly without them -- but this is the acceptance test for extract(), the
one that catches a transposed port (the file is valid Pd either way). The
bar is the one the py2pd port already met: similarity 1.000.
"""

from __future__ import annotations

import copy

import pytest

from pdbuild import Patch
from pdbuild.extract import between, crossing_edges, extract, node_text

pytest.importorskip("pdverify")
from pdverify import analyze, compare              # noqa: E402
from pdverify.render import RenderSpec, render      # noqa: E402
from pdverify.pd_locate import discover             # noqa: E402

try:
    discover()
    _HAVE_PD = True
except Exception:
    _HAVE_PD = False

pytestmark = pytest.mark.skipif(not _HAVE_PD, reason="needs a Pd install")


def _flat_synth() -> Patch:
    """osc~ 110 -> [ *~ 0.6 -> lop~ 1200 -> hip~ 40 ] -> dac~ (stereo).

    A self-contained voice with a self-terminating recorder is added by the
    renderer; here we just make sound. The middle three objects are the
    region we extract; the crossings are one signal cord in and one out.
    """
    p = Patch(500, 400, 10)
    p.osc = p.obj("osc~ 110")
    p.gain = p.obj("*~ 0.6")
    p.lop = p.obj("lop~ 1200")
    p.hip = p.obj("hip~ 40")
    p.dac = p.obj("dac~")
    p.chain(p.osc, p.gain, p.lop, p.hip)
    p.link(p.hip, 0, p.dac, 0)
    p.link(p.hip, 0, p.dac, 1)
    return p


def _render(patch_path, **kw):
    return analyze(render(str(patch_path), RenderSpec(duration=3.0, **kw)).audio)


def test_signal_extraction_preserves_the_sound(tmp_path):
    flat = _flat_synth()
    flat_path = tmp_path / "flat.pd"
    flat.save(flat_path)
    before = _render(flat_path)
    assert not before.is_silent, "the fixture itself is silent -- test is void"

    # extract the middle of the chain
    region = between(flat, flat.gain, flat.hip)
    assert [node_text(n) for n in region] == ["*~ 0.6", "lop~ 1200", "hip~ 40"]
    extract(flat, region, "voicefx", str(tmp_path))

    extracted_path = tmp_path / "extracted.pd"
    flat.save(extracted_path)
    after = _render(extracted_path)

    sim = compare(after, before).similarity
    assert sim == pytest.approx(1.0, abs=1e-6), (
        f"extraction changed the sound: similarity {sim:.4f}\n"
        f"  before: {before.summary()}\n  after:  {after.summary()}")


def test_extracted_abstraction_has_the_expected_interface(tmp_path):
    flat = _flat_synth()
    extract(flat, between(flat, flat.gain, flat.hip), "voicefx", str(tmp_path))

    text = (tmp_path / "voicefx.pd").read_text(encoding="utf-8")
    # one signal inlet feeding the gain, one signal outlet from the hip~
    assert text.count("#X obj") >= 1
    assert "inlet~;" in text
    assert "outlet~;" in text
    # the parent now holds a [voicefx] box, and the guts are gone
    parent = flat.render()
    assert "voicefx" in parent
    assert "lop~ 1200" not in parent


def test_extracted_patch_still_renders_and_is_audible(tmp_path):
    flat = _flat_synth()
    extract(flat, between(flat, flat.gain, flat.hip), "voicefx", str(tmp_path))
    path = tmp_path / "e.pd"
    flat.save(path)
    assert not _render(path).is_silent


def test_abstraction_instantiates_twice_without_collision(tmp_path):
    """A well-formed abstraction can be dropped in more than once. This is the
    check that a hidden global name (a delay line, a send) would fail -- the
    milestone-1 region deliberately owns none."""
    flat = _flat_synth()
    extract(flat, between(flat, flat.gain, flat.hip), "voicefx", str(tmp_path))

    # two independent voices through two [voicefx] instances, summed
    host = Patch(500, 400, 10)
    o1 = host.obj("osc~ 110")
    o2 = host.obj("osc~ 220")
    fx1 = host.pd.add("voicefx", x_pos=40, y_pos=120, num_inlets=1, num_outlets=1)
    fx2 = host.pd.add("voicefx", x_pos=200, y_pos=120, num_inlets=1, num_outlets=1)
    dac = host.obj("dac~")
    host.link(o1, 0, fx1, 0)
    host.link(o2, 0, fx2, 0)
    host.link(fx1, 0, dac, 0)
    host.link(fx2, 0, dac, 1)
    host_path = tmp_path / "host.pd"
    host.save(host_path)

    r = _render(host_path)
    assert not r.is_silent
    assert not r.has_nan_inf


def test_extraction_leaves_valid_connection_indices(tmp_path):
    """A rebuild that dropped or misnumbered a cord would corrupt the parent.
    No Pd needed for this one -- it is pure structure."""
    flat = _flat_synth()
    extract(flat, between(flat, flat.gain, flat.hip), "voicefx", str(tmp_path))
    n = len(flat.pd.nodes)
    for c in flat.pd.connections:
        assert 0 <= c.source < n and 0 <= c.sink < n


# --------------------------------------------------------------------------- #
# Milestone 2: namespacing, verified against Pd's own console
# --------------------------------------------------------------------------- #

def _engine_with_delay() -> Patch:
    """osc~ -> [ *~ -> delwrite~ echo / delread~ echo -> +~ ] -> dac~

    The delay line is the part that matters: two instances of an abstraction
    that both allocate `delwrite~ echo` collide, and Pd says so on the console.
    """
    p = Patch(600, 400, 10)
    p.src = p.obj("osc~ 110")
    p.gain = p.obj("*~ 0.6")
    p.dw = p.obj("delwrite~ echo 200")
    p.dr = p.obj("delread~ echo 125")
    p.mix = p.obj("+~")
    p.dac = p.obj("dac~")
    p.link(p.src, 0, p.gain, 0)
    p.link(p.gain, 0, p.dw, 0)
    p.link(p.gain, 0, p.mix, 0)
    p.link(p.dr, 0, p.mix, 1)
    p.link(p.mix, 0, p.dac, 0)
    p.link(p.mix, 0, p.dac, 1)
    return p


def _render_full(patch_path, **kw):
    return render(str(patch_path), RenderSpec(duration=3.0, **kw))


def test_internal_delay_line_is_namespaced(tmp_path):
    p = _engine_with_delay()
    extract(p, between(p, p.gain, p.mix), "echofx", str(tmp_path))
    text = (tmp_path / "echofx.pd").read_text(encoding="utf-8")
    # Pd writes $0 escaped, as \$0 -- matching its own saved patches
    assert r"delwrite~ \$0-echo" in text
    assert r"delread~ \$0-echo" in text
    assert "delwrite~ echo" not in text


def test_namespacing_preserves_the_sound(tmp_path):
    p = _engine_with_delay()
    flat = tmp_path / "flat.pd"
    p.save(flat)
    before = analyze(_render_full(flat).audio)
    assert not before.is_silent

    extract(p, between(p, p.gain, p.mix), "echofx", str(tmp_path))
    after_path = tmp_path / "after.pd"
    p.save(after_path)
    after = analyze(_render_full(after_path).audio)

    sim = compare(after, before).similarity
    assert sim == pytest.approx(1.0, abs=1e-6), (
        f"namespaced extraction changed the sound: {sim:.4f}")


def test_two_instances_do_not_collide_on_the_delay_line(tmp_path):
    """The definitive namespacing check: Pd prints 'multiply defined' when two
    objects allocate the same delay line. With $0- they never share a name."""
    p = _engine_with_delay()
    extract(p, between(p, p.gain, p.mix), "echofx", str(tmp_path))

    host = Patch(600, 400, 10)
    o1 = host.obj("osc~ 110")
    o2 = host.obj("osc~ 220")
    fx1 = host.pd.add("echofx", x_pos=40, y_pos=150, num_inlets=1, num_outlets=1)
    fx2 = host.pd.add("echofx", x_pos=240, y_pos=150, num_inlets=1, num_outlets=1)
    dac = host.obj("dac~")
    host.link(o1, 0, fx1, 0); host.link(o2, 0, fx2, 0)
    host.link(fx1, 0, dac, 0); host.link(fx2, 0, dac, 1)
    host_path = tmp_path / "two.pd"
    host.save(host_path)

    result = _render_full(host_path)
    console = result.pd_console.lower()
    assert "multiply defined" not in console, (
        f"delay line collided across instances:\n{result.pd_console[-800:]}")
    assert "couldn't create" not in console
    r = analyze(result.audio)
    assert not r.is_silent and not r.has_nan_inf


def test_unnamespaced_delay_would_collide(tmp_path):
    """Positive control: the same two-instance host built WITHOUT namespacing
    does collide. Without this, the test above could pass for the wrong reason."""
    p = _engine_with_delay()
    # broadcast= forces the delay to stay global, i.e. defeats namespacing
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("ignore")
        extract(p, between(p, p.gain, p.mix), "sharedfx", str(tmp_path),
                broadcast=["echo"])
    assert "delwrite~ echo" in (tmp_path / "sharedfx.pd").read_text(encoding="utf-8")

    host = Patch(600, 400, 10)
    o1 = host.obj("osc~ 110")
    o2 = host.obj("osc~ 220")
    fx1 = host.pd.add("sharedfx", x_pos=40, y_pos=150, num_inlets=1, num_outlets=1)
    fx2 = host.pd.add("sharedfx", x_pos=240, y_pos=150, num_inlets=1, num_outlets=1)
    dac = host.obj("dac~")
    host.link(o1, 0, fx1, 0); host.link(o2, 0, fx2, 0)
    host.link(fx1, 0, dac, 0); host.link(fx2, 0, dac, 1)
    host_path = tmp_path / "two_shared.pd"
    host.save(host_path)

    console = _render_full(host_path).pd_console.lower()
    assert "multiply defined" in console, (
        "expected a collision without namespacing -- if this fails, the "
        "namespacing test above proves nothing")


def test_boundary_receive_stays_global_and_still_works(tmp_path):
    """The rig's pattern: an engine driven by a global receive, with no control
    inlet at all. The receive must survive extraction and still be reachable
    from the parent's send."""
    p = Patch(600, 400, 10)
    src = p.obj("osc~ 110")
    r = p.obj("r cutoff")
    filt = p.obj("lop~ 500")
    dac = p.obj("dac~")
    p.link(src, 0, filt, 0); p.link(r, 0, filt, 1)
    p.link(filt, 0, dac, 0); p.link(filt, 0, dac, 1)
    lb = p.obj("loadbang"); m = p.msg("3000"); s = p.obj("s cutoff")
    p.link(lb, 0, m, 0); p.link(m, 0, s, 0)

    flat = tmp_path / "flat2.pd"
    p.save(flat)
    before = analyze(_render_full(flat).audio)

    extract(p, [r, filt], "filtfx", str(tmp_path))
    text = (tmp_path / "filtfx.pd").read_text(encoding="utf-8")
    assert "r cutoff" in text and "$0-cutoff" not in text   # left global

    after_path = tmp_path / "after2.pd"
    p.save(after_path)
    after = analyze(_render_full(after_path).audio)
    assert compare(after, before).similarity == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# Milestone 3: load an existing .pd, and duplicate shared-init sources
# --------------------------------------------------------------------------- #

def test_load_then_save_preserves_the_sound(tmp_path):
    """Patch.load goes through py2pd's parse -> to_builder. If that path were
    lossy, everything extracted from a file would inherit the damage."""
    p = _engine_with_delay()
    orig = tmp_path / "orig.pd"
    p.save(orig)
    before = analyze(_render_full(orig).audio)

    reloaded = Patch.load(orig)
    round_path = tmp_path / "round.pd"
    reloaded.save(round_path)
    after = analyze(_render_full(round_path).audio)
    assert compare(after, before).similarity == pytest.approx(1.0, abs=1e-6)


def test_loadbang_is_duplicated_across_the_cut(tmp_path):
    """A [loadbang] that inits both the engine and the parent must not become a
    port. Each side gets its own copy -- the engine self-initialises, the
    parent keeps its own init, and the abstraction exposes no extra outlet."""
    p = Patch(600, 400, 10)
    lb = p.obj("loadbang")
    # engine: a gain whose level is set at load, feeding dac~
    gain = p.obj("*~")
    setg = p.msg("0.5")
    osc = p.obj("osc~ 220")
    dac = p.obj("dac~")
    p.link(osc, 0, gain, 0)
    p.link(lb, 0, setg, 0); p.link(setg, 0, gain, 1)
    p.link(gain, 0, dac, 0); p.link(gain, 0, dac, 1)
    # parent-side init that the SAME loadbang drives
    dspmsg = p.msg("; pd dsp 1")
    p.link(lb, 0, dspmsg, 0)

    flat = tmp_path / "flat.pd"
    p.save(flat)
    before = analyze(_render_full(flat).audio)

    # region = the engine (gain, its setter, the osc), NOT the parent dsp msg,
    # but loadbang gets pulled in because it feeds the engine too.
    # NOTE the name: [voice]/[voices] are real objects on the ELSE path, and an
    # abstraction that shadows a path object silently never loads.
    region = [lb, gain, setg, osc]
    inst = extract(p, region, "dupvoice", str(tmp_path))

    # loadbang did not become a port: the engine has one signal outlet (the
    # mono gain, fanning to both dac~ inlets) and no inlets at all.
    assert inst.num_inlets == 0 and inst.num_outlets == 1
    # the abstraction has its own loadbang
    atext = (tmp_path / "dupvoice.pd").read_text(encoding="utf-8")
    assert "loadbang" in atext
    # the parent has its own loadbang too (a copy), still driving `; pd dsp 1`
    ptext = p.render()
    assert ptext.count("loadbang") == 1

    after_path = tmp_path / "after.pd"
    p.save(after_path)
    after = analyze(_render_full(after_path).audio)
    assert compare(after, before).similarity == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# Composability: extract() runs on its own output (nested abstractions)
# --------------------------------------------------------------------------- #

def test_extract_is_composable_on_its_own_output(tmp_path):
    """The headline gap: an abstraction instance's signal outlet must type as
    signal on the SECOND cut, or the nested abstraction gets an [outlet] and
    goes silent. The region is JUST the [vfilt] instance, so its outlet IS the
    boundary crossing -- if the resolver were still keyed off `~`, this renders
    silent. Verified by ear. (Abstractions written next to the parent so Pd
    finds them as siblings.)"""
    p = Patch(600, 400, 10)
    osc = p.obj("osc~ 220"); filt = p.obj("lop~ 800")
    gain = p.obj("*~ 0.5"); dac = p.obj("dac~")
    p.chain(osc, filt, gain); p.link(gain, 0, dac, 0); p.link(gain, 0, dac, 1)

    flat = tmp_path / "flat.pd"; p.save(flat)
    before = analyze(_render_full(flat).audio)

    # first cut: pull the filter into [vfilt] (1 signal inlet, 1 signal outlet)
    extract(p, [filt], "vfilt", str(tmp_path))
    # second cut: JUST the [vfilt] instance -> its outlet crosses to `gain`, so
    # the boundary outlet type comes from the abstraction, not a `~` name.
    vfilt = next(n for n in p.pd.nodes if node_text(n) == "vfilt")
    extract(p, [vfilt], "vstage", str(tmp_path))

    after_path = tmp_path / "after.pd"; p.save(after_path)
    after = analyze(_render_full(after_path).audio)
    assert compare(after, before).similarity == pytest.approx(1.0, abs=1e-6), (
        "nested extraction changed the sound -- the abstraction outlet likely "
        "typed as control")

    # the nested abstraction's boundary port is a SIGNAL outlet, and it holds
    # the [vfilt] instance (nesting, not a re-typed copy)
    vstage = (tmp_path / "vstage.pd").read_text(encoding="utf-8")
    assert "outlet~;" in vstage and "outlet;" not in vstage
    assert "vfilt" in vstage


def test_extract_after_reload_resolves_abstraction_ports(tmp_path):
    """Patch.load records source_dir, so a reloaded patch's abstraction outlets
    resolve without the caller passing search_dirs. All abstractions live next
    to the parent so Pd resolves them as siblings."""
    p = Patch(600, 400, 10)
    osc = p.obj("osc~ 330"); filt = p.obj("bp~ 1200 4")
    gain = p.obj("*~ 0.4"); dac = p.obj("dac~")
    p.chain(osc, filt, gain); p.link(gain, 0, dac, 0); p.link(gain, 0, dac, 1)
    p.save(tmp_path / "flat.pd")
    before = analyze(_render_full(tmp_path / "flat.pd").audio)
    extract(p, [filt], "eng", str(tmp_path))
    p.save(tmp_path / "parent.pd")

    # reload from disk (source_dir set automatically) and extract JUST [eng],
    # WITHOUT passing search_dirs -- source_dir must carry the resolution.
    reloaded = Patch.load(tmp_path / "parent.pd")
    assert reloaded.source_dir is not None
    eng = next(n for n in reloaded.pd.nodes if node_text(n) == "eng")
    extract(reloaded, [eng], "engstage", str(tmp_path))
    reloaded.save(tmp_path / "after.pd")

    after = analyze(_render_full(tmp_path / "after.pd").audio)
    assert compare(after, before).similarity == pytest.approx(1.0, abs=1e-6)
    stage = (tmp_path / "engstage.pd").read_text(encoding="utf-8")
    assert "outlet~;" in stage and "outlet;" not in stage
