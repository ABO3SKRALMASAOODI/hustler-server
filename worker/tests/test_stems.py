"""Round 97 (#7) — music/voice stem separation, end to end minus the model.

'Remove the music but keep the talking' was an honest 'impossible' (two of
seventeen engaged users asked in one 36-hour window). Demucs runs ONLY on
the executor image, so what this suite pins locally is everything AROUND the
model — and the renderer's mixing math with real ffmpeg and synthetic stems:

  * the StemMix schema, its gain bounds, and the EDL field;
  * stem_mix is an AUDIO field: a change stitches with ZERO video windows;
  * the /health feature, the RUNNERS route, the remote dispatch contract;
  * the tool: registration, honest hiding, the cache-hit path, the length
    cap, and the node it writes;
  * THE MIX ITSELF: with vocals=220Hz and accompaniment=880Hz tones,
    music_gain_db=-60 must leave 220 dominant and 880 suppressed in the
    rendered audio — and the same must hold through the audio-only pruned
    graph (the stitched-preview path), and missing stems must degrade to
    the original track instead of failing the render.

LIVE ffmpeg; the audio half is skipped without it.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np                                              # noqa: E402

import agent_tools                                              # noqa: E402
import config                                                   # noqa: E402
import http_server                                              # noqa: E402
import remote                                                   # noqa: E402
import renderer                                                 # noqa: E402
import schemas                                                  # noqa: E402
import stems                                                    # noqa: E402
import stitch                                                   # noqa: E402
import version                                                  # noqa: E402
from timeline import Timeline                                   # noqa: E402

PASS = 0
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


def tl_of(edl):
    return Timeline(edl["keep"], edl.get("inserts") or [],
                    edl.get("speed") or [])


print("== 1. schema ==")

e = schemas.validate_edl(
    {"keep": [[0.0, 10.0]],
     "stem_mix": {"vocals_key": "stems/x/vocals.m4a",
                  "accomp_key": "stems/x/accomp.m4a",
                  "music_gain_db": -60, "voice_gain_db": 0}}, 10.0
).model_dump()
check("stem_mix validates and survives the dump",
      e["stem_mix"]["music_gain_db"] == -60
      and e["stem_mix"]["vocals_key"] == "stems/x/vocals.m4a")


def raises(fn, *a):
    try:
        fn(*a)
    except Exception:
        return True
    return False


check("gains outside -60..+6 refuse",
      raises(schemas.validate_edl,
             {"keep": [[0, 10]],
              "stem_mix": {"vocals_key": "a", "accomp_key": "b",
                           "music_gain_db": -80}}, 10.0))

print("== 2. a stem change is audio-only to the stitcher ==")

check("stem_mix is in AUDIO_FIELDS", "stem_mix" in stitch.AUDIO_FIELDS)
v1 = schemas.validate_edl({"keep": [[0.0, 10.0]]}, 10.0).model_dump()
v2 = schemas.validate_edl(
    {"keep": [[0.0, 10.0]],
     "stem_mix": {"vocals_key": "stems/x/vocals.m4a",
                  "accomp_key": "stems/x/accomp.m4a",
                  "music_gain_db": -60}}, 10.0).model_dump()
w, r, why = stitch.plan_timeline(v1, v2, tl_of(v1), tl_of(v2), 10.0)
check("muting the music re-encodes ZERO video",
      w == [] and r and abs(r[0][1] - r[0][0] - 10.0) < 0.01)

print("== 3. plumbing: health, runners, remote ==")

check("the stems runner is routed", http_server.RUNNERS.get("stems")
      is stems.run_stems_job)
import inspect                                                  # noqa: E402
check("...with the (worker_db, job) signature EVERY runner is called with "
      "(http_server dispatches runner(db, job); the first live call "
      "TypeError'd on this)",
      list(inspect.signature(stems.run_stems_job).parameters)
      == ["worker_db", "job"])
_real_avail = stems.available
try:
    stems.available = lambda: True
    check("/health advertises stems when the build can run one",
          "stems" in version.version_report()["features"])
    stems.available = lambda: False
    check("...and stays silent when it cannot",
          "stems" not in version.version_report()["features"])
finally:
    stems.available = _real_avail
check("the executor timeout table covers stems",
      config.executor_timeout_for("stems") >= 600)
check("remote gates on the /health feature",
      "executor_supports(\"stems\")" in
      __import__("inspect").getsource(remote.stems_available))

print("== 4. the tool ==")

check("separate_music is registered with both gains",
      "separate_music" in agent_tools.TOOLS
      and "music_gain_db" in agent_tools.TOOLS["separate_music"][2])
check("remove_stem_mix is registered",
      "remove_stem_mix" in agent_tools.TOOLS)
check("both are WRITE tools (turn facts see their diffs)",
      "separate_music" in agent_tools.WRITE_TOOLS
      and "remove_stem_mix" in agent_tools.WRITE_TOOLS)


class FakeCtx:
    has_main_video = True
    duration = 60.0
    project_id = 1
    job = {"user_id": 7}

    def __init__(self):
        self.written = None
        self.edl = schemas.validate_edl({"keep": [[0.0, 60.0]]},
                                        60.0).model_dump()
        self.db = types.SimpleNamespace(run=lambda fn, *a: {
            "sha256": "cafe1234", "storage_key": "orig/x.mp4", "meta": {}})

    def latest_edl(self):
        return {"version": 1, "json": self.edl}

    def write_edl(self, edl, desc):
        self.written = edl
        return f"EDL v1 -> v2: {desc}"


_real_sup = agent_tools._stems_supported
_real_exists = agent_tools.storage.exists
try:
    agent_tools._stems_supported = lambda: True
    agent_tools.storage.exists = lambda k: True          # cache hit
    ctx = FakeCtx()
    out = agent_tools.separate_music(ctx, music_gain_db=-60)
    check("a cached separation writes the node without a remote call",
          out.startswith("EDL v")
          and ctx.written["stem_mix"]["music_gain_db"] == -60.0
          and ctx.written["stem_mix"]["vocals_key"]
          == "stems/cafe1234/vocals.m4a")
    check("...and the reply teaches the honesty caveat",
          "not surgical" in out)
    ctx2 = FakeCtx()
    ctx2.duration = config.STEMS_MAX_SOURCE_S + 60
    out = agent_tools.separate_music(ctx2, music_gain_db=-60)
    check("an over-long video gets the honest cap, not a hang",
          "minutes" in out and ctx2.written is None)
    agent_tools._stems_supported = lambda: False
    out = agent_tools.separate_music(FakeCtx(), music_gain_db=-60)
    check("a definite no from the render service refuses gracefully",
          "isn't available" in out)
    check("...and hides the tool entirely",
          agent_tools._tool_disabled("separate_music") is True)
finally:
    agent_tools._stems_supported = _real_sup
    agent_tools.storage.exists = _real_exists

ctx3 = FakeCtx()
out = agent_tools.remove_stem_mix(ctx3)
check("removing an absent mix says so instead of writing",
      "already" in out and ctx3.written is None)
ctx3.edl["stem_mix"] = {"vocals_key": "a", "accomp_key": "b",
                        "voice_gain_db": 0.0, "music_gain_db": -60.0}
out = agent_tools.remove_stem_mix(ctx3)
check("removing a set mix clears the node",
      out.startswith("EDL v") and ctx3.written["stem_mix"] is None)

print("== 5. the image bakes what the code names ==")

df = open(os.path.join(os.path.dirname(__file__), "..",
                       "Dockerfile")).read()
check("the demucs layer exists and is CPU-only",
      "demucs==" in df and "download.pytorch.org/whl/cpu" in df)
check("torch/torchaudio are pinned to the classic-backend pairing "
      "(unpinned = TorchCodec-era save() that demucs cannot call)",
      "torch==2.4.1" in df and "torchaudio==2.4.1" in df)
check("the baked weights match config.STEMS_MODEL",
      f"get_model('{config.STEMS_MODEL}')" in df)
check("skills teach the tool where the old 'impossible' lived",
      "separate_music" in open(os.path.join(
          os.path.dirname(__file__), "..", "skills", "audio.md")).read())

if not HAVE_FFMPEG:
    print("== 6 skipped (no ffmpeg) ==")
    print(f"\nALL {PASS} CHECKS PASSED")
    sys.exit(0)

print("== 6. the mix itself: mute the music, keep the voice ==")

d = tempfile.mkdtemp(prefix="stems_")
src = os.path.join(d, "src.mp4")
subprocess.run(
    ["ffmpeg", "-y", "-v", "error",
     "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=12",
     "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", src],
    check=True)
vocals = os.path.join(d, "vocals.m4a")
accomp = os.path.join(d, "accomp.m4a")
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "sine=frequency=220:duration=12", "-c:a", "aac", vocals],
               check=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "sine=frequency=880:duration=12", "-c:a", "aac", accomp],
               check=True)

IDX = {"video": {"duration": 12.0, "width": 320, "height": 180, "fps": 30.0},
       "words": [], "sentences": [], "silences": []}
EM = schemas.validate_edl(
    {"keep": [[0.0, 10.0]],
     "stem_mix": {"vocals_key": "stems/t/vocals.m4a",
                  "accomp_key": "stems/t/accomp.m4a",
                  "voice_gain_db": 0, "music_gain_db": -60}}, 12.0
).model_dump()

_real_cache = renderer._cached_source
renderer._cached_source = lambda k: {
    "stems/t/vocals.m4a": vocals, "stems/t/accomp.m4a": accomp}.get(k)


def tone_db(path, hz, audio_only=False):
    """Magnitude (dB) of `hz` in the file's audio, via rfft of 4 middle
    seconds at 8kHz mono."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0", "-ss", "2",
         "-t", "4", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True)
    x = np.frombuffer(r.stdout, np.int16).astype(np.float64)
    if not len(x):
        return -120.0
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1 / 8000.0)
    band = spec[(freqs > hz - 15) & (freqs < hz + 15)]
    return 20 * np.log10(float(band.max()) + 1e-9)


