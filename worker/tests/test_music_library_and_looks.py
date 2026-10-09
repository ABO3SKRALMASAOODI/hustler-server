"""CC0 music library (list_music_library / add_library_music) and apply_look v2.

The library is a whitelist CATALOGUE of storage objects (worker/music/
manifest.json): an exact slug picks a row, the row's literal legacy-music/
key is copied once into the project, and the copy is placed through
add_music like any project asset. apply_look v2 keeps the classic looks and
adds premium systems that set captions, grade, texture, transition (with
owned sounds) and, on request, a ducked library bed in ONE version.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools  # noqa: E402
import db as dbx  # noqa: E402
import motion_captions  # noqa: E402
import motion_tools  # noqa: E402
import music_library  # noqa: E402
import sfx_kit  # noqa: E402
from schemas import (CaptionStyle, GradeCustom, STYLIZE_KINDS,  # noqa: E402
                     TRANSITION_STYLES, default_edl, edl_signature,
                     validate_edl)


# ── fakes ───────────────────────────────────────────────────────────────

class _Db:
    def __init__(self):
        self.assets = {}
        self.inserted = []
        self.used = []

    def run(self, fn, *a, **k):
        if fn is dbx.asset_by_key:
            return self.assets.get(a[1])
        if fn is dbx.insert_asset:
            _pid, kind, key = a
            row = {"kind": kind, "storage_key": key,
                   "duration_s": k.get("duration_s"),
                   "meta": k.get("meta") or {}}
            self.assets[key] = row
            self.inserted.append(row)
            return len(self.inserted)
        if fn is dbx.library_music_used_by_user:
            return list(self.used)
        if getattr(fn, "__name__", "") == "<lambda>":
            return []
        return None


class _Storage:
    def __init__(self, missing=()):
        self.objects = {m["storage_key"] for m in music_library.entries()
                        if m["storage_key"] not in set(missing)}
        self.copies = []

    def exists(self, key):
        return key in self.objects

    def copy_object(self, src, dst):
        if src not in self.objects:
            raise RuntimeError("An error occurred (404) when calling the "
                               "HeadObject operation: Not Found")
        self.copies.append((src, dst))
        self.objects.add(dst)


def _words(n=60, step=0.5):
    vocab = ("this changed everything about how we build companies and the "
             "number was 400 million dollars in revenue").split()
    return [{"w": vocab[i % len(vocab)], "t0": round(0.4 + i * step, 3),
             "t1": round(0.4 + i * step + 0.4, 3), "speaker": "S0"}
            for i in range(n)]


class _Ctx:
    project_id = 7

    def __init__(self, ratio="9:16", duration=40.0, speech=True, edl=None,
                 shots=None):
        self.duration = duration
        base = edl or default_edl(duration)
        base = json.loads(json.dumps(base))
        base.setdefault("frame", {})
        if ratio:
            base["frame"] = dict(base.get("frame") or {}, ratio=ratio,
                                 mode="crop")
        self._edl = validate_edl(base, duration).model_dump()
        self.writes = []
        self.db = _Db()
        self.job = {"id": 11, "user_id": 3}
        self.has_main_video = True
        words = _words() if speech else []
        self.index = {"video": {"width": 1920, "height": 1080},
                      "words": words,
                      "sentences": ([{"t0": 0.4, "t1": 30.0, "text": "x"}]
                                    if speech else []),
                      "shots": shots or []}

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        norm = validate_edl(edl, self.duration).model_dump()
        if edl_signature(norm) == edl_signature(self._edl):
            return "NO CHANGE — identical"
        self._edl = norm
        self.writes.append(desc)
        n = len(self.writes)
        return f"EDL v{n} -> v{n + 1}: {desc}"


@pytest.fixture
def store(monkeypatch):
    s = _Storage()
    monkeypatch.setattr(music_library.storage, "exists", s.exists)
    monkeypatch.setattr(music_library.storage, "copy_object", s.copy_object)
    return s


@pytest.fixture
def kit(monkeypatch):
    placed = []

    def fake(ctx, kind):
        placed.append(kind)
        return f"sfx/{ctx.project_id}/kit-{kind}.wav"
    monkeypatch.setattr(motion_tools, "ensure_kit_asset", fake)
    return placed


@pytest.fixture(autouse=True)
def _no_perception(monkeypatch):
    # emphasis picking must not try to load audio perception in unit tests
    monkeypatch.setattr(agent_tools, "_get_perception",
                        lambda ctx: (_ for _ in ()).throw(RuntimeError("off")),
                        raising=False)


# ── the catalogue ───────────────────────────────────────────────────────

def test_manifest_is_a_cc0_whitelist_of_literal_storage_objects():
    rows = music_library.entries()
    assert len(rows) == 24
    assert len(music_library.catalog()) == 23
    for r in rows:
        assert r["license"] == "CC0"
        assert r["storage_key"] == "legacy-music/" + r["file"]
        assert r["source_url"].startswith("https://freemusicarchive.org/")
        assert r["mood"] in music_library.MOODS
        assert isinstance(r["bytes"], int) and len(r["sha256"]) == 64
    assert {r["mood"] for r in music_library.catalog()} == \
        set(music_library.MOODS)


def test_manifest_rows_with_foreign_keys_are_never_loaded():
    good = dict(music_library.entries()[1])
    assert music_library._valid_row(good)
    for bad in ({"storage_key": "music/1/secret.mp3"},
                {"storage_key": "legacy-music/../renders/x.mp3"},
                {"file": "../x.mp3"},
                {"license": "CC BY"},
                {"mood": "techno"},
                {"slug": "Hip Hop!"}):
        assert not music_library._valid_row(dict(good, **bad)), bad


def test_find_is_exact_slug_only():
    t, err = music_library.find("hiphop-abducted")
    assert t and t["title"] == "Abducted" and err is None
    assert music_library.find("library:hiphop-abducted")[0]["slug"] == \
        "hiphop-abducted"                       # stale pre-August reference
    for probe in ("hiphop", "hiphop-abd", "abducted",
                  "legacy-music/hiphop-abducted.mp3",
                  "music/1/library-hiphop-abducted.mp3", "../x", ""):
        t, err = music_library.find(probe)
        assert t is None and err.startswith("REJECTED"), probe
    t, err = music_library.find("upbeat-50-over-the-speed-limit")
    assert t is None and "retired" in err


def test_list_music_library_groups_by_mood_and_marks_repeats():
    ctx = _Ctx()
    ctx.db.used = ["hiphop-abducted"]
    out = music_library.list_music_library(ctx)
    assert "hiphop-abducted" in out and "add_library_music" in out
    assert "upbeat-50-over-the-speed-limit" not in out
    line = next(ln for ln in out.splitlines() if "hiphop-abducted" in ln)
    assert "already used" in line
    only = music_library.list_music_library(ctx, mood="chill")
    assert "chill-bubbles-lofi-bright-relaxed" in only
    assert "hiphop-abducted" not in only
    assert music_library.list_music_library(ctx, mood="techno") \
        .startswith("REJECTED")


def test_pick_is_deterministic_rotates_by_project_and_avoids_repeats():
    a = music_library.pick(("chill",), "7:editorial")
    assert a == music_library.pick(("chill",), "7:editorial")
    assert a["mood"] == "chill"
    seen = {music_library.pick(("chill",), f"{p}:editorial")["slug"]
            for p in range(40)}
    assert len(seen) == 3                       # every chill bed gets used
    fresh = music_library.pick(("chill", "ambient"), "7:x",
                               avoid={t["slug"] for t in
                                      music_library.browse("chill")})
    assert fresh["mood"] == "ambient"           # spills to the next mood
    everything = {t["slug"] for t in music_library.catalog()}
    assert music_library.pick(("chill",), "7:x", avoid=everything)["mood"] \
        == "chill"                              # all used: still a bed


# ── add_library_music ───────────────────────────────────────────────────

def test_add_library_music_copies_once_registers_licence_and_places(store):
    ctx = _Ctx()
    out = music_library.add_library_music(ctx, "chill-canon-event-lofi-sad-reflection")
    assert out.startswith("EDL v1"), out
    assert store.copies == [("legacy-music/chill-canon-event-lofi-sad-reflection.mp3",
                             "music/7/library-chill-canon-event-lofi-sad-reflection.mp3")]
    asset = ctx.db.inserted[0]
    assert asset["kind"] == "music"
    meta = asset["meta"]
    assert meta["license"] == "CC0" and meta["author"] == "HoliznaCC0"
    assert meta["library_slug"] == "chill-canon-event-lofi-sad-reflection"
    assert meta["source_url"].startswith("https://freemusicarchive.org/")
    item = ctx.latest_edl()["json"]["music"][0]
    assert item["storage_key"] == "music/7/library-chill-canon-event-lofi-sad-reflection.mp3"
    assert item["gain_db"] == -18.0 and item["duck"] is True
    assert item["duck_mode"] == "smooth" and item["loop"] is True
    assert "Valmera CC0 library" in item["purpose"]
    assert "CC0" in out and "no attribution" in out
    # A second placement reuses the project copy — no second copy/insert.
    out2 = music_library.add_library_music(
        ctx, "chill-canon-event-lofi-sad-reflection", start=0, end=10,
        gain_db=-21)
    assert out2.startswith("EDL v2"), out2
    assert len(store.copies) == 1 and len(ctx.db.inserted) == 1
    assert ctx.latest_edl()["json"]["music"][1]["gain_db"] == -21.0


def test_missing_storage_object_is_an_honest_no_op(monkeypatch):
    s = _Storage(missing=("legacy-music/hiphop-abducted.mp3",))
    monkeypatch.setattr(music_library.storage, "exists", s.exists)
    monkeypatch.setattr(music_library.storage, "copy_object", s.copy_object)
    ctx = _Ctx()
    out = music_library.add_library_music(ctx, "hiphop-abducted")
    assert out.startswith("UNAVAILABLE") and "Do NOT claim" in out
    assert ctx.writes == [] and ctx.db.inserted == []


def test_unknown_or_retired_slug_never_touches_storage(store):
    ctx = _Ctx()
    for slug in ("hiphop", "upbeat-50-over-the-speed-limit"):
        assert music_library.add_library_music(ctx, slug).startswith("REJECTED")
    assert store.copies == [] and ctx.writes == []


def test_library_tools_are_registered_and_routed():
    for name in ("list_music_library", "add_library_music"):
        assert name in agent_tools.TOOLS
        assert name in agent_tools.TOOL_DOMAINS["audio"]
        assert name in agent_tools.REQUIRED_ARGS
        assert not agent_tools._tool_disabled(name)
    assert agent_tools.REQUIRED_ARGS["add_library_music"] == ["slug"]
    assert "add_library_music" in agent_tools.WRITE_TOOLS
    assert "list_music_library" not in agent_tools.WRITE_TOOLS
    names = {row["function"]["name"] for row in agent_tools.openai_tools()}
    assert {"list_music_library", "add_library_music"} <= names


# ── apply_look: data integrity ──────────────────────────────────────────

def test_every_look_component_is_a_real_renderable_value():
    presets = set(agent_tools.CAPTION_PRESETS)
    gc_fields = set(GradeCustom.model_fields)
    for name, look in agent_tools.LOOKS.items():
        assert "sound_design" not in look, name     # implemented, not dead
        caps = look.get("captions") or {}
        assert caps.get("preset") in presets, name
        if caps.get("motion_look"):
            assert caps["motion_look"] in motion_captions.LOOKS, name
        CaptionStyle(**{k: v for k, v in caps.items() if v is not None})
        assert look.get("grade") in (None, "vibrant", "warm", "cool", "bw",
                                     "vintage", "cinematic"), name
        assert set(look.get("grade_custom") or {}) <= gc_fields, name
        GradeCustom(**(look.get("grade_custom") or {}))
        st = look.get("stylize") or ()
        st = (st,) if st and isinstance(st[0], str) else st
        assert all(k in STYLIZE_KINDS and 0 < i <= 1 for k, i in st), name
        if look.get("transition"):
            assert look["transition"][0] in TRANSITION_STYLES, name
        kinds, _gain = look.get("transition_sfx") or ((), 0)
        assert all(k in sfx_kit.KINDS for k in kinds), name
        assert all(m in music_library.MOODS for m in look.get("music", ())), name
        if look.get("system"):
            assert caps.get("motion_look"), name
            assert -22.0 <= look["music_db"] <= -18.0, name
            assert look["fade_in_s"] == 0.0, name
    assert {"editorial", "creator_punch", "cinematic_doc", "mono_noir",
            "clean_minimal"} <= {n for n, lk in agent_tools.LOOKS.items()
                                 if lk.get("system")}
    schema = agent_tools.TOOLS["apply_look"][2]
    assert set(schema["name"]["enum"]) == set(agent_tools.LOOKS)
    assert "music" in schema


# ── apply_look: classic looks stay compatible ───────────────────────────

def test_classic_hype_on_landscape_is_unchanged_plus_real_sound_design(kit):
    ctx = _Ctx(ratio="16:9")
    out = agent_tools.apply_look(ctx, "hype")
    assert out.startswith("EDL v1"), out
    edl = ctx.latest_edl()["json"]
    st = edl["captions"]["style"]
    assert st["preset"] == "beast" and st["size"] == "xl"
    assert st.get("motion_look") is None
    fx = edl["effects"]
    assert fx["grade"] == "vibrant"
    assert fx["transition"]["style"] == "zoom_punch"
    assert fx["transition"]["duration_s"] == 0.25
    assert fx["fade_out_s"] == 0.6                 # landscape keeps its fade
    assert not edl.get("music")                    # no music unless asked
    # one continuous take: nothing to transition, so no sounds, said so
    assert not edl.get("sfx") and kit == []
    assert "fires only where the footage really changes" in out


def test_classic_looks_never_fade_a_vertical_short_from_black():
    for name in ("cinematic", "luxury", "clean"):
        ctx = _Ctx(ratio="9:16")
        out = agent_tools.apply_look(ctx, name)
        assert out.startswith("EDL v1"), (name, out)
        fx = ctx.latest_edl()["json"]["effects"] or {}
        assert not fx.get("fade_in_s"), name
        assert not fx.get("fade_out_s"), name
        assert "no fade in from black" in out, name
    ctx = _Ctx(ratio="16:9")
    agent_tools.apply_look(ctx, "cinematic")
    fx = ctx.latest_edl()["json"]["effects"]
    assert fx["fade_in_s"] == 1.0 and fx["fade_out_s"] == 1.0


# ── apply_look: premium systems ─────────────────────────────────────────

def test_editorial_sets_a_coherent_system_in_one_version(monkeypatch):
    monkeypatch.setattr(motion_tools.motion_engine, "available", lambda: True)
    ctx = _Ctx(ratio="9:16")
    seeded = json.loads(json.dumps(ctx._edl))
    seeded["effects"] = {"fade_in_s": 1.0,
                         "grade_custom": {"exposure": 0.3, "tint": 0.4}}
    seeded["captions"] = {"mode": "from_transcript", "max_words_per_caption": 8,
                          "style": {"preset": "stacked", "font": "Anton",
                                    "size_scale": 1.6}}
    ctx._edl = validate_edl(seeded, ctx.duration).model_dump()
    out = agent_tools.apply_look(ctx, "editorial")
    assert out.startswith("EDL v1") and len(ctx.writes) == 1, out
    edl = ctx.latest_edl()["json"]
    caps = edl["captions"]
    st = caps["style"]
    assert st["motion_look"] == "serif" and st["preset"] == "editorial"
    assert st["color"] == "#F5F1EA" and st["highlight_color"] == "#F2C94C"
    assert st.get("font") is None and st.get("size_scale") is None
    assert caps.get("max_words_per_caption") is None
    assert caps["emphasis_words"]                  # accent words chosen
    assert motion_captions.look_of(edl) == "serif"
    fx = edl["effects"]
    assert fx["grade"] is None
    gc = {k: v for k, v in fx["grade_custom"].items() if v is not None}
    assert gc == agent_tools.LOOKS["editorial"]["grade_custom"]  # replaced
    kinds = sorted((s["kind"], s["intensity"]) for s in fx["stylize"])
    assert kinds == [("grain", 0.18), ("vignette", 0.25)]
    assert fx["transition"]["style"] == "dip_white"
    assert fx["transition"]["scope"] == "scene"
    assert not fx.get("fade_in_s")                 # vertical: opens on picture
    assert "fade in cleared" in out
    assert "HERO MOMENTS" in out and "hook_title" in out
    assert '"treatment": "serif"' in out and '"accent": "#F2C94C"' in out
    assert "music='auto'" in out                   # offers the bed


def test_switching_systems_replaces_texture_grade_and_owned_sounds(kit):
    ctx = _Ctx(ratio="9:16", duration=40.0,
               edl=_broll_edl(40.0, [10.0, 22.0]))
    out = agent_tools.apply_look(ctx, "creator_punch")
    assert out.startswith("EDL v1"), out
    edl = ctx.latest_edl()["json"]
    tx = [s for s in edl["sfx"] if s["id"].startswith("look_tx")]
    # 2 inserts -> 4 junctions (in/out of each B-roll), each sounded
    assert len(tx) == 4
    assert {s["storage_key"].split("kit-")[1][:-4] for s in tx} == \
        {"whoosh_hard", "swipe"}
    # the whoosh PEAKS on the cut: placed before the junction, not on it
    junction_times = [10.0, 12.0, 24.0, 26.0]     # inserts shift later cuts
    for s, t in zip(sorted(tx, key=lambda s: s["at"]), junction_times):
        assert t - 0.4 < s["at"] < t, (s, t)
        assert s["purpose"]
    user_sound = {"id": "sx1", "storage_key": "sfx/7/kit-pop_soft.wav",
                  "at": 5.0, "gain_db": -8.0, "purpose": "user pop"}
    edl2 = json.loads(json.dumps(edl))
    edl2["sfx"].append(user_sound)
    ctx._edl = validate_edl(edl2, ctx.duration).model_dump()
    out = agent_tools.apply_look(ctx, "mono_noir")
    assert out.startswith("EDL v2"), out
    edl = ctx.latest_edl()["json"]
    fx = edl["effects"]
    assert fx["grade"] == "bw"
    gc = {k: v for k, v in fx["grade_custom"].items() if v is not None}
    assert gc == agent_tools.LOOKS["mono_noir"]["grade_custom"]
    assert sorted(s["kind"] for s in fx["stylize"]) == ["grain", "vignette"]
    assert [s["intensity"] for s in fx["stylize"] if s["kind"] == "grain"] \
        == [0.32]
    assert edl["captions"]["style"]["motion_look"] == "stack"
    assert edl["captions"]["style"]["highlight_color"] == "#ED080D"
    tx = [s for s in edl["sfx"] if s["id"].startswith("look_tx")]
    assert len(tx) == 4 and any("glitch" in s["storage_key"] for s in tx)
    assert any(s["id"] == "sx1" for s in edl["sfx"])   # user sound kept
    # clean_minimal = hard cuts: transition AND its sounds go
    out = agent_tools.apply_look(ctx, "clean_minimal")
    edl = ctx.latest_edl()["json"]
    assert edl["effects"].get("transition") is None
    assert [s["id"] for s in edl["sfx"]] == ["sx1"]
    assert not edl["effects"].get("stylize")
    assert "transitions cleared" in out


def test_dense_scene_changes_get_an_evenly_spaced_subset(kit):
    cuts = [4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.0]
    ctx = _Ctx(ratio="9:16", duration=40.0, edl=_broll_edl(40.0, cuts, 1.0))
    out = agent_tools.apply_look(ctx, "creator_punch")
    assert out.startswith("EDL v1"), out
    tr = ctx.latest_edl()["json"]["effects"]["transition"]
    assert tr["junctions"] and len(tr["junctions"]) <= 48 // 5
    tx = [s for s in ctx.latest_edl()["json"]["sfx"]
          if s["id"].startswith("look_tx")]
    assert len(tx) == len(tr["junctions"])
    assert "evenly spaced" in out


def test_music_auto_lays_a_ducked_library_bed_in_the_same_version(store):
    ctx = _Ctx(ratio="9:16")
    out = agent_tools.apply_look(ctx, "editorial", music="auto")
    assert out.startswith("EDL v1") and len(ctx.writes) == 1, out
    edl = ctx.latest_edl()["json"]
    m = edl["music"][0]
    assert m["storage_key"].startswith("music/7/library-")
    slug = m["storage_key"][len("music/7/library-"):-4]
    assert music_library.find(slug)[0]["mood"] in ("chill", "ambient",
                                                   "inspiring")
    assert m["gain_db"] == -18.0 and m["duck"] and m["duck_mode"] == "smooth"
    assert "'editorial' look" in m["purpose"]
    assert "CC0" in out
    # a second application never stacks a second bed
    out = agent_tools.apply_look(ctx, "creator_punch", music="auto")
    assert len(ctx.latest_edl()["json"]["music"]) == 1
    assert "music left as is" in out


def test_music_choice_by_mood_slug_and_lead_without_speech(store):
    ctx = _Ctx(ratio="9:16", speech=False)
    out = agent_tools.apply_look(ctx, "mono_noir", music="hiphop-dance-of-the-dead")
    assert out.startswith("EDL v1"), out
    m = ctx.latest_edl()["json"]["music"][0]
    assert m["storage_key"] == "music/7/library-hiphop-dance-of-the-dead.mp3"
    assert m["gain_db"] == -4.0                    # no speech: music leads
    ctx = _Ctx(ratio="9:16")
    agent_tools.apply_look(ctx, "clean_minimal", music="cinematic")
    m = ctx.latest_edl()["json"]["music"][0]
    assert "library-cinematic-" in m["storage_key"] and m["gain_db"] == -20.0


def test_bad_music_choice_is_rejected_before_anything_changes(store):
    ctx = _Ctx()
    out = agent_tools.apply_look(ctx, "editorial", music="techno")
    assert out.startswith("REJECTED") and "auto" in out
    assert ctx.writes == [] and store.copies == []
    out = agent_tools.apply_look(ctx, "editorial", music="none")
    assert out.startswith("EDL v1") and not ctx.latest_edl()["json"]["music"]


def test_missing_library_object_still_applies_the_look(monkeypatch):
    s = _Storage(missing=[r["storage_key"] for r in music_library.entries()])
    monkeypatch.setattr(music_library.storage, "exists", s.exists)
    monkeypatch.setattr(music_library.storage, "copy_object", s.copy_object)
    ctx = _Ctx()
    out = agent_tools.apply_look(ctx, "cinematic_doc", music="auto")
    assert out.startswith("EDL v1"), out
    assert not ctx.latest_edl()["json"]["music"]
    assert "music NOT added" in out and "UNAVAILABLE" in out


def _broll_edl(duration, cuts, ins_dur=2.0):
    """Keep split at each cut with a still insert spliced in there."""
    points = [0.0] + list(cuts) + [duration]
    keep = [[points[i], points[i + 1]] for i in range(len(points) - 1)]
    edl = default_edl(duration)
    edl["keep"] = keep
    edl["inserts"] = [{"id": f"ins{i + 1}", "asset_key": f"img/7/{i}.png",
                       "kind": "image", "at_output_s": c, "duration_s": ins_dur}
                      for i, c in enumerate(cuts)]
    return edl
