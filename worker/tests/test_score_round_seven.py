"""First films toward the judge, round seven (week-1112 lane K, 14 Nov 2026).

What these hold: a talking take that would hold one framing past two seconds is cut inside itself where a phrase
starts, and no cut goes back to the framing just left; a stretch with room for one step takes it once (wordless) or
closes in and drifts back out (under a voice); a Bin picture the Score placed nowhere is cut in on the first word it
was found for, and every line that names a shot of that beat follows it; a designed picture of type is shown whole;
the Director's own sounds sit in the one level hierarchy; a film of some pictures carries small ticks; the film's
sounds and its arranged bed are one mix; footage that was cut once is on the shelf for the next film, and the Bin
takes it. Fixtures are synthetic Scores, index stubs and a storage stand-in. No customer media is here."""
import json
import os
import threading

import numpy as np
import pytest
from PIL import Image

import score.compiler as S
from score import fill, move, pace, sound
from tests.test_score_compiler import BIN, wordless_index
from tests.test_score_taste import beats_over, comp, film as film_text, selfie, voice


def shots(c):
    return sorted((i for i in c.l1["items"] if i["k"] == "shot"), key=lambda i: i["f"][0])


def rows(s):
    return s["crop"] if isinstance(s["crop"][0], list) else [s["crop"]]


def width(r):
    return r[2] - r[0]


# ------------------------------------------------------------------ the take under a voice

def test_a_talking_take_is_cut_inside_itself_where_a_phrase_starts():
    idx = selfie(20.0)
    c = comp(film_text(beats_over(idx, 4, lambda k, a, z: {"pic": "main"})), idx, voice(20.0))
    take = [s for s in shots(c) if s.get("pic") == "main"]
    cuts = [s for s in take if s.get("punch")]
    assert len(cuts) >= 5 and all(s.get("talk") for s in cuts)                 # 20 s of one take: a framing every 2 s at most
    assert c.meter["pace"]["take_punches"] == len(cuts) and c.meter["move"].get("talk_cuts") == len(cuts)
    words = {int(f) for f in (int(round(w["t0"] * 30)) for w in idx["words"])}
    spans, start = [], take[0]["f"][0]
    for s in cuts:                                                              # never two cuts closer than a framing can be read
        spans.append(s["f"][0] - start)
        start = s["f"][0]
    assert min(spans) >= pace.TALK_MIN_F - 1 and max(spans) <= pace.TALK_MAX_F + pace.TALK_MIN_F
    assert words                                                                # the index has word times to cut on
    for s in take:                                                              # every framing holds the head whole
        for r in rows(s):
            assert r[0] <= 0.36 + 1e-6 and r[2] >= 0.64 - 1e-6 and r[1] <= 0.2 + 1e-6 and r[3] >= 0.4 - 1e-6


def test_no_cut_inside_a_talking_take_goes_back_to_the_framing_just_left():
    idx = selfie(20.0)
    c = comp(film_text(beats_over(idx, 4, lambda k, a, z: {"pic": "main"})), idx, voice(20.0))
    take = [s for s in shots(c) if s.get("pic") == "main"]
    starts = [width(rows(take[0])[0])] + [width(rows(s)[0]) for s in take if s.get("punch")]
    steps = [(width(rows(a)[-1]), width(rows(b)[0])) for a, b in zip(take, take[1:]) if b.get("punch")]
    assert all(abs(x / y - 1) >= 0.05 for x, y in steps)                        # each cut is a step the eye reads
    turned = all(abs(starts[i + 1] / starts[i - 1] - 1) > 0.03 for i in range(1, len(starts) - 1))
    closing = all(x / y > 1.04 for x, y in steps)
    assert turned or closing                                                    # wide, closer, closest, wide; or every cut closes in and the frame drifts back


def test_a_talking_stretch_with_room_for_one_step_closes_in_and_drifts_back_out(monkeypatch):
    monkeypatch.setattr(move, "TALK_MAX", 1.15)                                 # one step of room: two framings
    idx = selfie(20.0)
    c = comp(film_text(beats_over(idx, 4, lambda k, a, z: {"pic": "main"})), idx, voice(20.0))
    take = [s for s in shots(c) if s.get("pic") == "main"]
    cuts = [(a, b) for a, b in zip(take, take[1:]) if b.get("punch")]
    assert len(cuts) >= 4
    for a, b in cuts:
        assert width(rows(a)[-1]) / width(rows(b)[0]) > 1.05                    # every cut closes on the speaker
    drift = [s for s in take if s.get("punch") and len(rows(s)) > 4]
    assert drift and all(width(rows(s)[-1]) > width(rows(s)[0]) for s in drift)  # and the frame eases wide again while they talk


