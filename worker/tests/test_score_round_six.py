"""First films toward the judge, round six (week-1112 lane K, 14 Nov 2026).

What these hold: a found still fills the frame, moves and is cut inside itself; a wordless stretch of the take is
cut inside itself to closer framings and one under a voice never is; a counter keeps the take whole; captions build
word by word from one left edge; a finish is baked into the still that carries it and nowhere else; the critic's
choice of the film a customer holds does not stand when that film is worse than its upload; a paying account's first
film is admitted by one door and counted without a trial budget; footage that arrives after the film joins it as the
next version, and is given up the moment the customer asks for something. Fixtures are synthetic Scores and index
stubs. No customer media is here."""
import json
import os
import time
import types

import pytest

import score.compiler as S
from score import call, film, flags, lower, move, pace, reel, sound, taste
from tests.test_score_compiler import BIN, wordless_index
from tests.test_score_taste import beats_over, comp, film as film_text, selfie


def shots(c):
    return sorted((i for i in c.l1["items"] if i["k"] == "shot"), key=lambda i: i["f"][0])


# ------------------------------------------------------------------ found pictures at the judge's pace

def test_a_found_still_held_a_second_is_cut_inside_itself_and_every_part_moves():
    idx = selfie(20.0)
    text = film_text(beats_over(idx, 4, lambda k, a, z: {"cut": [30, 48, 999], "pic": ["main", f"bin:{k}", "main"]} if k == 1 else {"pic": "main"}))
    c = comp(text, idx)
    parts = [s for s in shots(c) if s["id"].startswith("b02.s1")]
    assert len(parts) >= 2 and parts[0]["id"] == "b02.s1" and all(s["pic"] == parts[0]["pic"] for s in parts)      # the first keeps the shot's id
    assert sum(s["f"][1] - s["f"][0] for s in parts) == 48 and all(pace.parent(s["id"]) == "b02.s1" for s in parts)
    moving = [s for s in parts if isinstance(s["crop"][0], list) and len({tuple(r) for r in s["crop"]}) > 1]
    assert moving and c.meter["pace"]["parts_added"] >= 1 and c.meter["pace"]["stills_moving"] >= 1
    first = [tuple(s["crop"][0]) if isinstance(s["crop"][0], list) else tuple(s["crop"]) for s in parts]
    assert len(set(first)) == len(first)                                   # each part is another framing of the picture
    for s in parts:                                                        # and every framing stays inside the picture
        for r in (s["crop"] if isinstance(s["crop"][0], list) else [s["crop"]]):
            assert -1e-6 <= r[0] < r[2] <= 1 + 1e-6 and -1e-6 <= r[1] < r[3] <= 1 + 1e-6


def test_the_pace_switched_off_is_the_film_as_round_five_cut_it(monkeypatch):
    idx = selfie(20.0)
    text = film_text(beats_over(idx, 4, lambda k, a, z: {"cut": [30, 48, 999], "pic": ["main", f"bin:{k}", "main"]} if k == 1 else {"pic": "main"}))
    monkeypatch.setenv("VALMERA_PACE", "0")
    c = comp(text, idx)
    assert [s["id"] for s in shots(c) if s["id"].startswith("b02.s1")] == ["b02.s1"]
    assert c.meter["pace"] == {"parts_added": 0, "take_punches": 0, "stills_moving": 0, "soft_starts": 0}


