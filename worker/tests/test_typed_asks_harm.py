"""Typed requests, round 4 (lane E, 13 Nov 2026): nothing unasked without a reason. Faces stay whole
(measured heads, the renderer's camera math), a picture's source is read against the place named,
length asks hold, names are spelled as the customer spells them, a Patch never writes a value the
schema has no name for, and the reply says what the rendered film shows."""
import json
from types import SimpleNamespace as NS

import agent_loop
import agent_tools
from score import asks, patch

# A vertical talking take: her head sits high in the frame (index fractions of the upload).
INDEX = {"video": {"width": 1080, "height": 1920, "duration": 20.0},
         "spatial": {"samples": [{"t": float(t), "faces": [[0.36, 0.14, 0.64, 0.30]]} for t in range(0, 20, 2)]}}
EDL = {"keep": [[0.0, 20.0]],
       "captions": {"mode": "from_transcript", "style": {"color": "#FFFFFF", "size": "m"}},
       "overlays": [{"id": "ov1", "kind": "image", "asset_key": "fetched/a.jpg", "start": 12.0, "duration_s": 3.0, "fit": "cover"}]}


def _edl(**kw):
    out = json.loads(json.dumps(EDL))
    out.update(kw)
    return out


class _Ctx(NS):
    def latest_edl(self):
        return {"version": self.versions_written[-1] if self.versions_written else 1, "json": self.edl}

    def write_edl(self, new, desc):
        self.edl = new
        self.versions_written.append(len(self.versions_written) + 2)
        self.desc = desc
        return f"EDL v{self.versions_written[-1]}: ok"


def _ctx(edl, **kw):
    return _Ctx(**dict(dict(edl=edl, versions_written=[], index=INDEX, duration=20.0, has_main_video=True), **kw))


# ------------------------------------------------------------------ faces stay whole

def test_a_punch_in_that_cuts_her_head_is_measured_and_refitted():
    # p5464: 36-58% punch-ins centred low cut the top of her head.
    edl = _edl(effects={"zooms": [{"id": "zm1", "start": 2.0, "end": 5.0, "strength": 0.5, "mode": "punch", "cx": 0.5, "cy": 0.7},
                                  {"id": "zm2", "start": 6.0, "end": 9.0, "strength": 0.05, "mode": "push_in", "cx": 0.5, "cy": 0.5}]})
    cuts = asks.face_cuts(edl, INDEX)
    assert set(cuts) == {"zm1"}
    assert "CUTS A FACE" in next(r for r in asks.move_rows(edl, index=INDEX) if r.startswith("move:zm1"))
    new, notes = asks.fit_faces(edl, INDEX)
    assert asks.face_cuts(new, INDEX) == {} and notes and "zm1" in notes[0]
    zm1 = next(z for z in new["effects"]["zooms"] if z["id"] == "zm1")
    assert zm1["strength"] <= 0.5 and zm1["cy"] < 0.5
    assert next(z for z in new["effects"]["zooms"] if z["id"] == "zm2") == edl["effects"]["zooms"][1]


def test_a_travelling_move_that_cuts_a_head_is_taken_out_and_one_under_a_cutaway_is_not_measured():
    edl = _edl(effects={"zoom_paths": [{"id": "zp1", "start": 2.0, "end": 5.0, "strength": 0.6, "mode": "path",
                                        "path": [{"f": 0.0, "cx": 0.5, "cy": 0.9, "s": 0.6}, {"f": 1.0, "cx": 0.5, "cy": 0.9, "s": 0.6}]}],
                        "zooms": [{"id": "zm3", "start": 12.2, "end": 14.8, "strength": 0.6, "mode": "punch", "cx": 0.5, "cy": 0.9}]})
    new, notes = asks.fit_faces(edl, INDEX)
    assert new["effects"]["zoom_paths"] == [] and any("zp1" in n for n in notes)
    assert [z["id"] for z in new["effects"]["zooms"]] == ["zm3"]       # the photo over it hides her