def test_a_wordless_stretch_with_room_for_one_step_takes_it_once(monkeypatch):
    monkeypatch.setattr(move, "PUNCH_MAX", 1.2)                                 # p5582's jar: two framings
    idx = wordless_index(8.0)
    c = comp(film_text([{"k": "b", "id": "b01", "at": "@0.2..@4.0", "job": "x", "pic": "main@0.2-4.0"}]), idx)
    one = [s for s in shots(c) if s["id"].startswith("b01.s0")]
    if c.meter["move"].get("punched"):
        assert sum(1 for s in one if s.get("punch")) == 1                       # wide, closer, and it stays: never wide, closer, wide, closer
        ws = [round(width(rows(s)[0]), 3) for s in one]
        assert ws[0] > ws[1] and all(w <= ws[1] + 1e-3 for w in ws[1:])


def test_a_mark_that_only_touches_the_window_does_not_hold_the_take():
    c = [0.1, 0.05, 1.0, 0.94]
    assert move._cap(c, [[0.44, 0.9399, 0.93, 1.0]], 0.6, 0.3) > 1.2            # a label that starts where the window ends
    assert move._cap(c, [[0.44, 0.80, 0.93, 1.0]], 0.6, 0.3) == 1.0             # one the window cuts through


# ------------------------------------------------------------------ every checked picture reaches the film

def _work(tmp_path, idx, items):
    work = str(tmp_path)
    os.makedirs(os.path.join(work, "bin"), exist_ok=True)
    json.dump(idx, open(os.path.join(work, "index.json"), "w"))
    for it in items:
        Image.linear_gradient("L").resize((320, 214)).convert("RGB").save(os.path.join(work, it["path"]))
    json.dump({"items": items}, open(os.path.join(work, "bin.json"), "w"))
    return work


def test_a_bin_picture_the_score_placed_nowhere_is_cut_in_on_its_word(tmp_path):
    idx = selfie(20.0)
    beats = beats_over(idx, 4, lambda k, a, z: {"pic": "main"})
    w0 = int(beats[2]["w"].split("-")[0]) + 2
    items = [{"id": 0, "kind": "image", "path": "bin/0.jpg", "w": 1600, "h": 1067, "for": f"w{w0}-{w0 + 2}", "wish": 0, "what": "a thing"},
             {"id": 1, "kind": "image", "path": "bin/1.jpg", "w": 1600, "h": 1067, "for": f"w{w0}-{w0 + 2}", "wish": 0, "what": "the same thing, closer"}]
    work = _work(tmp_path, idx, items)
    beats[2]["do"] = [{"g": "big", "t": "NOW", "for": "b03.s0", "at": "b03.in", "d": 30, "seat": [0.5, 0.7]}]
    text = film_text(beats)
    text2, rep, c2 = fill.fill(work, text)
    assert rep["unused"] == 2 and rep["placed"] == 2 and rep["ids"] == [0, 1] and c2 is not None
    pics = [(s["pic"], s["f"][0]) for s in shots(c2) if str(s.get("pic")).startswith("bin:")]
    assert {p for p, _f in pics} == {"bin:0", "bin:1"}
    at = int(round(idx["words"][w0]["t0"] * 30))
    assert abs(min(f for _p, f in pics) - at) <= 14                             # on the first word it was found for (a cut may wait for a breath)
    lines = [json.loads(x) for x in text2.splitlines() if x.strip()]
    b3 = next(o for o in lines if o.get("id") == "b03")
    take = b3["pic"].index("main")
    assert b3["do"][0]["for"] == f"b03.s{take}"                                # the title that named the take's shot still names the take
    assert c2.l1["dur_f"] == comp(text, idx, bin_items=BIN).l1["dur_f"]