def test_a_wordless_stretch_of_the_take_is_cut_inside_itself_and_a_spoken_one_never_is():
    idx = wordless_index(8.0)
    text = film_text([{"k": "b", "id": "b01", "at": "@0.2..@4.0", "job": "x", "pic": "main@0.2-4.0"},
                      {"k": "b", "id": "b02", "at": "@4.6..@7.8", "job": "x", "pic": "main@4.6-7.8"}])
    c = comp(text, idx)
    one = [s for s in shots(c) if s["id"].startswith("b01.s0")]
    assert len(one) >= 3 and [s["punch"] for s in one] == list(range(len(one)))
    assert all(s["f"][1] - s["f"][0] >= pace.PUNCH_MIN_F for s in one)
    assert abs(one[-1]["src"][1] - one[0]["src"][0] - (one[-1]["f"][1] - one[0]["f"][0]) / 30.0) < 0.2       # the take plays on through the cuts
    assert c.meter["pace"]["take_punches"] >= 2
    ev = sound.l1_events(c.l1)
    assert any(e["kind"] == "cut" and e["id"] == one[1]["id"] for e in ev)        # a cut the eye sees is a cut the sound may land on
    talk = selfie(20.0)
    said = comp(film_text(beats_over(talk, 4, lambda k, a, z: {"pic": "main"})), talk)
    assert all(s.get("punch") is None for s in shots(said))                       # under a voice the take is eased, never punched


def test_a_punched_stretch_steps_closer_and_never_back_to_the_framing_it_left():
    assert move.punch_levels(1.34) == [1.0, pytest.approx(1.34 ** 0.5), 1.34] and len(move.punch_levels(move.PUNCH_MAX)) == 3
    assert move.punch_levels(1.2) == [1.0, 1.2] and move.punch_levels(1.05) == []
    idx = wordless_index(8.0)
    c = comp(film_text([{"k": "b", "id": "b01", "at": "@0.2..@4.0", "job": "x", "pic": "main@0.2-4.0"}]), idx)
    one = [s for s in shots(c) if s["id"].startswith("b01.s0")]
    widths = [round((s["crop"][0] if isinstance(s["crop"][0], list) else s["crop"])[2] - (s["crop"][0] if isinstance(s["crop"][0], list) else s["crop"])[0], 3) for s in one]
    if c.meter["move"].get("punched"):                                            # the picture had the room: no part repeats the one before
        assert all(a != b for a, b in zip(widths, widths[1:]))


def test_a_view_of_the_upload_stays_inside_one_shot_of_it():
    from score import supply
    per = {"shots": [{"id": 1, "t": [0.0, 8.0]}, {"id": 2, "t": [8.0, 10.0]}],
           "samples": [{"t": 0.25 + 0.5 * i, "diff": (None if i == 0 else 47.0 if i in (4, 8, 12, 16) else 17.0)} for i in range(20)]}
    cuts = supply._own_cuts(per)
    assert (1.75, 2.25) in cuts and (3.75, 4.25) in cuts and any(lo < 8.0 < hi for lo, hi in cuts)      # the index listed two shots; the frames show five
    assert supply._own_shot(per, 0.31, 10.0) == (0.0, 1.75) and supply._own_shot(per, 4.7, 10.0) == (4.25, 5.75)
    assert supply._own_shot(per, 4.07, 10.0) is None                    # a cut lies beside that frame: which shot it is cannot be said
    assert supply._own_shot({"samples": [{"t": 0.25 + 0.5 * i, "diff": 12.0} for i in range(20)]}, 3.0, 10.0) == (0.0, 10.0)      # one take


def test_a_counter_keeps_the_take_whole_under_it():
    idx = wordless_index(8.0)
    text = film_text([{"k": "b", "id": "b01", "at": "@0.2..@4.0", "job": "x", "pic": "main@0.2-4.0",
                       "do": [{"g": "counter", "from": 0, "to": 4, "for": "b01.s0", "at": "b01.in..b01.out", "fmt": "{n}/4", "size": 0.06, "seat": [0.5, 0.3]}]}],
                     system={"devices": ["counter"], "sounds": [], "seats": [[0.5, 0.3]]})
    c = comp(text, idx)
    assert [s["id"] for s in shots(c) if s["id"].startswith("b01.s0")] == ["b01.s0"]


# ------------------------------------------------------------------ captions that build

