"""Round 47 — using ONLY the audio of an uploaded video.

The bug this pins, from prod on 2026-07-26: a user wanted the song off a
TikTok. The only file anyone ever has for that is the VIDEO, so they attached
it — four times — and every audio tool answered "not a music asset here". The
agent believed the product could not do it and told them to convert the file
themselves. Nothing was actually missing: the renderer has always been able to
read the audio stream out of an mp4.

Two halves, both here:

  * media.extract_audio_track — the ffmpeg primitive (LIVE ffmpeg; skipped
    where there is none). It must copy AAC rather than re-encode it, must
    write a file with no video stream at all, and must RAISE on a silent
    source rather than hand back a file of silence.
  * the resolvers — add_music / add_sfx / add_voiceover / get_audio_analysis
    must accept a [video_clip] key and store the EXTRACTED key in the EDL, not
    the video's. Faked DB + storage, so this half runs anywhere.
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import media                                                   # noqa: E402
import agent_tools                                             # noqa: E402
from schemas import default_edl                                # noqa: E402

PASS = 0
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


# ── 1. the ffmpeg primitive ────────────────────────────────────────────

print("== 1. extract_audio_track ==")

if not HAVE_FFMPEG:
    print("  -- skipped (no ffmpeg on this machine)")
else:
    _d = tempfile.mkdtemp(prefix="clipaudio_")
    _av = os.path.join(_d, "song.mp4")
    _silent = os.path.join(_d, "silent.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", _av], check=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2",
         "-an", "-c:v", "libx264", _silent], check=True)

    check("audio_stream_of reads the codec off a video",
          (media.audio_stream_of(_av) or {}).get("codec") == "aac")
    check("audio_stream_of returns None for a silent video",
          media.audio_stream_of(_silent) is None)

    _out = os.path.join(_d, "song.m4a")
    _dur = media.extract_audio_track(_av, _out)
    check("extraction returns the real duration", 2.5 < _dur < 3.5)
    check("extraction writes a non-empty file", os.path.getsize(_out) > 0)

    # The whole point: the scene is HIDDEN. A file that still carried the
    # picture would play video wherever the renderer treats it as audio, and
    # would cost the user bandwidth on every render.
    _probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", _out], capture_output=True, text=True)
    _types = [t for t in _probe.stdout.split() if t]
    check("the extracted file has NO video stream", "video" not in _types)
    check("the extracted file has audio", "audio" in _types)
    check("AAC is stream-copied, not re-encoded",
          (media.audio_stream_of(_out) or {}).get("codec") == "aac")

    # The renderer feeds a music item as `-stream_loop -1 -i <file>` and pulls
    # [n:a] into the mix. Prove the extracted container survives that shape —
    # an extraction the renderer cannot read is a silent failure at export.
    _mixed = os.path.join(_d, "mixed.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", _av,
         "-stream_loop", "-1", "-i", _out,
         "-filter_complex",
         "[1:a]atrim=0:2,asetpts=PTS-STARTPTS,volume=-4dB[m];"
         "[0:a][m]amix=inputs=2:duration=first[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
         "-t", "2", _mixed], check=True)
    check("the extracted audio mixes as a music input the renderer's way",
          os.path.getsize(_mixed) > 0
          and (media.audio_stream_of(_mixed) or {}).get("codec") == "aac")

    # A silent clip must fail LOUDLY. Returning a file of silence here is how
    # an agent ends up telling a user their song was added when it was not.
    try:
        media.extract_audio_track(_silent, os.path.join(_d, "nope.m4a"))
        raise AssertionError("FAIL: a silent video must raise, not produce "
                             "a file of silence")
    except media.MediaError as e:
        check("a silent video raises 'no audio stream'",
              "no audio stream" in str(e))

    shutil.rmtree(_d, ignore_errors=True)


# ── 2. the resolvers ───────────────────────────────────────────────────

print("== 2. an uploaded VIDEO resolves to its audio ==")

CLIP = {"id": 959, "kind": "video_clip", "storage_key": "clips/1/abc.mp4",
        "duration_s": 126.7, "sha256": "deadbeef",
        "meta": {"filename": "tiktok_song.mp4"}}
SILENT_CLIP = {"id": 960, "kind": "video_clip",
               "storage_key": "clips/1/silent.mp4", "duration_s": 10.0,
               "sha256": "cafe", "meta": {"filename": "no_sound.mp4"}}


class FakeDB:
    """Just enough of worker.db for the resolvers: assets by key, the
    extraction cache, and inserts."""

    def __init__(self, assets):
        self.assets = list(assets)
        self.inserted = []
        self._next_id = 1000

    def run(self, fn, *a, **kw):
        name = getattr(fn, "__name__", "")
        if name == "asset_by_key":
            _pid, key = a
            return next((x for x in self.assets
                         if x["storage_key"] == key), None)
        if name == "latest_asset":
            _pid, kind = a
            return next((x for x in reversed(self.assets)
                         if x["kind"] == kind), None)
        if name == "extracted_audio_asset":
            _pid, src_key, sha = a
            return next((x for x in self.assets
                         if x["kind"] == "music"
                         and ((x.get("meta") or {}).get("from_asset_key")
                              == src_key
                              or (sha and (x.get("meta") or {}).get(
                                  "from_sha256") == sha))), None)
        if name == "assets_by_kinds":
            _pid, kinds = a[0], a[1]
            return [x for x in self.assets if x["kind"] in kinds]
        if name == "insert_asset":
            _pid, kind, key = a
            row = {"id": self._next_id, "kind": kind, "storage_key": key,
                   "duration_s": kw.get("duration_s"),
                   "meta": kw.get("meta") or {}}
            self._next_id += 1
            self.assets.append(row)
            self.inserted.append(row)
            return row["id"]
        if name == "update_asset_meta":
            return None
        raise AssertionError(f"unexpected db call {name}")


class FakeCtx:
    def __init__(self, assets, duration=43.8):
        self.db = FakeDB(assets)
        self.project_id = 1
        self.workdir = tempfile.mkdtemp(prefix="ctx_")
        self.duration = duration
        self.index = {"video": {"duration": duration}, "sentences": []}
        self.has_main_video = True
        self._asset_locals = {}
        self._asset_perception = {}
        self.audio_extracted = []
        self.written = []
        self._edl = default_edl(duration)

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = edl
        self.written.append(desc)
        return f"EDL v{len(self.written)} -> v{len(self.written) + 1}: {desc}"

    def clamp(self, t):
        return max(0.0, min(float(t), self.duration))


EXTRACTED = []


def _fake_extract(local, out):
    """Stand in for ffmpeg: write a marker file, report a duration."""
    EXTRACTED.append((local, out))
    with open(out, "wb") as f:
        f.write(b"m4a")
    return 54.5


def _install_fakes():
    agent_tools.media.extract_audio_track = _fake_extract
    agent_tools._asset_local_path = lambda ctx, asset: os.path.join(
        ctx.workdir, "local.mp4")
    agent_tools.storage.upload_file = lambda path, key, ct: None


_real = (media.extract_audio_track, agent_tools._asset_local_path,
         agent_tools.storage.upload_file)
_install_fakes()

# The exact call the prod agent made, which used to be REJECTED.
ctx = FakeCtx([CLIP])
res = agent_tools.add_music(ctx, CLIP["storage_key"])
check("add_music on a video_clip is accepted", res.startswith("EDL v"))
mus = ctx._edl["music"][0]
check("the EDL stores the EXTRACTED key, never the video's",
      mus["storage_key"].startswith("music/1/")
      and mus["storage_key"].endswith(".m4a"))
check("the extraction is registered as a music asset",
      ctx.db.inserted and ctx.db.inserted[0]["kind"] == "music")
check("the source video is recorded on the new asset",
      (ctx.db.inserted[0]["meta"] or {}).get("from_asset_key")
      == CLIP["storage_key"])
check("the turn records that an asset was created (honesty layer reads it)",
      len(ctx.audio_extracted) == 1)
check("the tool result says the picture is not used",
      "picture appears NOWHERE" in res)

# What the agent reads back is what it repeats to the user. A random storage
# key here ("7f3a91b2c4d5.m4a") is how a user gets told about a file they do
# not recognise instead of the song they attached.
_gain = agent_tools.set_audio_gain(ctx, "music", mus["id"], -8.0)
check("later tools name the file the user knows, not the hex key",
      "(audio).m4a" in _gain)

# Extract ONCE. Resolving the same clip again (music, then analysis, then a
# swap) must not re-download and re-encode a 500MB clip per call.
_before = len(EXTRACTED)
_again = agent_tools.add_music(ctx, CLIP["storage_key"], start=0, end=5)
check("a second resolve of the same clip reuses the extraction",
      len(EXTRACTED) == _before)
check("the cached path still says the picture is not used",
      "picture appears NOWHERE" in _again)

# A re-upload of the SAME bytes is a different storage object. The user in
# prod attached the identical file four times; each must not pay again.
ctx2_assets = list(ctx.db.assets) + [dict(CLIP, id=961,
                                          storage_key="clips/1/dup.mp4")]
ctx2 = FakeCtx(ctx2_assets)
ctx2.db.inserted = []
_before = len(EXTRACTED)
agent_tools.add_music(ctx2, "clips/1/dup.mp4")
check("a re-upload of the same bytes reuses the extraction (matched on sha)",
      len(EXTRACTED) == _before and not ctx2.db.inserted)

# Same door for one-shots and narration.
ctx3 = FakeCtx([CLIP])
res3 = agent_tools.add_sfx(ctx3, CLIP["storage_key"], at=1.0)
check("add_sfx on a video_clip is accepted", res3.startswith("EDL v"))
check("sfx stores the extracted key",
      ctx3._edl["sfx"][0]["storage_key"].endswith(".m4a"))

ctx4 = FakeCtx([CLIP])
res4 = agent_tools.add_voiceover(ctx4, CLIP["storage_key"])
check("add_voiceover on a video_clip is accepted", res4.startswith("EDL v"))
check("voiceover stores the extracted key",
      ctx4._edl["voiceover"][0]["asset_key"].endswith(".m4a"))

# A precise sentence from the main source can be reused over a second scene.
# This is intentionally legal only through the voiceover role: add_music and
# extract_audio still guard against accidentally doubling the whole source.
main = {"id": 1, "kind": "original", "storage_key": "originals/1/a.mov",
        "duration_s": 43.8, "meta": {"filename": "main.mov"}}
ctx4b = FakeCtx([main])
res4b = agent_tools.add_voiceover(
    ctx4b, "main", start_output_s=33.83, source_offset_s=4.05,
    duration_s=5.48)
check("a bounded main-source dialogue excerpt is accepted as voiceover",
      res4b.startswith("EDL v"))
check("main-source excerpt stores exact source, destination and duration",
      ctx4b._edl["voiceover"][0] == {
          "id": "vo1", "asset_key": "originals/1/a.mov",
          "start_output_s": 33.83, "source_offset_s": 4.05,
          "duration_s": 5.48, "gain_db": 0.0, "duck_others": True})

# The explicit tool.
ctx5 = FakeCtx([CLIP])
res5 = agent_tools.extract_audio(ctx5, CLIP["storage_key"])
check("extract_audio returns a usable storage_key",
      "storage_key=music/1/" in res5)
check("extract_audio says nothing is in the edit yet",
      "Nothing is in the edit yet" in res5)

# The main video is NOT a legal source: its audio is already in the program,
# and layering it over itself is the round-2 inaudible-music bug.
ctx6 = FakeCtx([main])
res6 = agent_tools.extract_audio(ctx6, "originals/1/a.mov")
check("extract_audio refuses the MAIN video and points at set_volume",
      res6.startswith("REJECTED") and "set_volume" in res6)

# A silent clip is the one honest no.
def _fake_extract_silent(local, out):
    raise media.MediaError("no audio stream")


agent_tools.media.extract_audio_track = _fake_extract_silent
ctx7 = FakeCtx([SILENT_CLIP])
res7 = agent_tools.add_music(ctx7, SILENT_CLIP["storage_key"])
check("a silent clip is refused honestly, with the reason",
      res7.startswith("REJECTED") and "no sound in it" in res7)
check("nothing was written for a silent clip", not ctx7.written)
agent_tools.media.extract_audio_track = _fake_extract

# list_assets must ADVERTISE this where the agent is already looking.
ctx8 = FakeCtx([CLIP])
listed = agent_tools.list_assets(ctx8, "all")
check("list_assets tells the agent a clip can be used as sound only",
      "SOUND ONLY" in listed)

(media.extract_audio_track, agent_tools._asset_local_path,
 agent_tools.storage.upload_file) = _real

print(f"\n{PASS} checks passed.")