def test_no_measured_faces_or_a_reframed_film_changes_nothing():
    edl = _edl(effects={"zooms": [{"id": "zm1", "start": 2.0, "end": 5.0, "strength": 0.5, "mode": "punch", "cy": 0.7}]})
    assert asks.fit_faces(edl, {"video": INDEX["video"]}) == (None, [])
    assert asks.face_cuts(dict(edl, frame={"ratio": "16:9"}), INDEX) == {}
    assert asks.face_cuts(dict(edl, frame={"ratio": "9:16"}), INDEX)          # the upload's own shape


def test_a_portrait_placed_over_a_wide_film_is_cropped_to_hold_the_face(monkeypatch, tmp_path):
    import cv2
    import numpy as np
    import spatial
    import storage
    src = tmp_path / "portrait.jpg"
    cv2.imwrite(str(src), np.zeros((900, 600, 3), dtype=np.uint8))
    monkeypatch.setattr(storage, "download_to", lambda key, local: __import__("shutil").copy(str(src), local))
    monkeypatch.setattr(spatial, "analyze_frame", lambda path: {"faces": [[0.35, 0.10, 0.65, 0.30]]})
    ctx = NS(workdir=str(tmp_path))
    crop = patch.face_crop(ctx, "fetched/obama.jpg", [1920, 1080])
    assert crop and crop[0] == 0.0 and crop[2] == 1.0
    assert crop[1] <= 0.10 - 0.3 * 0.2 + 1e-6 and crop[3] >= 0.30
    assert abs((crop[3] - crop[1]) - (600 / 900) / (1920 / 1080)) < 1e-3
    monkeypatch.setattr(spatial, "analyze_frame", lambda path: {"faces": [[0.4, 0.45, 0.6, 0.55]]})
    assert patch.face_crop(NS(workdir=str(tmp_path / "b")), "fetched/centre.jpg", [1920, 1080]) is None
    assert patch.face_crop(ctx, "fetched/clip.mp4", [1920, 1080], "video") is None


# ------------------------------------------------------------------ a named picture is the place named

def test_a_found_picture_whose_source_names_another_city_is_kept_off(monkeypatch):
    seen = {}

    def call(ctx, name, schema, system, content, **k):
        seen["content"] = content
        return ({"rows": [{"id": "pass0", "fits": False, "why": "IMSS hospital in Xalapa, the story is Cordoba"},
                          {"id": "pass1", "fits": True, "why": "names Cordoba"}]}, {"usd": 0.001, "s": 1.0, "model": "m"})
    monkeypatch.setattr(asks, "_call", call)
    rows = [{"id": "pass0", "purpose": "IMSS hospital in Cordoba", "query": "IMSS Cordoba", "hit": {"title": "Hospital IMSS Xalapa", "page_url": "u"}},
            {"id": "pass1", "purpose": "IMSS hospital in Cordoba", "query": "IMSS Cordoba", "hit": {"description": "IMSS Cordoba"}}]
    ctx = NS()
    wrong = patch.named_sources(ctx, {"asks": [{"said": "no other hospitals"}]}, rows)
    assert list(wrong) == ["pass0"] and "Xalapa" in seen["content"] and "no other hospitals" in seen["content"]
    assert ctx._pass_source_usage["usd"] == 0.001
    assert patch.named_sources(ctx, {}, []) == {}


# ------------------------------------------------------------------ length asks hold

def test_an_ask_to_keep_the_upload_length_brings_back_what_a_cut_took(monkeypatch):
    ledger = asks.settle({"lang": "es", "asks": [{"said": "keep my full narration", "want": "all of it", "kind": "keep",
                                                  "length": "upload", "items": [], "how": "measure", "where": "", "pass": ""}]},
                         {"keep": [[0.0, 20.0]]})
    assert ledger["asks"][0]["length"] == "upload"
    ctx = _ctx({"keep": [[0.0, 8.0], [9.0, 17.0]]})
    calls = []
    monkeypatch.setattr(agent_tools, "restore_range", lambda c, a, b: calls.append((a, b)) or "EDL v5: restored")
    note = agent_loop._typed_hold_length(ctx, ledger, {"id": 1})
    assert calls == [(0.0, 20.0)] and ledger["held"] == {"from_s": 16.0, "to_s": 20.0} and "length" in note
    calls.clear()
    assert agent_loop._typed_hold_length(_ctx({"keep": [[0.0, 20.0]]}), ledger, {"id": 1}) == "" and not calls
    ledger["asks"][0]["length"] = "none"
    assert agent_loop._typed_hold_length(ctx, ledger, {"id": 1}) == "" and not calls