def test_a_chunk_is_written_word_by_word_from_one_left_edge():
    idx = selfie(20.0)
    c = comp(film_text(beats_over(idx, 4, lambda k, a, z: {"pic": "main", "type": f"{a}-{a + 2}|{a + 3}-{min(z, a + 5)}"})), idx)
    rep = {}
    caps = lower.lower_captions(c.l1, rep)
    assert rep.get("captions_built", 0) >= 1
    built = [x for x in caps if x["style"].get("anchor_x") is not None]
    assert built and all(x["style"]["text_align"] == "left" and x["style"]["single_line"] for x in built)
    by_edge = {}
    for x in built:
        by_edge.setdefault((x["style"]["anchor_x"], x["style"]["anchor_y"]), []).append(x)
    grew = [rows for rows in by_edge.values() if len(rows) >= 2]
    assert grew                                                                   # one line, written longer each time
    for rows in grew:
        rows.sort(key=lambda x: x["start"])
        for p, q in zip(rows, rows[1:]):
            if q["text"].startswith(p["text"]):
                assert len(q["text"]) > len(p["text"]) and q["start"] >= p["start"]


def test_the_build_switched_off_sets_a_chunk_whole(monkeypatch):
    idx = selfie(20.0)
    c = comp(film_text(beats_over(idx, 4, lambda k, a, z: {"pic": "main", "type": f"{a}-{a + 2}|{a + 3}-{min(z, a + 5)}"})), idx)
    monkeypatch.setenv("VALMERA_CAP_BUILD", "0")
    rep = {}
    caps = lower.lower_captions(c.l1, rep)
    assert not rep.get("captions_built") and all(x["style"].get("anchor_x") is None for x in caps)


def test_anchor_x_is_a_frame_fraction_and_old_edls_render_as_before():
    import captions
    import schemas
    assert schemas.CaptionStyle(anchor_x=0.25).anchor_x == 0.25 and schemas.CaptionStyle().anchor_x is None
    with pytest.raises(ValueError):
        schemas.CaptionStyle(anchor_x=1.4)
    assert "anchor_x" in captions.STYLE_KEYS and captions.DEFAULT_STYLE["anchor_x"] is None


# ------------------------------------------------------------------ finishes by section

def test_a_finish_is_baked_into_the_still_that_carries_it(tmp_path):
    from PIL import Image
    still = str(tmp_path / "s.jpg")
    Image.new("RGB", (1200, 800), (180, 90, 40)).save(still)
    rects = [[0.2 + 0.01 * i, 0.0, 0.2 + 0.01 * i + (720 / 1280) * (800 / 1200), 1.0] for i in range(8)]
    assert reel.key_of(still, rects, 720, 1280, 30, 0) != reel.key_of(still, rects, 720, 1280, 30, 0, "archive")
    assert reel.key_of(still, rects, 720, 1280, 30, 0, None) == reel.key_of(still, rects, 720, 1280, 30, 0)        # a film with no finish keeps its baked clips
    for name, chain in reel.FINISHES.items():
        assert ";" not in chain and "[" not in chain and "file" not in chain, name                                  # one chain on one stream, no file


def test_the_lowering_bakes_a_marked_still_and_draws_no_accent_over_the_frame(tmp_path, monkeypatch):
    baked = []
    monkeypatch.setattr(reel, "bake", lambda still, rects, W, H, fps, out, soft=0, finish=None: (baked.append(finish), open(out, "wb").write(b"x" * 900), {"how": "stub"})[2])
    assert lower.finish_of({"treat": {"mode": "archive"}}) == "archive" and lower.finish_of({"treat": {"mode": "tape"}}) == "tape"
    assert lower.finish_of({"treat": {"mode": "neg"}}) is None and lower.finish_of({}) is None
    assert lower.FINISH_AS["archive"] == "mono" and lower.FINISH_AS["tape"] is None                                  # on a clip: black and white, or left clean
    assert set(lower.FINISH_AS) == set(reel.FINISHES)