def test_a_picture_the_score_names_or_whose_words_are_gone_is_left_alone(tmp_path, monkeypatch):
    idx = selfie(20.0)
    beats = beats_over(idx, 4, lambda k, a, z: {"pic": "main"})
    last = len(idx["words"]) - 1
    items = [{"id": 0, "kind": "image", "path": "bin/0.jpg", "w": 1600, "h": 1067, "for": "w0-2", "wish": 0, "what": "the hook"},
             {"id": 1, "kind": "image", "path": "bin/1.jpg", "w": 1600, "h": 1067, "for": f"w{last}-{last}", "wish": 1, "what": "the last word"}]
    work = _work(tmp_path, idx, items)
    text = film_text(beats)
    text2, rep, c2 = fill.fill(work, text)
    assert c2 is None and text2 == text and rep["placed"] == 0                  # the film's first half second and its last line are the Score's
    monkeypatch.setenv("VALMERA_FILL", "0")
    assert fill.fill(work, text)[1] == {"unused": 0, "placed": 0, "beats": 0}


def test_a_designed_picture_of_type_is_shown_whole_and_never_cut_inside_itself():
    idx = selfie(20.0)
    bin_items = dict(BIN, **{"0": dict(BIN["0"], w=1080, h=1920, design={"kind": "number"}, provider="designed")})
    text = film_text(beats_over(idx, 4, lambda k, a, z: {"cut": [30, 48, 999], "pic": ["main", "bin:0", "main"]} if k == 1 else {"pic": "main"}))
    c = comp(text, idx, bin_items=bin_items)
    parts = [s for s in shots(c) if s["id"].startswith("b02.s1")]
    assert [s["id"] for s in parts] == ["b02.s1"]
    for r in rows(parts[0]):
        assert width(r) >= 0.98 and r[0] >= -1e-6 and r[2] <= 1 + 1e-6          # its words never leave the frame


# ------------------------------------------------------------------ sound in a Director film

def _sound_ctx(dur_s, existing, events, np10=6.0, bed=True, own_tiers=True):
    fps = 30.0
    return events, {"fps": fps, "dur_f": int(dur_s * fps), "existing": existing, "speech": None, "voice": None, "words": [], "bed": bed,
                    "np10": np10, "seed": 7, "own_tiers": own_tiers}


def test_the_directors_own_sounds_take_their_place_in_the_one_hierarchy():
    lib = sound.library()
    hit = next(k for k, v in lib.items() if v.get("role") == "impact")
    riser = next(k for k, v in lib.items() if v.get("role") == "riser")
    existing = [{"id": "b01.snd0", "s": hit, "file": lib[hit]["file"], "event_f": 6, "start_s": 0.0, "trim_s": 0.0, "db": -30.0, "peak_s": 0.2},
                {"id": "b09.snd0", "s": riser, "file": lib[riser]["file"], "event_f": 560, "start_s": 17.6, "trim_s": 0.0, "db": -30.0, "peak_s": 1.0},
                {"id": "b09.snd1", "s": hit, "file": lib[hit]["file"], "event_f": 560, "start_s": 18.4, "trim_s": 0.0, "db": -30.0, "peak_s": 0.2}]
    events = [{"f": f, "kind": "picture", "id": f"s{f}", "still": False} for f in range(60, 520, 45)]
    as_written = [dict(e) for e in existing]
    out, rep = sound.plan_sounds(*_sound_ctx(20.0, existing, events))
    own = {s["id"]: s for s in out if s["id"].startswith("b0")}
    assert own["b01.snd0"]["tier"] == "hero" and own["b09.snd1"]["tier"] == "hero"         # the hook and the close are the film's hero hits
    assert own["b01.snd0"]["director_db"] == -30.0 and own["b01.snd0"]["db"] > -30.0 + 8    # at the hero's level, not where a guess left them
    body = [s["db"] for s in out if s.get("tier") in ("body", "tick")]
    assert not body or own["b09.snd1"]["db"] >= max(body) - 6.0                              # and no body sound is the film's loudest by a class
    assert own["b09.snd0"]["db"] <= own["b09.snd1"]["db"] + 12.0                             # the climb is set against its hit
    plain, _rep = sound.plan_sounds(*_sound_ctx(20.0, as_written, events, own_tiers=False))
    kept = {s["id"]: s for s in plain if s["id"].startswith("b0")}
    assert kept["b01.snd0"].get("tier") is None and kept["b01.snd0"]["db"] < own["b01.snd0"]["db"]      # a caller that sets its own sounds keeps the role levels


def test_a_film_of_some_pictures_carries_small_ticks_on_its_type():
    events = [{"f": f, "kind": "display", "id": f"b{f}.big"} for f in range(40, 580, 40)] + [{"f": f, "kind": "picture", "id": f"s{f}", "still": False} for f in (90, 300, 500)]
    out, rep = sound.plan_sounds(*_sound_ctx(20.0, [], sorted(events, key=lambda e: e["f"]), np10=3.0))
    assert rep["class"] == "M" and rep["allow_ticks"] >= 2
    assert not any("small ticks" in d["why"] and "carries 0" in d["why"] for d in rep["dropped"])


