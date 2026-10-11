"""Honest render reports and review tools (Oct 10 podcast run, 0/9 shipped).

Each block pins one defect the run's editors and reviewers hit:
  * watch_video's filmstrip rode out on the caller's NEXT call;
  * the render's MID-WORD AUDIT flagged edges the audio-safe keep tools left
    in quiet sound on purpose;
  * HOOK OPENS MID-SOUND fired on a first word starting on the cut
    (review_audio heard 'I have', 'What is an economy' clean);
  * PICTURE CHECK called faces "cut 16-30% past the card edge" from Haar
    boxes on the card's enlarged pixels and its blurred backdrop — card
    faces are now read on the source and placed through card_geom;
  * reviewers estimated picture area, cap heights, the payoff's size and
    caption collisions by eye — the render now measures them;
  * look_at's assembled view ignored picture cards and erase patches,
    native mode took one time per call, and a render's report could only
    be read by rendering again.
"""

import math
import os
import shutil
import subprocess
import sys
import time
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools  # noqa: E402
import audit  # noqa: E402
import card_geom  # noqa: E402
import config  # noqa: E402
import cut_audio  # noqa: E402
import db as dbx  # noqa: E402
import mcp_exec  # noqa: E402
import render_qc  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


class _FnDb:
    def __init__(self, handlers):
        self.handlers = handlers
        self.calls = []

    def run(self, fn, *args, **kwargs):
        self.calls.append(fn)
        if fn in self.handlers:
            h = self.handlers[fn]
            return h(*args, **kwargs) if callable(h) else h
        raise AssertionError(f"unexpected DB call: {getattr(fn, '__name__', fn)}")


# ── watch_video: the filmstrip belongs to this reply ───────────────────