def test_a_baked_still_is_published_as_the_clip_it_is(tmp_path, monkeypatch):
    import db as dbx
    import music_gate
    import storage
    clip, plate = str(tmp_path / "abc.mp4"), str(tmp_path / "p.png")
    open(clip, "wb").write(b"x" * 900)
    open(plate, "wb").write(b"y" * 90)
    json.dump({"w": 720, "h": 1280, "dur": 0.87, "fps": 30.0, "how": "ffmpeg", "finish": "archive"}, open(clip + ".json", "w"))
    sent, rows = [], []
    monkeypatch.setattr(storage, "upload_file", lambda p, key, ct=None: sent.append((os.path.basename(p), ct)))
    monkeypatch.setattr(film, "_publish_bed", lambda ctx, db, edl: None)

    def run(fn, *a, **k):
        if fn is dbx.insert_asset:
            rows.append((a[1], k))
        return None
    ctx = types.SimpleNamespace(project_id=7)
    edl = {"overlays": [{"id": "ov.a", "asset_key": "today/abc.mp4", "kind": "video"}, {"id": "ov.b", "asset_key": "today/p.png", "kind": "image"}], "sfx": []}
    out = film._publish(ctx, types.SimpleNamespace(run=run), edl, {"today/abc.mp4": clip, "today/p.png": plate}, {"items": []}, "orig/k.mp4")
    assert ("abc.mp4", "video/mp4") in sent and ("p.png", "image/png") in sent
    video = next(k for kind, k in rows if kind == "video_clip")
    assert video["duration_s"] == 0.87 and video["width"] == 720 and video["height"] == 1280 and video["meta"]["finish"] == "archive"
    assert [kind for kind, _k in rows].count("image_ref") == 1                    # the plate is still an image; the clip is never one
    assert all(o["asset_key"].startswith("fetched/7/") for o in out["overlays"]) and out["overlays"][0]["asset_key"].endswith(".mp4")


def test_a_score_may_write_a_finish_on_a_bin_still():
    idx = selfie(20.0)
    text = film_text(beats_over(idx, 4, lambda k, a, z: {"cut": [30, 40, 999], "pic": ["main", {"src": f"bin:{k}", "treat": "archive"}, "main"]} if k == 1 else {"pic": "main"}))
    c = comp(text, idx)
    marked = [s for s in shots(c) if s["id"].startswith("b02.s1")]
    assert marked and all((s.get("treat") or {}).get("mode") == "archive" for s in marked)
    assert not S.validate_l1(c.l1)


# ------------------------------------------------------------------ the critic and the upload

def _critic(monkeypatch, answer, frames=True):
    from score import boards, brief
    from PIL import Image
    monkeypatch.setattr(boards, "grab", lambda p, t, height=360: Image.new("RGB", (200, height), (9, 9, 9)) if frames else None)
    monkeypatch.setattr(boards, "save", lambda im, path, quality=86: None)
    monkeypatch.setattr(brief, "image_block", lambda p: {"type": "image"})
    monkeypatch.setattr(taste.subprocess if hasattr(taste, "subprocess") else __import__("subprocess"), "run",
                        lambda *a, **k: types.SimpleNamespace(stdout="10.0\n", returncode=0, stderr=""))
    asked = {}

    def stream(model, system, content, **kw):
        asked.update(system=system, ask=content[-1]["text"])
        return types.SimpleNamespace(text=json.dumps(answer), usd=0.01)
    monkeypatch.setattr(call, "stream", stream)
    return asked


def test_a_held_film_that_is_worse_than_its_upload_cannot_hold_its_place(tmp_path, monkeypatch):
    asked = _critic(monkeypatch, {"better": "A", "a_worse_than_upload": True, "b_worse_than_upload": False, "why": "A has a bold title"})
    v, usd = taste.against("a.mp4", "b.mp4", str(tmp_path / "board.jpg"), 1e12, "m", upload="u.mp4")
    assert v["better"] == "new" and v["critic_pick"] == "current" and v["current_below_upload"] and not v["new_below_upload"] and usd == 0.01
    assert "third row" in asked["system"] and "the raw upload at the bottom" in asked["ask"]