try:
    out = os.path.join(d, "muted_music.mp4")
    renderer.render_edl(EM, IDX, src, out, d, preview=True)
    v_db, m_db = tone_db(out, 220), tone_db(out, 880)
    check(f"voice tone survives, music tone is gone "
          f"(220Hz {v_db:.0f}dB vs 880Hz {m_db:.0f}dB)",
          v_db - m_db > 30.0)

    EM2 = dict(EM)
    EM2["stem_mix"] = dict(EM["stem_mix"], voice_gain_db=-60,
                           music_gain_db=0)
    out2 = os.path.join(d, "muted_voice.mp4")
    renderer.render_edl(EM2, IDX, src, out2, d, preview=True)
    v_db, m_db = tone_db(out2, 220), tone_db(out2, 880)
    check(f"...and the reverse mutes the voice instead "
          f"(880Hz {m_db:.0f}dB vs 220Hz {v_db:.0f}dB)",
          m_db - v_db > 30.0)

    aud = os.path.join(d, "stems_audio.m4a")
    renderer.render_edl(EM, IDX, src, aud, d, preview=True, audio_only=True)
    v_db, m_db = tone_db(aud, 220), tone_db(aud, 880)
    check(f"the pruned audio-only graph mixes stems identically "
          f"(220Hz {v_db:.0f}dB vs 880Hz {m_db:.0f}dB)",
          v_db - m_db > 30.0)
finally:
    renderer._cached_source = _real_cache

# stems unavailable at render time -> the original track, not a dead render
_real_dl = renderer.storage.download_to
renderer._cached_source = lambda k: None
renderer.storage.download_to = (lambda key, path, **kw:
                                (_ for _ in ()).throw(RuntimeError("gone"))
                                if key.startswith("stems/") else None)
try:
    out3 = os.path.join(d, "fallback.mp4")
    renderer.render_edl(EM, IDX, src, out3, d, preview=True)
    o_db = tone_db(out3, 440)
    check(f"missing stems degrade to the ORIGINAL audio "
          f"(440Hz at {o_db:.0f}dB)", o_db > 40.0)
finally:
    renderer._cached_source = _real_cache
    renderer.storage.download_to = _real_dl

shutil.rmtree(d, ignore_errors=True)
print(f"\nALL {PASS} CHECKS PASSED")