# ------------------------------------------------------------------ harm nobody asked for

def test_the_check_names_harm_and_the_repair_takes_out_only_the_ids_it_printed():
    edl = _edl(effects={"zooms": [{"id": "zm1", "start": 2.0, "end": 4.0, "strength": 0.5, "mode": "punch"},
                                  {"id": "zm2", "start": 6.0, "end": 8.0, "strength": 0.05, "mode": "push_in"}]},
               sfx=[{"id": "s1", "storage_key": "sound:whoosh", "at": 2.0}])
    verdict = {"asks": [{"id": "a1", "state": "done", "seen": "captions on", "line": "ok"}],
               "unasked": asks.harm_rows([{"what": "15 punch-ins", "at_s": 2, "evidence": "moves rows", "items": ["move:zm1", "sfx:s1", "captions"], "fix": "remove"},
                                          {"what": "brand misspelled", "at_s": 27.6, "evidence": "Piranha", "items": [], "fix": "respell"}])}
    ctx = _ctx(edl)
    ledger = {"asks": [{"id": "a1", "want": "captions", "kind": "edit"}]}
    note = asks.undo_harm(ctx, verdict, ledger)
    assert [z["id"] for z in ctx.edl["effects"]["zooms"]] == ["zm2"] and ctx.edl["sfx"] == []
    assert ctx.edl["captions"] and "move:zm1" in note and ledger["undone"]
    repair = asks.repair_note(ledger, verdict, note)
    assert "every ask is met" in repair and "brand misspelled" in repair and "set_caption_fixes" in repair
    assert asks.outcome(ledger, verdict, "fulfilled") == "partial"
    assert asks.outcome(ledger, {"asks": verdict["asks"], "unasked": []}, "fulfilled") == "fulfilled"


def test_harm_alone_is_owed_a_repair(monkeypatch):
    ledger = asks.settle({"lang": "en", "asks": [{"said": "captions", "want": "captions", "kind": "edit", "items": [],
                                                  "how": "look", "where": "", "pass": ""}]}, EDL)
    ledger["cfg"] = {"repairs": 1, "check": True}
    ctx = _ctx(_edl(effects={"zooms": [{"id": "zm1", "start": 2.0, "end": 4.0, "strength": 0.5, "mode": "punch"}]}),
               ask_ledger=ledger, ask_verdict=None, last_preview={"edl_version": 1})
    monkeypatch.setattr(asks, "check", lambda *a, **k: {"asks": [{"id": "a1", "state": "done", "seen": "s", "line": "l"}],
                                                        "unasked": [{"what": "punch-ins", "at_s": 2.0, "evidence": "e",
                                                                     "items": ["move:zm1"], "fix": "remove"}], "usage": {}})
    monkeypatch.setattr(agent_loop, "_turn_change_list", lambda c: "")
    note = agent_loop._asks_check(ctx, "draft")
    assert note and "punch-ins" in note and ctx.edl["effects"]["zooms"] == [] and ledger["repairs"] == 1


# ------------------------------------------------------------------ the reply says what the film shows

def test_a_reply_that_denies_what_the_frames_show_is_corrected(monkeypatch):
    # p5460: "the b-roll is not in the film" while the check saw every insert.
    monkeypatch.setattr(asks, "_call", lambda *a, **k: (
        {"wrong": ["The b-roll was not added."], "reply": "The b-roll is in: the Himalayas at 10.5 s."},
        {"usd": 0.001, "s": 1.0, "model": "m"}))
    ledger = {"asks": [{"id": "a1", "said": "add b-roll"}]}
    verdict = {"asks": [{"id": "a1", "state": "done", "seen": "mountains at 10.5 s", "line": "l"}]}
    assert asks.true_reply(NS(), ledger, verdict, "The b-roll was not added.") == "The b-roll is in: the Himalayas at 10.5 s."
    assert ledger["reply_fixed"] == ["The b-roll was not added."]
    monkeypatch.setattr(asks, "_call", lambda *a, **k: ({"wrong": [], "reply": ""}, {"usd": 0, "s": 0, "model": "m"}))
    assert asks.true_reply(NS(), ledger, verdict, "All in.") == "All in."
    assert asks.true_reply(NS(), ledger, {"asks": []}, "All in.") == "All in."