def test_the_critic_still_keeps_a_held_film_that_stands_over_its_upload(tmp_path, monkeypatch):
    _critic(monkeypatch, {"better": "A", "a_worse_than_upload": False, "b_worse_than_upload": False, "why": "A reads"})
    v, _ = taste.against("a.mp4", "b.mp4", str(tmp_path / "board.jpg"), 1e12, "m", upload="u.mp4")
    assert v["better"] == "current" and "critic_pick" not in v
    _critic(monkeypatch, {"better": "A", "a_worse_than_upload": True, "b_worse_than_upload": True, "why": "both lose"})
    v, _ = taste.against("a.mp4", "b.mp4", str(tmp_path / "board.jpg"), 1e12, "m", upload="u.mp4")
    assert v["better"] == "current"                                                   # the new one is no better off: nothing changes hands


def test_the_critic_without_an_upload_is_the_critic_of_round_four(tmp_path, monkeypatch):
    asked = _critic(monkeypatch, {"better": "B", "why": "B shows the thing"})
    v, _ = taste.against("a.mp4", "b.mp4", str(tmp_path / "board.jpg"), 1e12, "m")
    assert v == {"better": "new", "why": "B shows the thing"} and asked["system"] == taste.AGAINST_SYSTEM


# ------------------------------------------------------------------ a paying account's first film

def _admit(cfg, **kw):
    base = dict(trial_active=False, subscribed=True, engine_on=False, source_s=40.0, payer_first=True)
    base.update(kw)
    return flags.admit(cfg, 5, **base)


def test_a_paying_accounts_first_film_has_one_door():
    on = flags.parse('{"mode": "on"}')
    assert on["payers"] is True and _admit(on) == (True, "")                          # no trial scope, no engine rule: the First cut job itself is the rule
    assert _admit(on, payer_first=False) == (False, "subscribed")                     # any other job of a subscriber: never
    assert _admit(on, url="https://x.test") == (False, "subscribed")
    assert _admit(on, source_s=181.0) == (False, "source_length")
    assert _admit(flags.parse('{"mode": "on", "payers": false}')) == (False, "subscribed")
    assert _admit(flags.parse('{"mode": "off"}')) == (False, "off")
    assert _admit(flags.parse('{"mode": "off", "test_user_ids": [5]}')) == (True, "")
    assert _admit(flags.parse('{"mode": "on", "percent": 0}')) == (True, "")          # the percent is the trial's
    # the trial's own rules read as they did
    assert flags.admit(on, 5, trial_active=True, subscribed=False, engine_on=True, source_s=40.0) == (True, "")
    assert flags.admit(on, 5, trial_active=False, subscribed=False, engine_on=True, source_s=40.0, payer_first=True) == (False, "not_trial")


def test_a_payers_film_is_counted_without_a_trial_budget(monkeypatch):
    import claude_trial
    monkeypatch.setattr(claude_trial, "current", lambda: None)
    p = film._Payer(7)
    assert p.is_test and p.spent_usd == 0.0
    call.TALLIES.append(p)
    try:
        call._meter("claude-opus-5-5", 0.12)
        call._meter("claude-haiku-5-5", 0.01)
    finally:
        call.TALLIES.remove(p)
    call._meter("claude-opus-5-5", 0.5)                                               # no film being made: nothing is counted anywhere
    assert p.spent_usd == pytest.approx(0.13)


def test_attempt_gives_a_payer_a_film_and_a_subscriber_with_no_first_cut_lane_none(monkeypatch):
    seen = {}
    monkeypatch.setattr(flags, "read", lambda db: flags.parse('{"mode": "on"}'))

    def fake(ctx, worker_db, job, started, spend, scope, cfg, payer=False):
        seen.update(payer=payer, scope=scope, tallied=scope in call.TALLIES)
        return "film"
    monkeypatch.setattr(film, "_attempt", fake)
    ctx = types.SimpleNamespace(first_cut_model="claude-opus-5-5", first_cut_q1=False, duration=40.0, first_cut_phases={})
    assert film.attempt(ctx, None, {"id": 1, "user_id": 5, "payload": {}}, 0.0, {}, None, True) == "film"
    assert seen["payer"] is True and isinstance(seen["scope"], film._Payer) and seen["tallied"] and not call.TALLIES
    seen.clear()
    ctx.first_cut_model = None                                                        # the Claude lane is off for this job: the chain, as before
    assert film.attempt(ctx, None, {"id": 1, "user_id": 5, "payload": {}}, 0.0, {}, None, True) is None and not seen