def test_the_films_sounds_and_its_arranged_bed_are_one_mix(tmp_path, monkeypatch):
    from scene import audio as AU
    from score import bed
    work = str(tmp_path)
    sr, dur = AU.SR, 6.0
    t = np.arange(int(sr * dur)) / sr
    song = (0.25 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    raw = os.path.join(work, "song.f32")
    np.stack([song, song], axis=1).tofile(raw)
    import subprocess
    subprocess.run([AU.FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", raw, "-c:a", "aac", os.path.join(work, "bed_arranged.m4a")], check=True)
    subprocess.run([AU.FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r={sr}:cl=stereo", "-t", str(dur), "-c:a", "aac", os.path.join(work, "source.mp4")], check=True)
    json.dump({"on": True, "settled": True, "cid": "x", "title": "t", "turn_s": None, "arranged": {"file": "bed_arranged.m4a", "dur_s": dur, "end_s": dur - 0.1, "sha": "old", "drop_at": None}},
              open(os.path.join(work, bed.FILE), "w"))
    lib = sound.library()
    hit = next(k for k, v in lib.items() if v.get("role") == "impact")
    one = sound._item("snd.j0.0", hit, 90, 30.0, -6.0, {"kind": "picture"}, "hook", "hit")
    one.update(tier="hero", duck_db=sound.DUCK_DB)
    l1 = {"fps": 30, "dur_f": int(dur * 30), "spine": [{"f": [0, int(dur * 30)], "src": [0.0, dur]}], "assets": {"main": {"path": "source.mp4"}},
          "sound": [one], "sound_design": {}}
    before = AU.pcm(os.path.join(work, "bed_arranged.m4a"))
    rep = bed.one_mix(work, l1, {"words": []})
    assert rep and rep["sounds"] == 1 and rep["ducks"] == 1                     # the song makes room under the hero hit
    after = AU.pcm(os.path.join(work, "bed_arranged.m4a"))
    a, z = int(3.0 * sr), int(3.1 * sr)
    assert float(np.abs(after[a:z]).mean()) < 0.8 * float(np.abs(before[a:z]).mean())
    doc = json.load(open(os.path.join(work, bed.FILE)))
    assert doc["arranged"]["sha"] != "old" and doc["arranged"]["mixed"]["ducks"] == 1 and os.path.exists(os.path.join(work, bed.RAW_FILE))
    rep2 = bed.one_mix(work, l1, {"words": []})                                  # mixed again (the look cut a line): from the track as it was cut, not twice dipped
    again = AU.pcm(os.path.join(work, "bed_arranged.m4a"))
    assert rep2["ducks"] == 1 and abs(float(np.abs(again[a:z]).mean()) - float(np.abs(after[a:z]).mean())) < 0.02


# ------------------------------------------------------------------ the footage shelf

@pytest.fixture
def shelf(tmp_path, monkeypatch):
    import shutil
    import storage
    root = os.path.join(str(tmp_path), "objects")

    def path(k):
        return os.path.join(root, k)

    def up(p, k, ct=None):
        os.makedirs(os.path.dirname(path(k)), exist_ok=True)
        shutil.copyfile(p, path(k))
    monkeypatch.setattr(storage, "upload_file", up)
    monkeypatch.setattr(storage, "exists", lambda k: os.path.exists(path(k)))
    monkeypatch.setattr(storage, "download_to", lambda k, p, check_capacity=True: shutil.copyfile(path(k), p))
    return root


def _offer(tmp_path, name, n):
    f = os.path.join(str(tmp_path), f"clip{n}.mp4")
    open(f, "wb").write(b"clip" + bytes([n]))
    from footage import verify
    verified = dict(version=verify.VERSION, model=verify.MODEL, ok=True, subject=name, kind="thing", sha256=verify.digest(f), frames_match=[True]*3)
    return {"verification": verified, "id": f"footage:clip{n}.mp4", "provider": "footage", "kind": "video", "named": name, "shows": f"{name} on camera", "local_file": f, "width": 1080,
            "height": 1920, "duration_s": 1.5, "credit": "A Channel", "page_url": f"https://example.org/v{n}", "title": "A title",
            "_img": Image.linear_gradient("L").resize((90, 160)).convert("RGB"), "footage": {"url": f"https://example.org/v{n}", "time_range": [12.0, 13.5]}}


def test_footage_cut_once_is_on_the_shelf_for_the_next_film(tmp_path, shelf):
    from footage import cache, seam
    assert cache.get("Greenland shark") == []
    assert seam.shelve([_offer(tmp_path, "Greenland shark", 1), _offer(tmp_path, "Greenland shark", 2), _offer(tmp_path, "Oscar Piastri", 3)]) == {"Greenland shark": 2, "Oscar Piastri": 1}
    got = cache.get("greenland  SHARK")                                          # one subject, however it is written
    assert len(got) == 2 and all(c["cache_key"].startswith(cache.PREFIX) and c["provider"] == "footage" and c["_img"].size == (90, 160) for c in got)
    assert got[0]["page_url"].startswith("https://example.org/v") and got[0]["credit"] == "A Channel"      # every clip keeps its source
    assert cache.get("Greenland shark", 1920, 1080) == []                         # another frame shape is another shelf
    out = os.path.join(str(tmp_path), "out.mp4")
    seam.acquire(dict(got[0]), out, 1080, 1920)
    assert open(out, "rb").read().startswith(b"clip")
    wishes = [{"w": "3-6", "want": "the shark"}, {"w": "9-12", "want": "the driver"}, {"w": "20-22", "want": "mine", "mine": True}]
    subjects = [[{"name": "Greenland shark"}], [{"name": "Oscar Piastri"}], [{"name": "Greenland shark"}]]
    offers, have = seam.shelved(wishes, subjects)
    assert have == ["Greenland shark", "Oscar Piastri"] and [(c["_wish"], c["_w"]) for c in offers] == [(0, "3-6"), (0, "3-6"), (1, "9-12")]


def test_the_shelf_switched_off_or_unreadable_is_an_empty_shelf(tmp_path, shelf, monkeypatch):
    from footage import cache, seam
    seam.shelve([_offer(tmp_path, "Greenland shark", 1)])
    monkeypatch.setenv("VALMERA_FOOTAGE_CACHE", "0")
    assert cache.get("Greenland shark") == [] and cache.put("x", [_offer(tmp_path, "x", 2)]) == 0
    monkeypatch.delenv("VALMERA_FOOTAGE_CACHE")
    import storage
    monkeypatch.setattr(storage, "exists", lambda k: (_ for _ in ()).throw(RuntimeError("no storage")))
    assert cache.get("Greenland shark") == []


def test_the_bin_takes_what_the_shelf_holds_by_the_moment_it_is_for():
    """score.supply.run's own lines for the footage box, on a box as film.footage_start fills it."""
    foot = {"ready": threading.Event(), "cached": [{"id": "footage:a.mp4", "named": "Greenland shark", "_w": "9-12", "_wish": 0},
                                                   {"id": "footage:b.mp4", "named": "Greenland shark", "_w": "40-41", "_wish": 3}]}
    foot["ready"].set()
    wishes = [{"w": "0-2", "hook": True}, {"w": "9-12"}, {"w": "20-24"}]          # Supply's own numbering: a hook wish came first
    at = {str(w.get("w") or w.get("at")): i for i, w in reversed(list(enumerate(wishes)))}
    took = [dict(c, _wish=at[str(c.get("_w"))]) for c in foot["cached"] if str(c.get("_w")) in at]
    assert [(c["id"], c["_wish"]) for c in took] == [("footage:a.mp4", 1)]       # the moment the film still has, by its own number


def test_footage_names_what_real_video_exists_of():
    from footage import seam
    text = seam.NAME_SYSTEM
    for word in ("species", "therapy", "named dish", "phenomenon"):
        assert word in text
    assert "not the customer's own product" in text and "an office" in text      # what a library clip shows, and what is the customer's, stay out


# ------------------------------------------------------------------ supply, and what the repair round is told

def test_a_view_of_the_upload_never_cuts_through_a_head():
    from score import supply
    per = {"samples": [{"t": 1.0, "face": [0.4, 0.5, 0.6, 0.65]}]}
    assert supply.keep_heads(per, 1.0, [0.2, 0.0, 0.76, 0.5], [0.3, 0.05, 0.7, 0.3]) is None      # p5591: the photograph above the speaker, ending at his eyes
    assert supply.keep_heads(per, 5.0, [0.2, 0.0, 0.76, 0.5], [0.3, 0.05, 0.7, 0.3]) == [0.2, 0.0, 0.76, 0.5]      # nobody is there at that second
    moved = supply.keep_heads(per, 1.0, [0.1, 0.5, 0.66, 1.0], [0.15, 0.6, 0.3, 0.85])             # a detail beside him: the window moves to hold the head whole
    assert moved is not None and moved[1] <= 0.41 + 2e-3 and moved[3] >= 0.6725 - 2e-3 and moved[3] - moved[1] == pytest.approx(0.5)
    assert supply.keep_heads({"samples": []}, 1.0, [0.2, 0.0, 0.76, 0.5], None) == [0.2, 0.0, 0.76, 0.5]


def test_a_view_is_never_closer_than_the_source_has_pixels_for():
    from score import supply
    crop = supply.reframe([0.4, 0.4, 0.5, 0.5], 576, 1024)
    assert (crop[3] - crop[1]) * 1024 >= 1920 / supply.VIEW_UPSCALE - 2                             # a 576 px upload: 640 px of it at least
    assert supply.reframe([0.4, 0.4, 0.5, 0.5], 304, 540) == pytest.approx([0.0, 0.0, 1.0, 1.0], abs=2e-3)      # a 304 px upload has no closer view


def test_the_scout_is_given_a_count_for_a_talking_upload_and_none_for_a_wordless_one():
    from score import scout
    idx = selfie(40.0)
    per = {"dur": 40.0, "W": 1080, "H": 1920, "shots": [{"id": 1, "t": [0.0, 40.0], "face_h": 0.2}]}
    n = scout.wishes_wanted(idx, per)
    assert 8 <= n <= scout.WISHES_MOST and f"about {n} times" in scout.user_text(idx, {}, per)
    assert "gets none" in scout.user_text(idx, {}, per)                                              # a count is a guide: no picture is asked for words that name nothing
    quiet = wordless_index(8.0)
    assert scout.wishes_wanted(quiet, {"dur": 8.0}) is None and "times" not in scout.user_text(quiet, {}, {"dur": 8.0, "W": 1080, "H": 1920, "shots": [{"id": 1, "t": [0.0, 8.0]}]}).split("Picture:")[-1]


def test_a_wordless_film_that_says_nothing_after_its_title_is_told_the_chapter_form():
    idx = wordless_index(12.0)
    beats = [{"k": "b", "id": f"b{k + 1:02d}", "at": f"@{k * 2.0:.1f}..@{k * 2.0 + 1.9:.1f}", "job": "x", "pic": f"main@{k * 2.0:.1f}-{k * 2.0 + 1.9:.1f}"} for k in range(5)]
    c = comp(film_text(beats), idx)
    told = [f for f in c.meter["findings"] if f.startswith("type: between its title")]
    if len(S.load_bin(None)) == 0 and c.meter["taste"]["supply"]["np"] >= 3:
        assert told and '"g":"counter"' in told[0]
    said = selfie(20.0)
    c2 = comp(film_text(beats_over(said, 4, lambda k, a, z: {"pic": "main"})), said, voice(20.0))
    assert not [f for f in c2.meter["findings"] if f.startswith("type: between its title")]          # a film that speaks is saying something


def test_a_film_that_is_still_the_upload_is_told_to_the_repair_round():
    idx = wordless_index(8.0)
    c = comp(film_text([{"k": "b", "id": "b01", "at": "@0..@7.8", "job": "x", "pic": "main"}]), idx, move=None) if False else \
        comp(film_text([{"k": "b", "id": "b01", "at": "@0..@7.8", "job": "x", "pic": "main"}]), idx)
    if c.meter["facts"]["class"] in ("light_touch", "same_upload"):
        assert any(f.startswith("edit: this is still the upload") for f in c.meter["findings"])


def test_a_picture_the_score_only_weighed_as_a_hook_is_still_cut_in(tmp_path):
    idx = selfie(20.0)
    beats = beats_over(idx, 4, lambda k, a, z: {"pic": "main"})
    w0 = int(beats[2]["w"].split("-")[0]) + 2
    items = [{"id": 0, "kind": "image", "path": "bin/0.jpg", "w": 1600, "h": 1067, "for": f"w{w0}-{w0 + 2}", "wish": 0, "what": "a thing"}]
    work = _work(tmp_path, idx, items)
    text = film_text(beats).replace('"k": "treat"', '"k": "treat", "hooks": [{"pic": "bin:0", "line": "x"}]', 1)
    assert "bin:0" in text
    _t, rep, c2 = fill.fill(work, text)
    assert rep["placed"] == 1 and c2 is not None
