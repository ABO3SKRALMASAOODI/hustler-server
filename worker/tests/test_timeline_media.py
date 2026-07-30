"""Round 54: the timeline draws real frames and real sound.

Pure logic — no ffmpeg, no DB, no network (the one ffmpeg-backed function,
peaks(), is exercised by its callers' contracts rather than by decoding a
file here). Run from worker/:
    python tests/test_timeline_media.py

The gate assertions matter more than the geometry ones. A version gate on
artwork is the same shape as the OUTRO/TRANSITION gates that took Download
down platform-wide on 2026-07-27, and the rule learned there is pinned here:
a gate may ask for a rebuild, it may NOT withhold what it already has.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import filmstrip                                             # noqa: E402

PASS = 0


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


print("\n— sheet geometry —")

n, interval, cols, rows = filmstrip.plan(60.0)
check("a minute of video is sampled at least twice a second",
      n >= 100 and abs(interval * n - 60.0) < 1e-6)
check("the grid never exceeds the 10-wide texture-safe layout", cols <= 10)

n2, _, c2, r2 = filmstrip.plan(60.0, max_tiles=filmstrip.ASSET_MAX_TILES)
check("an asset sheet is bounded by its own, much smaller cap",
      n2 == filmstrip.ASSET_MAX_TILES and c2 * r2 >= n2)

check("a bundled library track gets a key even though it has no path",
      filmstrip.asset_storage_key(7, "library:abducted", 1)
      != filmstrip.asset_storage_key(7, "library:other", 1))
check("the same asset in the same project resolves to ONE object",
      filmstrip.asset_storage_key(7, "clips/7/a.mp4", 16)
      == filmstrip.asset_storage_key(7, "clips/7/a.mp4", 16))
check("a re-sampled asset does not overwrite its own older sheet",
      filmstrip.asset_storage_key(7, "clips/7/a.mp4", 16)
      != filmstrip.asset_storage_key(7, "clips/7/a.mp4", 1))


print("\n— waveform transport —")

check("no audio encodes to nothing, never to a flat fake envelope",
      filmstrip.encode_peaks(None) is None
      and filmstrip.encode_peaks([]) is None)

_vals = [0, 7, 128, 255, 3]
_enc = filmstrip.encode_peaks(_vals)
import base64                                                # noqa: E402
check("peaks survive the base64 round trip byte for byte",
      list(base64.b64decode(_enc)) == _vals)
check("an envelope is smaller than the URL that would fetch it "
      "(the whole reason it is not a storage object)",
      len(filmstrip.encode_peaks([200] * filmstrip.WAVE_POINTS_ASSET)) < 600)

_src = open(os.path.join(os.path.dirname(__file__), "..",
                         "filmstrip.py")).read()
check("the PCM decode writes a FILE — media.run reads its child as text "
      "with errors='replace', which would mangle every sample above 0x7f",
      '"-f", "s16le"' in _src and '-f", "s16le", "-acodec"' in _src)
check("the envelope is normalised to the file's own peak, so a quiet "
      "voiceover is not drawn as a flat line beside a mastered song",
      "255.0 / peak_all" in _src)

_media = open(os.path.join(os.path.dirname(__file__), "..",
                           "media.py")).read()


print("\n— the rebuild gate —")

_bev = open(os.path.join(os.path.dirname(__file__), "..", "..",
                         "backend", "routes", "video.py")).read()
check("the backend mirrors TIMELINE_MEDIA_VERSION",
      f"TIMELINE_MEDIA_VERSION = {filmstrip.TIMELINE_MEDIA_VERSION}" in _bev)

# The round-53 rule, asserted structurally: every branch that decides a
# rebuild is needed still answers with `payload` when there is one.
_route = _bev[_bev.index("def filmstrip(user_id, project_id)"):
              _bev.index("@video_bp.route", _bev.index(
                  "def filmstrip(user_id, project_id)"))]
_after = _route[_route.index('payload = {"available": True'):]
_returns = re.findall(r"return jsonify\(([^\n]*)", _after)
check("every answer after the payload is built still serves it",
      _returns and all("payload" in r for r in _returns))
check("a strip built by an older worker is served, not withheld",
      "if fresh:" in _route and "return jsonify(payload)" in _route)
check("asking for a rebuild keeps the client polling",
      "rebuilding=True" in _route)
check("the gate can give up — an unwritten stamp must not enqueue forever",
      "MAX_FILMSTRIP_BUILDS_PER_SIG" in _bev
      and "same_sig" in _route)
check("the budget is counted per ASSET SET, so a user who keeps adding "
      "clips keeps getting artwork",
      "payload ->> 'sig'" in _route)
check("the fingerprint the worker echoes is the one the backend supplied",
      'payload.get("sig")' in _src
      and '"sig": want_sig' in _route)
# ROUND 61. Project 246 spent both of its builds on a worker that OOM-killed
# itself decoding a 4K insert; without this the fix could never reach the
# timeline it was written for, because two corpses still said "tried twice".
check("the budget is per BUILDER VERSION too, so a fix to this job can "
      "reach the projects whose two builds died under the bug",
      "payload ->> 'tm_v'" in _route
      and '"tm_v": str(TIMELINE_MEDIA_VERSION)' in _route)
check("bundled library music is in the fingerprint — it is not an asset, "
      "so nothing else would ever notice it arriving",
      "'music'" in _bev[_bev.index("def _timeline_media_sig"):
                        _bev.index("def _presigned_timeline_media")])


print("\n— cost bounds —")

check("one job never decodes an unbounded number of assets",
      filmstrip.MAX_ASSETS <= 20 and "todo[:MAX_ASSETS]" in _src)
check("a dropped asset is reported, not silently swallowed",
      "past the" in _src and "cap got no artwork" in _src)
check("a feature-length 'insert' gets one poster frame, not a linear decode",
      filmstrip.ASSET_STRIP_MAX_S <= 300 and "ASSET_STRIP_MAX_S" in _src)

# ── round 61: the asset strip must not decode the user's own 4K file ────────
# A 260 MB 23.86s 3840x2160 HEVC clip dropped on the timeline cost 94.4s of CPU
# and 620 MB of RSS to produce sixteen 160x90 thumbnails — on the DISPATCHER,
# which also runs agent turns. It OOM-killed the process three times, and took
# a user's agent turn down with it ("lost connection"). Seeks + one thread:
# 10.2s CPU, 230 MB.
check("an ASSET is sampled by seek, never by a linear decode — there is no "
      "proxy for a clip the user dropped, so the file is theirs at their own "
      "resolution",
      "build_by_seek(local, sheet" in _src
      and "m = build(local, sheet" not in _src)
check("each seek decodes with ONE thread — frame threading allocates a 4K "
      "picture buffer per thread, which is where the 620 MB went",
      '"-threads", "1"' in _src)
check("input seek (-ss BEFORE -i), so each tile reads its own GOP and not "
      "the whole file",
      _src.index('"-ss"') < _src.index('"-i", src, "-frames:v", "1"'))
check("one asset's ffmpeg is bounded — the media lane it shares with previews "
      "must not be held by a pathological file",
      filmstrip.ASSET_FFMPEG_TIMEOUT_S <= 180
      and "timeout=ASSET_FFMPEG_TIMEOUT_S" in _src)
check("a short read is a real answer: the tiles that decoded are kept and "
      "the grid is re-planned to match, so the client's tile arithmetic "
      "cannot disagree with the image",
      "n = len(frames)" in _src and "if not frames:" in _src)
check("the per-tile temporaries are always cleaned up, even on a raise",
      "finally:" in _src[_src.index("def build_by_seek"):
                         _src.index("def _local_for_ref")])
check("media.frame_at takes the timeout the caller needs (it was pinned at "
      "120s, sized for a proxy, not for a 4K original)",
      "timeout=120" in _media and "run(cmd, timeout=timeout)" in _media)
check("one unreadable clip cannot fail the job",
      "asset {ref} skipped" in _src)
check("a bundled track is opened where it lives, never downloaded",
      "music_library.local_path" in _src)
check("a bundled track is never deleted by the cleanup that removes "
      "downloaded copies",
      "local.startswith(workdir)" in _src)
check("a still is sampled at t=0 — seeking 10% into a one-frame PNG finds "
      "nothing, and media.frame_at correctly raises, which silently dropped "
      "every image insert back to a flat rectangle",
      'at = 0.0 if kind == "image"' in _src)
check("a rebuild does not re-download an asset whose sheet already exists "
      "and which has no waveform to measure",
      'if cached_key and kind == "image"' in _src)

print("\n— the reply contract —")

import agent_prompt                                          # noqa: E402

_p = agent_prompt.SYSTEM_PROMPT if hasattr(agent_prompt, "SYSTEM_PROMPT") \
    else open(os.path.join(os.path.dirname(__file__), "..",
                           "agent_prompt.py")).read()
check("the reply length is a NUMBER, not an adjective — 'short' produced a "
      "1,200-word structured dump on a real edit",
      "TWO OR THREE SENTENCES" in _p and "Under 60 words" in _p)
check("headed sections and per-effect lists are named and banned",
      'No headings ("Structure:"' in _p and "no bullet lists" in _p)
check("no sign-off question — the reply is a handover, not a pitch",
      "No sign-off question" in _p)
check("brevity is explicitly NOT permission to soften a failure",
      "Fewer words, not fewer facts." in _p)
check("the honesty rule survived the rewrite: a quoted number still has to "
      "come from a tool result",
      "literally present in THIS turn's tool results" in _p)
check("the tools that write timestamps no longer demand they all be recited",
      "a receipt, not a report" in _p)

print(f"\n{PASS} checks passed.\n")