# ------------------------------------------------------------------ the quick film, then the upgraded one

class _Thread:
    def __init__(self, alive_for=0):
        self.left = alive_for

    def is_alive(self):
        return self.left > 0

    def join(self, t=None):
        self.left -= 1


def _state(tmp_path, box, version=4):
    work = str(tmp_path / "film")
    os.makedirs(work, exist_ok=True)
    comp_ = types.SimpleNamespace(json=lambda: "first", l1={}, meter={})
    st = {"work": work, "text": "score", "comp": comp_, "edl": {}, "bin_doc": {"items": []}, "original_key": "o", "cfg": {}, "footage": box, "acquire": lambda c: None}
    ctx = types.SimpleNamespace(director_state=st, director_version=version, project_id=9, first_cut_phases={}, versions_written=[version],
                                latest_edl=lambda: {"version": version, "json": {}}, apply_score=lambda edl, d: "EDL v4 -> v5: " + d)
    return ctx, st


def test_a_film_whose_footage_is_still_coming_keeps_its_folder_for_the_upgrade(tmp_path):
    ctx, st = _state(tmp_path, {"thread": _Thread(3), "wishes": []})
    film.close(ctx)
    assert ctx.director_state is None and ctx.director_upgrade is st and os.path.isdir(st["work"])
    ctx2, st2 = _state(tmp_path / "b", None)                                          # no footage switch: the folder goes as before
    film.close(ctx2)
    assert not getattr(ctx2, "director_upgrade", None) and not os.path.isdir(st2["work"])
    ctx3, st3 = _state(tmp_path / "c", {"thread": _Thread(0), "wishes": [], "offers": []})      # the engine finished with nothing
    film.close(ctx3)
    assert not getattr(ctx3, "director_upgrade", None) and not os.path.isdir(st3["work"])


def test_the_upgrade_gives_up_the_moment_the_customer_asks_for_something(tmp_path):
    ctx, st = _state(tmp_path, {"thread": _Thread(50), "wishes": []})
    film.close(ctx)
    db = types.SimpleNamespace(run=lambda fn, *a, **k: fn is film._request_waiting)
    assert film.upgrade(ctx, db, {"id": 3}, time.monotonic()) is None
    rec = ctx.first_cut_phases["director"]["footage_later"]
    assert "asked for something" in rec["skipped"] and not os.path.isdir(st["work"]) and ctx.director_upgrade is None


def test_the_upgrade_does_not_outwait_its_deadline_or_write_over_a_film_that_changed(tmp_path, monkeypatch):
    ctx, st = _state(tmp_path, {"thread": _Thread(10 ** 6), "wishes": []})
    film.close(ctx)
    db = types.SimpleNamespace(run=lambda fn, *a, **k: None)
    assert film.upgrade(ctx, db, {"id": 3}, time.monotonic() - film.UPGRADE_BY_S - 1) is None
    assert "was not there" in ctx.first_cut_phases["director"]["footage_later"]["skipped"]
    ctx, st = _state(tmp_path / "b", {"thread": _Thread(0), "wishes": [], "offers": [{"kind": "video"}]})
    film.close(ctx)
    ctx.latest_edl = lambda: {"version": 6, "json": {}}                               # the customer's edit landed meanwhile
    assert film.upgrade(ctx, db, {"id": 3}, time.monotonic()) is None
    assert "no longer the one" in ctx.first_cut_phases["director"]["footage_later"]["skipped"]


