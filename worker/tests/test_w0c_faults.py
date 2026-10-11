"""AT-0C-3 — the fault-injection gate (track 0C; INTERFACES.md §6.2).

Every fault mode × every render spec gives ZERO render failures: a creative
device may degrade, it never fails a render. This file is the CI gate every
later wave inherits — a new device, stage or sidecar that turns an injected
fault into a failed render fails here.

Each cell injects one pinned fault (faultinject.inject) around a real local
preview render of one spec on a small synthetic source, resolves the spec's
devices under the same fault, and checks the render succeeded with the
program's exact length and a playable picture.

Consult sites belong to the tracks that own the failing code (0A sidecars,
remote, models; 0B fxgraph, motion_engine). Where a site has not merged
yet, the cell reproduces the real failure itself (a Chromium page that
dies mid-render) so the degrade path is still exercised; once the owner's
site lands, the injection reaches it directly.
"""

import inspect
import os
import shutil
import subprocess

import pytest

from w0c_support import six_matte_cards, use_cards

import devices  # noqa: E402
import faultinject  # noqa: E402
import motion_engine  # noqa: E402
import motion_layer  # noqa: E402
import renderer  # noqa: E402
import sound_library  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                reason="ffmpeg required")

FAULTS = ("none",) + faultinject.FAULTS
SRC_S = 12.0
WORKER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── inputs ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("w0c_faults")
    src, song, still = (str(d / "src.mp4"), str(d / "song.m4a"),
                        str(d / "still.png"))
    run = dict(check=True, capture_output=True, timeout=120)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s=640x360:r=30:d={SRC_S}", "-f", "lavfi",
                    "-i", f"sine=frequency=330:sample_rate=48000:"
                          f"duration={SRC_S}",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", src], **run)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=550:sample_rate=48000:duration=8",
                    "-c:a", "aac", song], **run)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x2a6fdb:s=640x360:d=1", "-frames:v", "1",
                    still], **run)
    return {"src": src, "song": song, "still": still, "dir": d}


def _words():
    text = ("this changed everything about how we build companies and the "
            "number was four hundred million").split()
    return [{"w": w, "t0": round(1.2 + i * 0.45, 2),
             "t1": round(1.2 + i * 0.45 + 0.35, 2), "speaker": "S0"}
            for i, w in enumerate(text)]


def _index(words=()):
    words = list(words)
    return {"words": words, "silences": [],
            "sentences": ([{"t0": words[0]["t0"], "t1": words[-1]["t1"],
                            "text": " ".join(w["w"] for w in words)}]
                          if words else []),
            "video": {"duration": SRC_S, "width": 640, "height": 360,
                      "fps": 30.0}}


def _spec_plain(m):
    edl = default_edl(SRC_S)
    edl["keep"] = [[1.0, 5.0], [6.0, 9.0]]
    edl["frame"] = {"ratio": "9:16", "mode": "crop"}
    edl["effects"] = {"grade": "warm",
                      "stylize": [{"id": "gr", "kind": "grain",
                                   "intensity": 0.2}]}
    return edl, _index(), {}


def _spec_captions(m):
    edl = default_edl(SRC_S)
    edl["keep"] = [[1.0, 8.0]]
    edl["frame"] = {"ratio": "9:16", "mode": "crop"}
    edl["captions"] = {"mode": "from_transcript",
                       "style": {"preset": "clean",
                                 "motion_look": "editorial"}}
    return edl, _index(_words()), {}


def _spec_motion(m):
    edl = default_edl(SRC_S)
    edl["keep"] = [[1.0, 7.0]]
    edl["motion"] = [{"id": "mg1", "template": "hook_title", "start": 0.5,
                      "end": 2.5, "params": {"text": "Big *idea*"}},
                     {"id": "mg2", "template": "hook_title", "start": 3.0,
                      "end": 5.0, "params": {"text": "Second *beat*"}}]
    return edl, _index(), {}


def _spec_audio(m):
    sid = "whoosh_soft_1"
    key = sound_library.asset_key(7, sid)
    edl = default_edl(SRC_S)
    edl["keep"] = [[0.5, 6.5]]
    edl["music"] = [{"id": "mus1", "storage_key": "legacy-music/x.mp3",
                     "start": 0.0, "end": 6.0, "gain_db": -18.0,
                     "duck": True}]                 # legacy: no provenance
    edl["sfx"] = [{"id": "s1", "storage_key": key, "at": 2.0,
                   "purpose": "the turn"}]
    return edl, _index(), {"legacy-music/x.mp3": m["song"],
                           key: sound_library.path(sid)}


