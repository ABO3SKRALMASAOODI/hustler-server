"""Classification of fetched media. Needs ffmpeg/ffprobe on PATH.

Run from the worker/ directory:  python tests/test_media_classify.py

Kept out of test_units.py because that suite promises no ffmpeg. The cases
here cannot be faked with fixtures: what a fetched file IS gets decided by
what a decoder sees, so the test has to hand real containers to real ffprobe.

Two of these are the whole reason the module has a classifier rather than an
extension lookup, and both were wrong at some point during development:

* An MP3 WITH EMBEDDED COVER ART reports a video stream. Naively "has a video
  stream => it's a video" files every such track as a silent one-frame clip,
  so the user's song lands on the timeline as footage.
* A STILL GIF and an ANIMATED GIF share a codec AND a container. Only the
  frame count separates them — and ffprobe names the container "gif", not
  "gif_pipe", which is what the first version got wrong.
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import url_media                                             # noqa: E402

if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
    print("SKIPPED — ffmpeg/ffprobe not on PATH")
    raise SystemExit(0)

PASS = 0


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


V = "testsrc=size=64x64:rate=10:duration=2"
S = "testsrc=size=64x64:rate=1:duration=1"
A = "sine=frequency=440:duration=2"

d = tempfile.mkdtemp(prefix="valmera-classify-")
try:
    ff("-f", "lavfi", "-i", V, f"{d}/anim.gif")
    ff("-f", "lavfi", "-i", S, "-frames:v", "1", f"{d}/still.gif")
    ff("-f", "lavfi", "-i", S, "-frames:v", "1", f"{d}/still.png")
    ff("-f", "lavfi", "-i", S, "-frames:v", "1", f"{d}/still.jpg")
    ff("-f", "lavfi", "-i", A, f"{d}/tone.mp3")
    ff("-f", "lavfi", "-i", A, f"{d}/tone.wav")
    ff("-f", "lavfi", "-i", V, f"{d}/silent.mp4")
    ff("-f", "lavfi", "-i", V, "-f", "lavfi", "-i", A, "-shortest",
       f"{d}/av.mp4")
    # The awkward one: audio with an attached picture.
    ff("-f", "lavfi", "-i", A, "-f", "lavfi", "-i", S,
       "-map", "0:a", "-map", "1:v", "-frames:v", "1", "-c:v", "mjpeg",
       "-disposition:v", "attached_pic", f"{d}/cover.mp3")

    EXPECTED = {
        "anim.gif": url_media.KIND_VIDEO,     # animated => footage
        "still.gif": url_media.KIND_IMAGE,    # same codec+container, 1 frame
        "still.png": url_media.KIND_IMAGE,
        "still.jpg": url_media.KIND_IMAGE,
        "tone.mp3": url_media.KIND_AUDIO,
        "tone.wav": url_media.KIND_AUDIO,
        "cover.mp3": url_media.KIND_AUDIO,    # cover art is not footage
        "silent.mp4": url_media.KIND_VIDEO,   # no audio is still a video
        "av.mp4": url_media.KIND_VIDEO,
    }
    for name, expected in EXPECTED.items():
        kind, info = url_media.classify(os.path.join(d, name))
        check(f"{name} is classified as {expected}", kind == expected)
        if expected == url_media.KIND_VIDEO:
            check(f"{name} reports a usable duration",
                  info.get("duration_s", 0) > 0)
        if expected == url_media.KIND_IMAGE:
            check(f"{name} reports its dimensions",
                  info.get("width") and info.get("height"))

    # A file that is not media at all must be refused, not filed as something.
    with open(os.path.join(d, "page.html"), "w") as f:
        f.write("<html><body>Sign in to continue</body></html>")
    try:
        url_media.classify(os.path.join(d, "page.html"))
        raise AssertionError("FAIL: an HTML page was accepted as media")
    except url_media.FetchMediaError:
        check("an HTML error page is refused, not filed as media", True)
finally:
    shutil.rmtree(d, ignore_errors=True)

print(f"\nALL {PASS} CHECKS PASSED")
