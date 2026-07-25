"""Round 43 — the four prod failures from 2026-07-25, pinned as tests.

Every case here is something a real user hit that day, reproduced without
ffmpeg, a DB or a network. Run from the worker/ directory.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import media                                                 # noqa: E402
import videogen                                              # noqa: E402
from schemas import EDLValidationError, default_edl, validate_edl  # noqa: E402

PASS = 0


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


print("== 1. ffmpeg's log is not UTF-8, and must never fail a job ==")

# ffmpeg echoes container metadata verbatim: a Shift-JIS title, a CP-1251
# artist, or raw bytes inside a decode warning on damaged input. With strict
# decoding that raised UnicodeDecodeError out of subprocess.run itself — not a
# MediaError — so every caller's `except MediaError` missed it and the agent
# turn died. Real message from prod: "'utf-8' codec can't decode byte 0xf9 in
# position 8695: invalid start byte".
_BAD = b"Metadata:\n  title: \xf9\xfe\xff broken\nframe= 1 fps=0\n"

_script = (
    "import sys;"
    "sys.stdout.buffer.write(%r);"
    "sys.stderr.buffer.write(%r);"
    "sys.exit(%d)"
)


def _run_bytes(rc):
    return media.run([sys.executable, "-c", _script % (_BAD, _BAD, rc)],
                     timeout=30)


try:
    out = _run_bytes(0)
    check("non-UTF-8 stdout/stderr on success decodes instead of raising",
          isinstance(out, str) and "broken" in out)
except UnicodeDecodeError:
    raise AssertionError("FAIL: run() still decodes strictly on the happy path")

try:
    _run_bytes(3)
    raise AssertionError("FAIL: a nonzero exit must raise MediaError")
except media.MediaError as e:
    check("non-UTF-8 stderr on FAILURE surfaces as MediaError, not "
          "UnicodeDecodeError", "broken" in str(e))
except UnicodeDecodeError:
    raise AssertionError("FAIL: run() still decodes strictly on the error path")

# The progress branch is a different code path with its own decoder, and it is
# the one every long encode goes through.
_seen = []
try:
    media.run([sys.executable, "-c", _script % (_BAD, b"", 0)],
              timeout=30, progress_cb=_seen.append, expected_out_s=10.0)
    check("the -progress branch also decodes leniently", True)
except UnicodeDecodeError:
    raise AssertionError("FAIL: the progress branch still decodes strictly")


print("== 2. a replacement upload must not deadlock the project ==")

# A user replaced a 276s video with a 202s one in the same project. Every EDL
# time is a SOURCE time, so the old edit no longer fit — and because every
# write tool validates the WHOLE EDL, the keep fix was blocked by the volume
# span and the volume fix by the keep span. Nothing could ever land again and
# the agent had to say "the edit is stuck and I can't change it from here".
_old = default_edl(276.7)
_old["keep"] = [[220.0, 233.0]]
_old["volume"] = [{"id": "v1", "start": 0.0, "end": 276.0, "gain_db": -60.0}]
validate_edl(_old, 276.7)          # fine against the video it was built on
try:
    validate_edl(_old, 201.97)
    raise AssertionError("FAIL: the stale edit should not validate")
except EDLValidationError:
    check("an edit built on the old source is invalid against the new one",
          True)

# ...which is exactly the signal _finish_setup now resets on. A re-index of the
# SAME file still validates, so real work is never thrown away.
check("the same edit still validates on a re-index of the same file",
      validate_edl(_old, 276.7) is not None)
check("a freshly generated default always validates against its own source",
      validate_edl(default_edl(201.97), 201.97) is not None)

_idx = open(os.path.join(os.path.dirname(__file__), "..",
                         "indexer.py")).read()
check("_finish_setup re-validates the existing EDL against the new duration",
      "validate_edl(_latest[\"json\"], info[\"duration\"])" in _idx)
check("...and tells the user the edit was reset rather than losing it "
      "silently", "edl_was_reset" in _idx)

# The escape hatch: reset_edit writes a generated default, so it cannot be
# blocked by whatever made the saved state unwritable.
_tools = open(os.path.join(os.path.dirname(__file__), "..",
                           "agent_tools.py")).read()
check("reset_edit exists and is registered as a write tool",
      "def reset_edit(ctx)" in _tools and '"reset_edit": (reset_edit' in _tools)
check("a rejected write that finds the SAVED state invalid names the escape "
      "hatch instead of looping", "call reset_edit to start from the full "
      "video" in _tools)


print("== 3. an erase must not be able to kill the worker ==")

# Two agent turns died outright on 2026-07-25 (a 1282x1596 erase and a
# 1440x2560 one). The job did not fail — the WORKER did, taking every other
# user's in-flight turn with it. clean_video ran two live x264 encoders on the
# smallest box in the fleet, at full source resolution, inside the agent turn.
_inp = open(os.path.join(os.path.dirname(__file__), "..",
                         "inpaint.py")).read()
check("the proxy is a SECOND pass, so two encoders are never alive at once",
      "if out_proxy:" in _inp.split("clean encode failed")[1])
check("the full-res encoder's lookahead and thread count are bounded",
      "CLEAN_X264_LOOKAHEAD" in _inp and "CLEAN_X264_THREADS" in _inp)
check("the raw-frame pipe buffer is capped, not proportional to frame size",
      "min(frame_bytes * 4," in _inp)
check("oversized work is refused honestly rather than attempted",
      "CLEAN_MAX_MPX_SECONDS" in _tools)

import config                                                # noqa: E402
check("the pixel budget is finite and the encoder bounds are small",
      config.CLEAN_MAX_MPX_SECONDS > 0
      and config.CLEAN_X264_LOOKAHEAD <= 20
      and config.CLEAN_X264_THREADS <= 4)

# The rectangle the detector cannot see. find_burned_text votes on horizontal
# LINE structure, so a soft centred wordmark ("Dream Life", upper third) scored
# nothing twice while the vision model read it off the frames instantly.
check("find_burned_text falls back to looking at the frames",
      "_vision_seeded_regions" in _tools)
check("...and the fallback still MEASURES the ink rather than trusting the "
      "model's rectangle", "snap_box_to_ink" in _inp and
      "snap_box_to_ink" in _tools)


print("== 4. video generation must not submit a call that cannot succeed ==")

# The configured model is `.../image-to-video`. fal accepts the submit without
# an image_url and only fails minutes later, on the worker, as a finished job
# with no video in it — so two real users got "the provider finished but
# returned no video url" and the agent concluded the feature was broken.
check("an image-to-video model id is recognised as needing a first frame",
      videogen.needs_image("fal-ai/kling-video/v2.5-turbo/pro/image-to-video"))
check("an i2v suffix counts too", videogen.needs_image("vendor/model/i2v"))
check("a text-to-video model does not",
      not videogen.needs_image("fal-ai/kling-video/v2.5-turbo/pro/"
                               "text-to-video"))

import config                                                # noqa: E402,F811

# Behavioural, not textual: with a key present and an i2v model configured,
# the call must be refused locally. If it ever reaches the network again this
# test hangs the provider's poll loop instead of passing.
_key, _model = config.FAL_KEY, config.VIDEO_GEN_MODEL
try:
    config.FAL_KEY = "test-key"
    config.VIDEO_GEN_MODEL = "fal-ai/kling-video/v2.5-turbo/pro/image-to-video"
    ok, err, secs = videogen.generate_video("a cat walking", "/dev/null")
    check("an image-to-video model with no image is refused BEFORE any "
          "network call", ok is False and secs == 0.0
          and "still image" in (err or "") and "given none" in (err or ""))
finally:
    config.FAL_KEY, config.VIDEO_GEN_MODEL = _key, _model

_vg = open(os.path.join(os.path.dirname(__file__), "..",
                        "videogen.py")).read()
check("an unrecognised provider payload reports what actually came back",
      "response keys:" in _vg)
check("the tool generates the first frame instead of dead-ending",
      "videogen.needs_image()" in _tools)
check("...and the still it pays for is quoted in the pre-flight budget",
      "projected += config.IMAGE_PRICE_USD" in _tools)


print("== 5. look_at must never go blind without saying why ==")

# 20+ consecutive look_at calls returned a bare "Could not extract frames for
# that range" on a project whose render_preview worked perfectly. The agent had
# no idea why, told the user their file could not be read, and a paid turn was
# spent on nothing.
_look = _tools.split("def look_at(")[1].split("def _asset_local_path")[0]
check("look_at falls back to the ORIGINAL when the proxy yields no frames",
      "_original_local(ctx)" in _look)
check("the real ffmpeg error is reported, not swallowed",
      "last_err" in _look and "proxy or the original" in _look)
check("look_at_asset reports its failure reason too",
      "unknown error" in _tools.split("def look_at_asset(")[1][:4000])
check("a storage key that IS the main video says so, instead of 'nothing of "
      "that type is uploaded'", "it IS the main video" in _tools)

print(f"\n{PASS} checks passed")


def test_round43_checks_ran():
    """pytest entry point.

    The checks above execute at import, so a regression already fails the run
    as a collection error. This gives pytest a collected item too, so the file
    can never quietly stop contributing coverage.
    """
    assert PASS >= 28