# ------------------------------------------------------------------ names as the customer spells them

def test_names_in_the_captions_are_respelled_from_the_customers_words_and_the_frames(monkeypatch):
    seen = {}

    def call(ctx, name, schema, system, content, **k):
        seen["content"] = content
        return ({"fixes": [{"heard": "Piranha", "right": "Marani", "evidence": "MARANI on the jar"},
                           {"heard": "honey", "right": "honey", "evidence": "same"}]}, {"usd": 0.001, "s": 1.0, "model": "m"})
    monkeypatch.setattr(asks, "_call", call)
    monkeypatch.setattr(asks, "spoken", lambda ctx, edl, limit=9000: "[27.6-28.4] this is Piranha honey")
    fixes = []
    monkeypatch.setattr(agent_tools, "set_caption_fixes", lambda ctx, reps, operation="replace": fixes.extend(reps) or "EDL v4: fixed")
    ctx = _ctx(_edl(), index=dict(INDEX, shots=[{"caption": {"on_screen_text": "MARANI raw honey"}}]))
    ledger = {"asks": []}
    note = asks.fix_names(ctx, ledger, "Add nature captions", [{"role": "user", "content": "my brand Marani"}])
    assert fixes == [{"from": "Piranha", "to": "Marani"}] and "Marani" in note and ledger["names"]
    assert "MARANI raw honey" in seen["content"] and "my brand Marani" in seen["content"]
    assert asks.fix_names(_ctx(_edl(captions=None)), ledger, "x") == ""


# ------------------------------------------------------------------ the Patch writes only values the schema names

def test_a_patch_value_the_schema_refuses_is_corrected_then_put_back(monkeypatch):
    # p5464: effect "soft-shadow" where only chroma, chrome or glow exist; the Patch fell to the loop.
    answers = [{"ops": [{"item": "captions", "set_json": '{"style": {"effect": "soft-shadow", "color": "#F3EBDC"}}', "remove": False}], "say": "s"},
               {"ops": [{"item": "captions", "set_json": '{"style": {"effect": "shadow", "color": "#F3EBDC"}}', "remove": False}], "say": "s"}]
    contents = []

    def call(ctx, name, schema, system, content, **k):
        contents.append(content)
        return answers.pop(0), {"usd": 0.001, "s": 1.0, "model": "m"}
    monkeypatch.setattr(asks, "_call", call)
    ledger = asks.settle({"lang": "en", "asks": [{"said": "nature captions", "want": "cream captions", "kind": "change",
                                                  "items": ["captions"], "how": "look", "where": "", "pass": ""}]}, EDL)
    ctx = _ctx(_edl())
    got = patch.run(ctx, ledger, "nature captions")
    assert "effect: one of chroma, chrome, glow" in contents[0]
    assert "values the schema refuses" in contents[1] and "captions" in contents[1]
    assert got["version"] and ctx.edl["captions"]["style"]["color"] == "#F3EBDC"
    assert "effect" not in ctx.edl["captions"]["style"] and any("kept as it was" in r for r in got["refused"])


def test_a_held_length_refuses_any_write_that_cuts_the_upload_shorter():
    ledger = {"asks": [{"id": "a1", "length": "upload"}]}
    ctx = NS(ask_ledger=ledger, has_main_video=True)
    prev = {"version": 9, "json": {"keep": [[0.0, 473.3]]}}
    why = agent_tools._length_held_refusal(ctx, prev, {"keep": [[0.0, 100.0], [102.0, 473.3]]})
    assert why and "473.3 s to 471.3 s" in why
    assert agent_tools._length_held_refusal(ctx, prev, {"keep": [[0.0, 473.3]]}) is None
    ctx.ask_ledger = {"asks": [{"id": "a1", "length": "none"}]}
    assert agent_tools._length_held_refusal(ctx, prev, {"keep": [[0.0, 100.0]]}) is None
    assert agent_tools._length_held_refusal(NS(has_main_video=True), prev, {"keep": [[0.0, 100.0]]}) is None