def test_the_upgrade_writes_the_footage_as_the_next_version(tmp_path, monkeypatch):
    ctx, st = _state(tmp_path, {"thread": _Thread(0), "wishes": [], "offers": [{"kind": "video"}], "seconds": 210.0})
    film.close(ctx)
    rows = [{"id": 3, "named": "Mark Webber", "page": "https://example.test/v"}, {"id": 4, "named": "Mark Webber", "page": "https://example.test/v"}]
    monkeypatch.setattr(film, "footage_into", lambda work, text, box, acquire, most=8: ("score with clips", rows))
    second = types.SimpleNamespace(json=lambda: "second", l1={"items": []}, meter={})
    monkeypatch.setattr(S, "compile_score", lambda text, work_dir=None: second)
    monkeypatch.setattr(film, "bare", lambda c: False)
    monkeypatch.setattr(lower, "lower", lambda l1, work: ({"keep": []}, {}, {"counts": {}}))
    monkeypatch.setattr(film, "_publish", lambda ctx_, db, edl, locals_, bin_doc, key: edl)
    monkeypatch.setattr(film, "_applied", lambda rep, l1, bin_doc: ["real footage"])
    monkeypatch.setattr(film, "_event", lambda *a, **k: None)
    ctx.versions_written.append(5)
    got = film.upgrade(ctx, types.SimpleNamespace(run=lambda fn, *a, **k: None), {"id": 3}, time.monotonic())
    assert got["clips"] == 2 and got["named"] == ["Mark Webber"] and got["edl_version"] == 5 and ctx.director_version == 5
    assert ctx.first_cut_phases["director"]["footage_later"]["clips"] == 2 and not os.path.isdir(st["work"])


def test_footage_is_cut_in_on_the_word_of_a_moment_the_film_showed_no_picture_for(tmp_path):
    from PIL import Image
    from tests.test_score_round_four import _work
    idx = selfie(20.0)
    work = _work(tmp_path, idx, items=[])
    os.makedirs(os.path.join(work, "bin"), exist_ok=True)
    beats = beats_over(idx, 4, lambda k, a, z: {"pic": "main"})
    text = film_text(beats)
    w0 = int(str(beats[1]["w"]).split("-")[0]) + 2                      # a word inside the second beat
    clip = {"id": "footage:a.mp4", "provider": "footage", "kind": "video", "_wish": 0, "named": "Greenland shark", "shows": "a shark in dark water",
            "width": 1080, "height": 1920, "duration_s": 1.5, "license": "Footage, not licensed", "credit": "Some Channel", "page_url": "https://example.org/v",
            "_img": Image.linear_gradient("L").resize((90, 160)).convert("RGB"), "footage": {"url": "https://example.org/v", "time_range": [12.0, 13.5]}}
    assert film._reads(clip["_img"]) and not film._reads(Image.new("RGB", (90, 160))) and not film._reads(Image.new("RGB", (90, 160), (120, 120, 120)))
    dark = dict(clip, _img=Image.new("RGB", (90, 160), (6, 6, 6)), _row=None)        # the cover crop missed its subject: a black frame is no picture
    assert film.footage_into(work, text, dict(wishes=[{"w": "3-5", "want": "x"}], offers=[dark]), lambda c: {"storage_key": "k", "dur": 1.5}) is None
    box = {"wishes": [{"w": f"{w0}-{w0 + 2}", "want": "a Greenland shark"}], "offers": [clip]}
    before = S.compile_score(text, work_dir=work)
    out = film.footage_into(work, text, box, lambda c: {"storage_key": "clips/7/a.mp4", "w": 1080, "h": 1920, "dur": 1.5, "fps": 30})
    assert out is not None
    new, rows = out
    b02 = next(json.loads(ln) for ln in new.splitlines() if '"id":"b02"' in ln)
    assert "bin:0" in b02["pic"] and b02["pic"][-1] == "main" and b02["cut"][-1] == 999 and film.INSERT_F in b02["cut"]
    assert rows[0]["named"] == "Greenland shark" and rows[0]["kind"] == "video"
    after = S.compile_score(new, work_dir=work)
    assert after.l1["dur_f"] == before.l1["dur_f"] and not S.validate_l1(after.l1)          # the same film, a picture more
    clips = [s for s in after.l1["items"] if s["k"] == "shot" and s["pic"] == "bin:0"]
    f_word = next(sp["f"][0] + round((idx["words"][w0]["t0"] - sp["src"][0]) * 30) for sp in before.l1["spine"] if sp["src"][0] <= idx["words"][w0]["t0"] < sp["src"][1])
    assert clips and abs(clips[0]["f"][0] - f_word) <= 10                                   # it lands on the word the Scout wished it for
    # a beat whose shots another line names is left as it is: an id would move
    named = film_text([dict(b, do=[{"g": "big", "t": "X", "for": "b02.s0", "at": "b02.s0.in", "d": 20}]) if b["id"] == "b02" else b for b in beats])
    json.dump({"own": [], "items": []}, open(os.path.join(work, "bin.json"), "w"))
    assert film.footage_into(work, named, dict(box, offers=[dict(clip, _row=None)]), lambda c: {"storage_key": "k", "dur": 1.5}) is None