def _media_session(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(mcp_exec.llm, "agent_client_for",
                        lambda *_: (object(), "outside-model"))
    boot = _FnDb({dbx.latest_creative_blueprint: None,
                  dbx.user_billing: (True, "ai", False)})
    session = mcp_exec._new_context(
        boot, {"id": 1, "user_id": 4}, {"id": 7, "chat_session_id": 13},
        {"video": {"duration": 10.0}}, "sha-a")
    monkeypatch.setattr(mcp_exec, "_sessions", {7: session})
    uploads = []
    monkeypatch.setattr(mcp_exec.storage, "upload_file",
                        lambda path, key, mime: uploads.append(key))
    db = _FnDb({
        mcp_exec._call_snapshot: {
            "project": {"id": 7, "chat_session_id": 13},
            "original": {"id": 1, "sha256": "sha-a"},
            "billing": (True, "ai", False)},
        dbx.latest_edl: {"version": 4, "json": default_edl(10.0)},
    })
    return session, db, uploads


def test_watch_video_delivers_its_filmstrip_in_the_same_reply(
        monkeypatch, tmp_path):
    session, db, uploads = _media_session(monkeypatch, tmp_path)
    sheet = tmp_path / "sheet.jpg"
    sheet.write_bytes(b"\xff\xd8jpeg")

    def prepare(ctx, args, _inline):
        ctx.pending_images.append(("The program, 12 moments", str(sheet)))
        return {"text": "Here is the program.",
                "video": {"storage_key": "media/7/p.mp4"}}
    monkeypatch.setattr(mcp_exec.mcp_media, "prepare", prepare)
    out = mcp_exec.run_mcp_job(db, {
        "id": 5, "project_id": 7, "user_id": 4,
        "payload": {"tool": mcp_exec.MEDIA_TOOL, "args": {}}})
    assert [img["label"] for img in out["images"]] == ["The program, 12 moments"]
    assert out["images"][0]["storage_key"] == uploads[0]
    assert session.ctx.pending_images == []     # nothing left for the next call


def test_a_refused_watch_keeps_queued_pictures_for_their_own_call(
        monkeypatch, tmp_path):
    session, db, uploads = _media_session(monkeypatch, tmp_path)
    sheet = tmp_path / "sheet.jpg"
    sheet.write_bytes(b"\xff\xd8jpeg")
    session.ctx.pending_images.append(("look_at page 2", str(sheet)))
    monkeypatch.setattr(mcp_exec.mcp_media, "prepare",
                        lambda ctx, args, _i: {"text": "No preview.",
                                               "is_error": True})
    out = mcp_exec.run_mcp_job(db, {
        "id": 5, "project_id": 7, "user_id": 4,
        "payload": {"tool": mcp_exec.MEDIA_TOOL, "args": {}}})
    assert "images" not in out and uploads == []
    assert len(session.ctx.pending_images) == 1


# ── MID-WORD AUDIT: judged on the sound, like the keep tools ───────────

@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
def test_midword_audit_drops_edges_the_audio_safe_tools_left_in_quiet(
        tmp_path):
    # 0-1.5 s a tone (speech), 1.5-2.5 s room tone, 2.5-4 s a tone
    wav = str(tmp_path / "s.wav")
    subprocess.run([
        FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
        "aevalsrc='if(between(t,1.5,2.5),0.0005*sin(2*PI*90*t),"
        "0.4*sin(2*PI*220*t))':s=16000:d=4", wav], check=True)
    words = [{"w": "loud", "t0": 0.8, "t1": 1.4},
             {"w": "late", "t0": 1.7, "t1": 2.8}]
    # 1.1 sits inside 'loud' in tone; 2.0 inside 'late' per the transcript
    # but in room tone (the transcript's timing is off: a quiet edge)
    keep = [[0.0, 1.1], [2.0, 4.0]]
    plain = audit.midword_audit(keep, words, 4.0)
    assert len(plain) == 2
    heard = audit.midword_audit(keep, words, 4.0, source=wav)
    assert len(heard) == 1 and "inside 'loud'" in heard[0]
    assert "dB" in heard[0]
    # unreadable sound keeps the transcript's verdict
    assert len(audit.midword_audit(keep, words, 4.0,
                                   source=str(tmp_path / "none.wav"))) == 2


# ── the hook's first sound ──────────────────────────────────────────────

def test_a_first_word_starting_on_the_cut_is_no_mid_sound_opening():
    # s06: 'mean,' ends 351.135, 'I' starts there; the keep starts 351.12
    hook = {"src0": 351.12, "words": [
        {"w": "mean,", "t0": 350.975, "t1": 351.135},
        {"w": "I", "t0": 351.135, "t1": 351.295}]}
    assert render_qc.hook_cut(hook)[0] == "onset"
    # s08: the keep starts exactly on the second 'what'
    hook = {"src0": 535.51, "words": [
        {"w": "what", "t0": 535.35, "t1": 535.51},
        {"w": "what", "t0": 535.51, "t1": 535.67}]}
    assert render_qc.hook_cut(hook)[0] == "onset"
    # a start inside a word's body is a mid-word opening
    kind, w = render_qc.hook_cut({"src0": 351.05, "words": [
        {"w": "mean,", "t0": 350.975, "t1": 351.135}]})
    assert kind == "word" and w["w"] == "mean,"
    # no word near: a breath or a laugh
    assert render_qc.hook_cut({"src0": 10.0, "words": [
        {"w": "far", "t0": 11.0, "t1": 11.3}]}) == (None, None)
    lines = render_qc.findings(
        {"hook_sound": [-9.0, -45.0, "mean,", 350.98, 351.14]}, {"fps": 30.0})
    assert lines[0].startswith("HOOK OPENS MID-WORD")
    assert "inside 'mean,'" in lines[0]


# ── card geometry ───────────────────────────────────────────────────────

def _card_edl(**card):
    edl = default_edl(100.0)
    edl["keep"] = [[10.0, 40.0]]
    edl["frame"] = {"ratio": "9:16", "mode": "crop"}
    base = {"id": "c", "start": 0.0, "end": 30.0, "box": [.14, .235, .86, .794]}
    base.update(card)
    edl["effects"] = {"picture_cards": [base]}
    return edl


def test_a_source_card_maps_its_rect_onto_its_box():
    edl = _card_edl(source=[.385, .215, .615, .779])
    wins = card_geom.windows(edl, 15.0, 1080, 1920, 1920, 1080, None, 5.0)
    assert len(wins) == 1
    box, src = wins[0]
    assert box == [.14, .235, .86, .794]
    # match_rect trims the rect to the box's pixel aspect
    bw, bh = .72 * 1080, .559 * 1920
    assert abs((src[2] - src[0]) * 1920 / ((src[3] - src[1]) * 1080) - bw / bh) < .01
    # a face box in the source lands where the card shows it
    face = [.44, .36, .60, .63]
    out = card_geom.to_output(face, wins[0])
    assert box[0] < out[0] < out[2] < box[2]
    # a 2x zoom on the composed canvas: the box shows half the rect
    zoomed = card_geom.windows(edl, 15.0, 1080, 1920, 1920, 1080,
                               (2.0, .5, .5), 5.0)[0][1]
    assert abs((zoomed[2] - zoomed[0]) - (src[2] - src[0]) / 2) < .01


def test_a_program_card_shows_the_programs_crop_cover_fit():
    edl = _card_edl()                           # no source: a program card
    (box, src), = card_geom.windows(edl, 15.0, 1080, 1920, 1920, 1080, None, 5.0)
    assert box == [.14, .235, .86, .794]
    # the 9:16 crop of a 16:9 source is 0.316 wide, centred; the card shows
    # its centre at the box's aspect
    assert .3 < src[0] < src[2] < .7 and abs((src[0] + src[2]) / 2 - .5) < 1e-6
    full, = card_geom.windows(dict(edl, effects={}), 15.0, 1080, 1920,
                              1920, 1080, None, 50.0)
    assert full[0] == [0.0, 0.0, 1.0, 1.0]
    assert abs((full[1][2] - full[1][0]) - (9 / 16) / (16 / 9)) < .01


def test_compose_draws_the_card_from_the_source_frame():
    from PIL import Image
    edl = _card_edl(source=[.25, .0, .75, 1.0], background="#FF0000",
                    radius=0.0)
    raw = Image.new("RGB", (192, 108), (0, 0, 255))
    raw.paste((0, 255, 0), (96, 0, 192, 108))   # right half green
    program = Image.new("RGB", (108, 192), (0, 0, 0))
    img = card_geom.compose(raw, program, edl, 5.0, 15.0, 1920, 1080)
    px = img.load()
    assert px[2, 2] == (255, 0, 0)               # the flat backdrop
    left = px[int(.2 * 108), 96]
    right = px[int(.8 * 108), 96]
    assert left[2] > 200 and right[1] > 200      # the rect's two halves
    assert card_geom.compose(raw, program, dict(edl, effects={}), 5.0,
                             15.0, 1920, 1080) is None


# ── PICTURE CHECK: card faces read on the source ─────────────────────────

@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
def test_card_faces_are_read_on_the_source_and_placed_through_the_card(
        tmp_path, monkeypatch):
    # a source whose right 30% is a black stage (a call window's edge)
    src = str(tmp_path / "src.mp4")
    subprocess.run([
        FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=s=448x252:r=30:d=4", "-vf",
        "drawbox=x=314:y=0:w=134:h=252:color=black:t=fill",
        "-pix_fmt", "yuv420p", src], check=True)
    edl = _card_edl(source=[.40, .10, .70, .90], start=0.0, end=3.0)
    edl["keep"] = [[0.0, 3.0]]
    edl = validate_edl(edl, 4.0).model_dump()
    index = {"video": {"width": 448, "height": 252, "fps": 30.0,
                       "duration": 4.0}}
    plan = render_qc.plan(edl, index, W=270, H=480, fps=30.0)
    assert plan["card_samples"] and plan["card_samples"][0]["wins"]
    face = [.55, .30, .78, .60]                  # past the window's right edge

    def detect(gray, cv2=None, cascades=None, face_px=None, roi=None):
        assert roi is not None and roi[0] < .40 and roi[2] > .70
        return [(face, 0)]
    monkeypatch.setattr("follow.detect", detect)
    monkeypatch.setattr("subject._cascades", lambda cv2: ["frontal"])
    rows = render_qc.source_card_faces(src, plan, time.monotonic() + 60)
    assert rows and len(rows) == len(plan["card_samples"])
    t, dets, dead = rows[0]
    box, look = dets[0]
    win = plan["card_samples"][0]["wins"][0]
    # the detector box narrowed to the face (src_face), then placed
    assert box == [round(v, 4) for v in card_geom.to_output(
        render_qc.src_face(face), win)]
    assert "right" in dead and "left" not in dead
    # the cut is reported as the source's own edge, with its honest fix
    cut = render_qc.cut_faces([(r[0], r[1]) for r in rows], plan,
                              [(0.0, 3.0)])
    assert cut and cut[0][2] == "right"
    res = {"cut": [list(c) + [render_qc._limited(rows, c[0], c[1], c[2])]
                   for c in cut]}
    line = render_qc.findings(res, plan)[0]
    assert line.startswith("FACE CUT BY THE CARD EDGE")
    assert "NO picture past that edge" in line


def test_source_measured_card_faces_take_no_side_margin():
    plan = {"cards": [(0.0, 9.0, [[.1, .1, .9, .9]])]}
    # the detector box ends 0.5% inside the card's right edge: the head
    # margin (6% of the face) would push it past; the source box spans
    # ear to ear already
    near = ([.60, .40, .895, .70], 0)
    samples = [(t, [near]) for t in (1.0, 1.5, 2.0)]
    assert render_qc.cut_faces(samples, plan)
    assert render_qc.cut_faces(samples, plan, [(0.0, 9.0)]) == []


def test_a_face_that_fits_its_card_is_no_cut_on_the_source_box():
    # s05 of the Oct 10 run at 1.75 s: the frontal box on the source runs
    # past both ears (cheeks 11-14% of its width inside it), and judged as
    # the box the face "ran 21% past" both card edges while the render
    # showed it inside, hair touching. Narrowed to the face it fits.
    win = ([0.25, 0.21, 0.75, 0.795], [0.44951, 0.1619, 0.61049, 0.75714])
    haar = [0.4281, 0.3583, 0.6172, 0.6944]
    plan = {"cards": [(0.0, 37.18, [win[0]])]}
    raw = [(t, [(card_geom.to_output(haar, win), 0)]) for t in (1.0, 1.5, 2.0)]
    old = render_qc.cut_faces(raw, plan, [(0.0, 37.18)])
    assert old and old[0][3] > .1                              # the box itself
    face = [(t, [(card_geom.to_output(render_qc.src_face(haar), win), 0)])
            for t in (1.0, 1.5, 2.0)]
    # at the edge, within the detector's own error — never a fifth past it
    assert all(c[3] < .02 for c in render_qc.cut_faces(
        face, plan, [(0.0, 37.18)]))
    # ... and a face that fills its card is AT its tighter edge (s07): only
    # where the faces were read on the source, never on rendered pixels
    clip = render_qc.clipped_faces(face, plan, [(0.0, 37.18)])
    assert clip and clip[0][2] == "left"
    assert render_qc.clipped_faces(face, plan) == []


# ── measures: picture area, type, captions ───────────────────────────────

def test_picture_area_is_measured_against_the_floor():
    from timeline import Timeline
    edl = _card_edl(source=[.385, .215, .615, .779])
    tl = Timeline(edl["keep"], [], None)
    index = {"video": {"width": 1920, "height": 1080}}
    pic = render_qc.picture_measures(edl, index, tl, 720, 1280, 30.0)
    (w,) = pic["windows"]
    assert w["id"] == "c" and abs(w["area"] - .72 * .559) < 1e-3
    assert 1.6 < w["upscale"] < 1.9              # on the 1080x1920 delivery
    line = render_qc.advice({"picture": pic})[0]
    assert line.startswith("PICTURE AREA 0.40 BELOW THE 0.54 FLOOR")
    assert "MEASURES: picture area 0.40 ('c', 1.8x" in \
        render_qc.measures_line({"picture": pic})


def _png(rects, W=200, H=400):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for r in rects:
        d.rectangle(r, fill=(255, 255, 255, 255))
    buf = BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_ink_lines_reads_cap_height_off_the_letters_standing_on_a_line():
    # a mixed-case line on baseline y=100: x-height letters 28 px, tall
    # letters 40 px, one descender and one dot that stand on no baseline
    rects = []
    for i, h in enumerate([28, 40, 28, 28, 40, 28, 40]):
        x = 10 + i * 20
        rects.append([x, 100 - h, x + 12, 99])
    rects.append([160, 80, 172, 112])            # a descender
    rects.append([180, 60, 184, 64])             # a dot
    # a second, smaller line on baseline y=200
    for i in range(5):
        x = 10 + i * 18
        rects.append([x, 200 - 20, x + 10, 199])
    m = render_qc.ink_lines(_png(rects))
    caps = sorted(round(ln["cap"] * 400) for ln in m["lines"])
    assert caps == [20, 40]
    assert round(m["cap"] * 400) == 40
    assert m["box"][1] == pytest.approx(60 / 400)
    assert render_qc.ink_lines(_png([]))["box"] is None


def test_a_headline_is_sized_by_its_claim_not_its_kicker():
    # s06: the kicker ('ELON MUSK · MARCH 2024', all caps) drawn ABOVE the
    # claim at 2.5% of the frame height, the band-squeezed claim at 1.8% —
    # the reviewers' hook size; the largest line alone read 2.4%
    rects = []
    for i in range(8):                            # the kicker: 40 px caps
        x = 10 + i * 22
        rects.append([x, 100 - 40, x + 14, 99])
    for i in range(10):                           # the claim: 28 px caps
        x = 10 + i * 18
        rects.append([x, 160 - 28, x + 12, 159])
    m = render_qc.ink_lines(_png(rects))
    assert round(m["cap"] * 400) == 40
    assert round(render_qc._claim_cap(m, kicker=True) * 400) == 28
    assert render_qc._claim_cap(m) == m["cap"]
    one = render_qc.ink_lines(_png(rects[8:]))   # an empty kicker: one line
    assert render_qc._claim_cap(one, kicker=True) == one["cap"]


def _browser_ok():
    try:
        import motion_engine
        return motion_engine.available()
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _browser_ok(), reason="no headless Chromium")
def test_the_type_pass_measures_a_headline_on_its_composition():
    import motion_templates
    params = motion_templates.check_params("headline", {
        "text": "Jobs called 1983 computer fonts *garbage*",
        "kicker": "Steve Jobs, 1983", "y": .15})
    item = {"id": "headline", "template": "headline", "start": 0.0,
            "end": 6.0, "params": params}
    tplan = {"W": 540, "H": 960, "fps": 30.0, "captions": [], "items": [{
        "id": "headline", "template": "headline", "start": 0.0, "end": 6.0,
        "layer": "above_captions", "tier": None, "purpose": None,
        "persistent": True, "box": None, "item": item}]}
    tres = render_qc.type_pass(tplan, time.monotonic() + 90)
    m = tres["items"]["headline"]
    # the claim, measured past its kicker, on the full-size composition
    assert 0.005 < m["cap"] < 0.1
    assert 0.0 <= m["box"][1] < m["box"][3] < 0.35


