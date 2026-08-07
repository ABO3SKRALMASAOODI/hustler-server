"""Round 95 — stills are senses, not inventory lines (project 380, 2026-08-06).

A user uploaded 8 photos and 4 clips and asked in one message for a video
built from them. The clips rode into every LLM call as filmstrips; the
photos entered as TEXT inventory lines only, so the agent spent 8
look_at_asset calls just to learn what they showed — and an agent that
skips that step places photos blind. A CANVAS program built purely from
stills started with no eyes at all. Now every project image attaches to the
senses block exactly like the video strips.

Pins:
  * _image_attach_local: EXIF orientation is applied (phone portraits
    otherwise attach sideways), alpha is flattened DARK (a white-text card
    stays readable), output fits IMAGE_ATTACH_MAX_PX, and the copy is
    built once — the second call never re-downloads.
  * filmstrip_parts attaches every project image as a labeled part:
    uploads say UPLOADED IMAGE, generated/ keys say GENERATED IMAGE, and
    the label carries the storage_key (the handle every media tool takes).
    An undecodable image is skipped without dropping the rest, and
    IMAGES_TURN_MAX caps the attach.
  * A project with stills and no videos still gets a senses block (the
    canvas case).
  * _attachment_context with direct sight points at the senses block —
    never a vision round trip, never a false "you cannot see it".
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image                                           # noqa: E402

import agent_loop                                               # noqa: E402
import config                                                   # noqa: E402
import db as dbx                                                # noqa: E402
import llm                                                      # noqa: E402
import storage                                                  # noqa: E402

PASS = 0


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


TMP = tempfile.mkdtemp(prefix="r95_")
config.TMP_DIR = TMP
FIXDIR = os.path.join(TMP, "fixtures")
os.makedirs(FIXDIR)

# A "phone photo": RGBA 1600x800, transparent except a white block — after
# the flatten the background must be dark and the block still white.
rgba_path = os.path.join(FIXDIR, "photo.png")
rgba = Image.new("RGBA", (1600, 800), (0, 0, 0, 0))
rgba.paste((255, 255, 255, 255), (600, 300, 1000, 500))
rgba.save(rgba_path)

# A landscape JPEG stamped orientation 6 (rotate 90): exif_transpose must
# deliver it portrait.
exif_path = os.path.join(FIXDIR, "rotated.jpg")
ex = Image.new("RGB", (400, 200), (200, 30, 30))
tag = Image.Exif()
tag[274] = 6
ex.save(exif_path, "JPEG", exif=tag)

# Not an image at all.
corrupt_path = os.path.join(FIXDIR, "corrupt.jpg")
with open(corrupt_path, "wb") as f:
    f.write(b"this is not a jpeg")

# A generated card.
card_path = os.path.join(FIXDIR, "card.png")
Image.new("RGB", (1080, 1920), (23, 50, 77)).save(card_path)

FIX = {
    "images/7/photo.png": rgba_path,
    "images/7/rotated.jpg": exif_path,
    "images/7/corrupt.jpg": corrupt_path,
    "generated/7/card.png": card_path,
}
downloads = {"n": 0}


def _fake_download(key, dest):
    downloads["n"] += 1
    shutil.copyfile(FIX[key], dest)


storage.download_to = _fake_download


def _asset(aid, key, name):
    return {"id": aid, "kind": "image_ref", "storage_key": key,
            "bytes": os.path.getsize(FIX[key]), "meta": {"filename": name},
            "project_id": 7}


A_PHOTO = _asset(1, "images/7/photo.png", "photo.png")
A_ROT = _asset(2, "images/7/rotated.jpg", "rotated.jpg")
A_BAD = _asset(3, "images/7/corrupt.jpg", "corrupt.jpg")
A_CARD = _asset(4, "generated/7/card.png", "card.png")


# ── _image_attach_local ──────────────────────────────────────────────

local = agent_loop._image_attach_local(A_PHOTO)
img = Image.open(local)
check("downscaled to the cap",
      max(img.size) == config.IMAGE_ATTACH_MAX_PX and img.size[0] > img.size[1])
px_corner = img.getpixel((5, 5))
px_center = img.getpixel((img.size[0] // 2, img.size[1] // 2))
check("alpha flattened dark, content still bright",
      max(px_corner) < 48 and min(px_center) > 200)

n_before = downloads["n"]
again = agent_loop._image_attach_local(A_PHOTO)
check("second call is served from cache",
      again == local and downloads["n"] == n_before)

rot = Image.open(agent_loop._image_attach_local(A_ROT))
check("EXIF orientation applied (landscape file attaches portrait)",
      rot.size == (200, 400))


# ── filmstrip_parts: stills attach like strips ───────────────────────

class _Ctx:
    has_main_video = False
    project_id = 7


class _WDB:
    def run(self, fn, *a, **k):
        return fn(None, *a, **k)


_real_images = agent_loop._image_assets
_real_clips = dbx.indexed_clips
dbx.indexed_clips = lambda conn, pid, *a: []
agent_loop._image_assets = \
    lambda conn, pid: [A_PHOTO, A_BAD, A_ROT, A_CARD]
try:
    parts = agent_loop.filmstrip_parts(_Ctx(), _WDB())
    check("a canvas project (stills, no videos) still gets senses",
          parts is not None)
    labels = [p["text"] for p in parts if p.get("type") == "text"]
    images = [p for p in parts if p.get("type") == "image_url"]
    check("intro names the block",
          "FILMSTRIPS & STILLS" in labels[0])
    check("the corrupt image is skipped, the rest attach",
          len(images) == 3)
    joined = "\n".join(labels)
    check("uploads and generated cards are told apart",
          'UPLOADED IMAGE "photo.png"' in joined
          and 'GENERATED IMAGE "card.png"' in joined
          and "corrupt.jpg" not in joined)
    check("labels carry the storage_key handle",
          "storage_key images/7/photo.png" in joined
          and "storage_key generated/7/card.png" in joined)

    old_cap = config.IMAGES_TURN_MAX
    config.IMAGES_TURN_MAX = 1
    try:
        parts = agent_loop.filmstrip_parts(_Ctx(), _WDB())
        images = [p for p in parts if p.get("type") == "image_url"]
        check("IMAGES_TURN_MAX caps the attach", len(images) == 1)
    finally:
        config.IMAGES_TURN_MAX = old_cap
finally:
    agent_loop._image_assets = _real_images
    dbx.indexed_clips = _real_clips


# ── _attachment_context: direct sight never claims blindness ─────────

class _Ctx2:
    project_id = 7
    direct_sight = True
    agent_model = "test-model"
    workdir = TMP


_real_get_asset = dbx.get_asset
_real_sees = llm.agent_sees
_real_vision = llm.ask_vision
dbx.get_asset = lambda conn, aid: dict(A_PHOTO)
llm.agent_sees = lambda model: True


def _no_vision(*a, **k):
    raise AssertionError("direct sight must not burn a vision call")


llm.ask_vision = _no_vision
try:
    note = agent_loop._attachment_context(
        _WDB(), _Ctx2(), {"meta": {"attachments": [1]}})
    check("attached image points at the senses block",
          "FILMSTRIPS & STILLS" in note
          and "storage_key images/7/photo.png" in note)
    check("no false blindness claim", "CANNOT" not in note)
finally:
    dbx.get_asset = _real_get_asset
    llm.agent_sees = _real_sees
    llm.ask_vision = _real_vision

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nALL {PASS} CHECKS PASSED")