def test_the_upgraded_film_is_rendered_once_and_said_in_one_line_and_a_failed_render_puts_the_first_film_back(monkeypatch):
    import first_cut
    got = {"clips": 2, "named": ["Mark Webber"], "edl_version": 5, "applied": [], "sources": ["https://example.test/v"], "was": {"keep": [[0, 9]]}}
    monkeypatch.setattr(film, "upgrade", lambda ctx, db, job, started: dict(got))
    renders, said, wrote = [], [], []
    monkeypatch.setattr(first_cut, "_render_cut", lambda ctx, steps, picked: renders.append(picked) or "Preview ready")
    monkeypatch.setattr(first_cut, "_on_screen", lambda ctx: True)
    ctx = types.SimpleNamespace(latest_edl=lambda: {"version": 5, "json": {}}, last_preview={"edl_version": 5}, apply_score=lambda edl, d: wrote.append(edl) or "EDL v5 -> v6")
    db = types.SimpleNamespace(run=lambda fn, *a, **k: said.append(a))
    meta = {"kind": "first_cut", "first_cut": {"steps": []}, "edl_version": 4, "preview": {"edl_version": 4}}
    first_cut._director_upgrade(ctx, db, {"id": 3}, 11, 0.0, [], {"how": "director"}, meta)
    assert len(renders) == 1 and renders[0]["footage"] and len(said) == 1 and not wrote
    session, role, text, more = said[0]
    assert session == 11 and role == "assistant" and "Mark Webber" in text and more["edl_version"] == 5 and more["preview"] == {"edl_version": 5}
    assert more["first_cut"]["footage"]["clips"] == 2 and meta["edl_version"] == 4                # the first reply's record is not rewritten
    monkeypatch.setattr(first_cut, "_on_screen", lambda ctx: False)
    first_cut._director_upgrade(ctx, db, {"id": 3}, 11, 0.0, [], {"how": "director"}, meta)
    assert len(said) == 1 and wrote == [{"keep": [[0, 9]]}]                                       # nothing is announced; the project is the film on screen again
    monkeypatch.setattr(film, "upgrade", lambda ctx, db, job, started: None)
    first_cut._director_upgrade(ctx, db, {"id": 3}, 11, 0.0, [], {"how": "director"}, meta)
    assert len(renders) == 2 and len(said) == 1                                                   # no footage: no render, no line


def test_a_first_films_footage_is_fetched_lean():
    import footage.seam as seam
    assert seam.FIRST_FILM == {"max_sources": 8, "hear_s": 0.0, "read_s": 300.0, "per": 2} and seam.LINE_S >= 3.0      # nothing heard, two clips a wish