def test_type_findings_name_a_small_hook_a_small_payoff_and_colliding_captions():
    tplan = {"hook": "headline", "payoff": "payoff",
             "items": [
                 {"id": "headline", "persistent": True, "layer": "above_captions",
                  "start": 0.0, "end": 30.0, "box": [.06, .13, .94, .23]},
                 {"id": "stat", "persistent": False, "layer": "above_captions",
                  "start": 5.0, "end": 8.0, "box": [.1, .70, .9, .80]},
                 {"id": "payoff", "persistent": False, "layer": "above_captions",
                  "start": 25.0, "end": 30.0, "box": [.1, .3, .9, .5]}]}
    tres = {"items": {"headline": {"cap": .018}, "stat": {"cap": .06},
                      "payoff": {"cap": .04}},
            "cues": [{"t": 1.0, "cap": .026, "box": [.05, .74, .60, .78],
                      "text": "in being actually remarkably"},
                     {"t": 6.0, "cap": .026, "box": [.3, .72, .7, .77],
                      "text": "ten times"},
                     {"t": 12.0, "cap": .026, "box": [.3, .72, .7, .77],
                      "text": "fine"}]}
    cards = [(0.0, 30.0, [[.14, .235, .86, .794]])]
    tq = render_qc.type_findings(tres, tplan, cards)
    assert tq["caps"]["captions"] == .026
    assert tq["caps"]["payoff"] == ("payoff", .04, "stat", .06)
    assert tq["collide"] == [(6.0, "ten times", "stat", 1.0, "under")]
    assert tq["cross"] == [(1.0, "in being actually remarkably", "left")]
    lines = render_qc.advice({"type": tq})
    assert lines[0].startswith("HOOK SMALLER THAN THE CAPTIONS")
    assert "1.8%" in lines[0] and "2.6%" in lines[0]
    assert lines[1].startswith("PAYOFF NOT THE LARGEST LOCKUP: 'payoff'")
    lines = render_qc.measured_findings({"type": tq})
    assert lines[0].startswith("CAPTION UNDER A GRAPHIC at 6.0s")
    assert lines[1].startswith("CAPTION CROSSES THE CARD EDGE at 1.0s")
    assert "payoff 'payoff' 4.0% (largest: 'stat' 6.0%)" in \
        render_qc.measures_line({"type": tq})
    # a hero-word caption look lays its slam across the card by design
    # (s05, Kinetic Poster 'stack'): no crossing; collisions still count
    tq = render_qc.type_findings(tres, dict(tplan, look="stack"), cards)
    assert tq["cross"] == [] and tq["collide"]