def _spec_inserts_flash(m):
    edl = default_edl(SRC_S)
    edl["keep"] = [[1.0, 4.0], [7.0, 10.0]]
    edl["inserts"] = [{"id": "ins1", "asset_key": "img/7/still.png",
                       "kind": "image", "at_output_s": 3.0,
                       "duration_s": 1.0}]
    edl["effects"] = {"transition": {"style": "flash", "duration_s": 0.2,
                                     "scope": "every_cut"},
                      "stylize": [{"id": "fl", "kind": "flash",
                                   "start": 5.0, "end": 5.1,
                                   "intensity": 0.8}]}
    return edl, _index(), {"img/7/still.png": m["still"]}


def _spec_future_fx(m):
    """A writer one version ahead (executor skew): an fx type this lane
    does not register. The render path strips it (rung omit)."""
    edl = default_edl(SRC_S)
    edl["keep"] = [[1.0, 6.0]]
    edl["fx"] = [{"id": "fx1", "type": "subject", "stage": "world",
                  "start": 1.0, "end": 1.5, "device": "subject_fill_flash"}]
    return edl, _index(), {}


SPECS = {"plain": _spec_plain, "captions": _spec_captions,
         "motion": _spec_motion, "audio": _spec_audio,
         "inserts_flash": _spec_inserts_flash, "future_fx": _spec_future_fx}


def _program_s(edl):
    from schemas import program_duration
    return float(program_duration(edl))


def _render_path_strips_unknown_fx():
    return "strip_unknown_fx" in inspect.getsource(renderer)


def _fxgraph_merged():
    return os.path.exists(os.path.join(WORKER, "fxgraph.py"))


# ── the real failure where the owner's consult site is not merged yet ──

def _native_site(name):
    """Does the owning module already consult this fault?"""
    mod = {"chromium_crash": motion_engine}.get(name)
    if mod is None:
        return True
    return f'fault("{name}' in inspect.getsource(mod)


@pytest.fixture
def real_failure(monkeypatch):
    def arm(fault):
        if fault == "chromium_crash" and not _native_site(fault):
            async def crashed(browser, job, out_path, deadline):
                raise RuntimeError("Target page, context or browser has "
                                   "been closed")
            monkeypatch.setattr(motion_engine, "_render_one", crashed)
            monkeypatch.setattr(motion_engine, "_cache_get", lambda key: None)
    return arm


def _sidecar_status(fault):
    return {"sidecar_timeout": "pending", "model_missing": "failed",
            "oom": "failed"}.get(fault, "ready")


# ── the matrix ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("fault", FAULTS)
@pytest.mark.parametrize("spec", sorted(SPECS))
def test_every_fault_times_every_spec_renders(spec, fault, media, tmp_path,
                                              monkeypatch, real_failure):
    if spec == "future_fx" and not _render_path_strips_unknown_fx():
        if _fxgraph_merged():
            pytest.fail("0B merged but the render path does not call "
                        "schemas.strip_unknown_fx: executor skew would fail "
                        "renders")
        pytest.skip("waiting for 0B's render-path wiring "
                    "(schemas.strip_unknown_fx in renderer)")
    use_cards(monkeypatch, six_matte_cards(), tmp_path / "cards")
    edl, index, locals_ = SPECS[spec](media)
    norm = edl if spec == "future_fx" else \
        validate_edl(edl, SRC_S).model_dump()
    want = _program_s(norm if spec != "future_fx" else
                      dict(edl, fx=[]))
    out = str(tmp_path / "out.mp4")
    warnings = []
    real_failure(fault)
    names = () if fault == "none" else (fault,)
    with faultinject.inject(*names):
        # the device report resolves under the same fault, never raising
        rows = devices.plan_rows(
            [{"id": "fx1", "device": "subject_fill_flash", "stage": "world",
              "start": 1.0, "end": 1.5}],
            lane="local",
            sidecar_status={"matte.person": _sidecar_status(fault)})
        assert len(rows) == 1 and rows[0]["rung"] in devices.RUNGS
        if fault in ("model_missing", "sidecar_timeout", "oom",
                     "ffmpeg_filter_missing"):
            assert rows[0]["rung"] != "full", rows[0]
        token = motion_layer.collect_warnings(warnings)
        try:
            dur = renderer.render_edl(norm, index, media["src"], out,
                                      str(tmp_path), True,
                                      suppress_outro=True,
                                      asset_locals=locals_)
        finally:
            motion_layer.stop_collecting(token)
    assert dur == pytest.approx(want, abs=0.07), (spec, fault, dur, want)
    probe = renderer.media.probe(out)
    assert probe.get("width") and probe.get("height"), probe
    assert float(probe.get("duration") or 0) == pytest.approx(want, abs=0.12)
    if spec == "motion" and fault == "chromium_crash":
        # the crash is named, never silent (and the render still shipped)
        assert warnings and all("mg" in w for w in warnings), warnings


def test_the_suite_covers_every_pinned_fault():
    assert set(FAULTS) - {"none"} == set(faultinject.FAULTS)
    assert set(faultinject.CONSULT_SITES) == set(faultinject.FAULTS)
    assert len(SPECS) >= 6