def test_heard_but_unshown_words_are_one_blocking_line():
    gap = {"start": 24.22, "end": 24.78, "duration_s": .56, "said": "scenario",
           "cause": "motion graphic 'swap' leaves no caption band clear of it",
           "fix": "move 'swap' off the caption band"}
    line = render_qc.unshown_line({"unshown": [gap]})
    assert line.startswith("WORDS HEARD BUT NEVER SHOWN (blocking")
    assert '"scenario"' in line and "move 'swap'" in line
    assert render_qc.unshown_line({}) == ""


def test_face_findings_group_their_runs_into_one_line_per_edge():
    plan = {"cards": [(0.0, 30.0, [[.1, .1, .9, .9]])], "fps": 30.0}
    res = {"cut": [[1.0, 2.0, "right", .1, "card", 1],
                   [4.0, 5.0, "right", .12, "card", 1],
                   [7.0, 8.0, "left", .05, "card", 0]],
           "clipped": [[1.0, 2.0, "right", 0.0, 1]]}
    lines = render_qc.findings(res, plan)
    assert len(lines) == 2
    assert lines[0].startswith("FACE CUT BY THE CARD EDGE 1.0-2.0s, 4.0-5.0s")
    assert "up to 12%" in lines[0] and "NO picture past" in lines[0]
    assert "re-solve the card's source rect" in lines[1]
    # results stored before the source pass (five fields) still read
    old = {"cut": [[1.0, 2.0, "right", .1, "card"]],
           "clipped": [[9.0, 10.0, "top", .01]]}
    assert len(render_qc.findings(old, plan)) == 2


# ── look_at: native detail, several times per call ──────────────────────

def test_native_looks_come_back_one_full_detail_image_per_time(
        tmp_path, monkeypatch):
    from PIL import Image
    frames = []
    for i in range(3):
        p = tmp_path / f"f{i}.jpg"
        Image.new("RGB", (64, 36), (i * 60, 0, 0)).save(p)
        frames.append(str(p))
    ctx = SimpleNamespace(workdir=str(tmp_path), pending_images=[],
                          sight_out=True, direct_sight=False, job={"id": 3})
    out = agent_tools._deliver_frames(ctx, frames, ["@1.00s", "@2.00s", "@3.00s"],
                                      "", "Frames", separate=True)
    assert len(ctx.pending_images) == 3
    assert [lb for lb, _p in ctx.pending_images] == [
        "Frames — @1.00s", "Frames — @2.00s", "Frames — @3.00s"]
    assert "3 full-detail images, one per time" in out
    ctx.pending_images = []
    agent_tools._deliver_frames(ctx, frames, ["a", "b", "c"], "", "Frames")
    assert len(ctx.pending_images) == 1          # the contact sheet


def test_native_looks_never_queue_pictures_for_the_next_mcp_call(
        tmp_path, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(agent_tools.config, "MCP_IMAGE_PAGE_SIZE", 4)
    asset = {"id": 9, "kind": "render", "width": 1080, "meta": {}}
    monkeypatch.setattr(agent_tools, "_resolve_media_asset",
                        lambda ctx, key, kinds: (asset, None))
    monkeypatch.setattr(agent_tools, "_asset_media_duration",
                        lambda ctx, a: 30.0)
    decoded = []

    def frames(ctx, a, times, **kw):
        decoded.extend(times)
        out = []
        for i, t in enumerate(times):
            p = tmp_path / f"n{i}.jpg"
            Image.new("RGB", (64, 36)).save(p)
            out.append((i, str(p)))
        return out, None
    monkeypatch.setattr(agent_tools, "_asset_frames", frames)
    ctx = SimpleNamespace(workdir=str(tmp_path), pending_images=[],
                          sight_out=True, direct_sight=False, job={"id": 3})
    out = agent_tools.look_at_asset(ctx, "renders/7/p.mp4",
                                    times=[1, 2, 3, 4, 5, 6],
                                    native_resolution=True)
    # this reply carries one transport page; nothing is left queued
    assert decoded == [1.0, 2.0, 3.0, 4.0] and len(ctx.pending_images) == 4
    assert "NOT captured — 5.00s, 6.00s" in out


def test_an_erased_window_is_read_from_its_patch_clip(tmp_path, monkeypatch):
    got = []
    monkeypatch.setattr(agent_tools.storage, "download_to",
                        lambda key, local: open(local, "wb").write(b"x"))
    monkeypatch.setattr(agent_tools.media, "frame_at",
                        lambda src, t, dst, **k: got.append((src, round(t, 2))))
    ctx = SimpleNamespace(workdir=str(tmp_path))
    edl = {"patches": [{"id": "pa1", "asset_key": "patches/7/a.mp4",
                        "src_start": 350.25, "src_end": 358.85,
                        "regions": []}]}
    note = agent_tools._patched_frame(ctx, edl, 355.0, str(tmp_path / "o.jpg"))
    assert note == " [erase patch pa1 applied]"
    assert got == [(str(tmp_path / "patch_a.mp4"), 4.75)]
    assert agent_tools._patched_frame(ctx, edl, 360.0, "x") == ""


# ── render_preview(report=true): read-only ───────────────────────────────

def test_render_report_reads_the_stored_report_without_rendering():
    pq = {"version": 4, "findings": [
              "CAPTION CROSSES THE CARD EDGE at 1.0s: 'x' runs across ..."],
          "advice": ["HOOK SMALLER THAN THE CAPTIONS: 'headline' ..."],
          "picture": {"windows": [{"id": "c", "t0": 0.0, "t1": 30.0,
                                   "area": .40, "upscale": 1.8}],
                      "floor": .54, "area": .40},
          "unshown": [{"start": 1.0, "end": 1.4, "said": "scenario",
                       "cause": "c", "fix": "f", "duration_s": .4}]}
    asset = {"id": 24838, "duration_s": 30.7,
             "meta": {"edl_version": 46, "quality": "approval",
                      "render_job_id": 63422}}
    record = {"status": "repair_required", "record": {
        "findings": [{"finding_id": "vf_1", "severity": "error",
                      "code": "review_finding", "message": "picture QC: x"}],
        "justifications": []}}
    db = _FnDb({dbx.find_render_asset: asset,
                dbx.get_job: {"result": {"picture_qc": pq,
                                         "midword_audit": ["boundary 1.00 inside 'x'"],
                                         "audio_qc": {}}},
                dbx.get_verification_record: record})
    ctx = SimpleNamespace(db=db, project_id=3447,
                          latest_edl=lambda: {"version": 46, "json": {}})
    out = agent_tools.render_preview(ctx, report=True)
    assert out.startswith("RENDER REPORT (read-only")
    assert "asset 24838, approval, 30.7s" in out
    assert "PICTURE CHECK (measured on the rendered frames): CAPTION CROSSES" in out
    assert "PICTURE MEASURES (advisory — the Look's rules): HOOK SMALLER" in out
    assert "MEASURES: picture area 0.40" in out
    assert "CAPTION CHECK: WORDS HEARD BUT NEVER SHOWN" in out
    assert "MID-WORD AUDIT: boundary 1.00" in out
    assert "VERIFICATION RECORD v46: repair_required — open: vf_1" in out
    assert dbx.get_or_enqueue_preview_job not in db.calls
    # a stale report says so; no preview at all is a prerequisite
    stale = _FnDb({dbx.find_render_asset: None, dbx.latest_render: asset,
                   dbx.get_job: None, dbx.get_verification_record: None})
    ctx.db = stale
    ctx.latest_edl = lambda: {"version": 50, "json": {}}
    assert "STALE: the edit is now at v50" in agent_tools.render_preview(
        ctx, report=True)
    ctx.db = _FnDb({dbx.find_render_asset: None, dbx.latest_render: None})
    assert agent_tools.render_preview(ctx, report=True).startswith(
        "PREREQUISITE")


def test_look_at_composes_the_card_onto_the_assembled_tile(tmp_path):
    from PIL import Image
    raw = tmp_path / "raw.jpg"
    fitted = tmp_path / "fit.jpg"
    Image.new("RGB", (192, 108), (0, 200, 0)).save(raw)
    Image.new("RGB", (108, 192), (0, 0, 0)).save(fitted)
    ctx = SimpleNamespace(workdir=str(tmp_path))
    edl = _card_edl(source=[.3, .1, .7, .9], background="#FFFFFF")
    path, sfx = agent_tools._card_view(
        ctx, 0, str(raw), str(fitted), "", edl, 5.0, 15.0, [], 30.0,
        {"width": 1920, "height": 1080})
    assert sfx == " [picture card 'c']" and path != str(fitted)
    img = Image.open(path).convert("RGB")
    assert img.getpixel((2, 2))[0] > 240                    # the backdrop
    assert img.getpixel((54, 96))[1] > 150                  # the footage
    # no card at that second: the tile is untouched
    assert agent_tools._card_view(
        ctx, 0, str(raw), str(fitted), "", edl, 50.0, 55.0, [], 60.0,
        {"width": 1920, "height": 1080}) == (str(fitted), "")
