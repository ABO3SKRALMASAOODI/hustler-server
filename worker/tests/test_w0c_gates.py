"""Owner gates in code (track 0C; AT-0C-4; INTERFACES.md §4.3, §6.1).

* Agents never choose music: every new music item carries who chose it
  (user_upload / user_named / user_picked_library); a library track is
  placed only on the user's pick, which only ask_user's offer (or the user
  naming the track) produces; legacy items still render.
* Sound effects come only from the approved library: the web-SFX chain is
  off and out of the live catalog; a missing sound becomes a sound request.
* RESTRAINT_RULE and SOUND_RULE are pinned word for word.
"""

import hashlib
import json
import os
import shutil
import subprocess

import pytest

from w0c_support import tool_context, upload_asset

import agent_prompt  # noqa: E402
import agent_tools  # noqa: E402
import config  # noqa: E402
import music_gate  # noqa: E402
import music_library  # noqa: E402
import sfx_search  # noqa: E402
import sound_requests  # noqa: E402
import tools_ext  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402

WEB_SFX = ("search_sfx", "audition_sfx_candidates", "fetch_sfx",
           "add_web_sfx")
LIB_KEY = "music/7/library-chill-bubbles-lofi-bright-relaxed.mp3"


def _music(key, mid="mus1", **extra):
    return {"id": mid, "storage_key": key, "start": 0.0, "end": 20.0,
            "gain_db": -18.0, "duck": True, **extra}


def _with_music(*items):
    edl = default_edl(20.0)
    edl["music"] = list(items)
    return edl


@pytest.fixture
def store(monkeypatch):
    objects = {r["storage_key"] for r in music_library.entries()}
    copies = []

    def copy_object(src, dst):
        copies.append((src, dst))
        objects.add(dst)
    monkeypatch.setattr(music_library.storage, "exists",
                        lambda k: k in objects)
    monkeypatch.setattr(music_library.storage, "copy_object", copy_object)
    return copies


# ── AT-0C-4a: a MusicItem without provenance fails validation ───────────

def test_a_new_music_item_without_provenance_fails_validation():
    prev = default_edl(20.0)
    new = _with_music(_music("music/7/song.mp3"))
    with pytest.raises(music_gate.MusicGateError, match="no provenance"):
        music_gate.validate_new_items(prev, new)
    new["music"][0]["provenance"] = "user_upload"
    music_gate.validate_new_items(prev, new)
    new["music"][0]["provenance"] = "legacy"     # legacy is never NEW
    with pytest.raises(music_gate.MusicGateError):
        music_gate.validate_new_items(prev, new)
    # A track already in the edit (gain/fit/mute/split) is not new.
    music_gate.validate_new_items(_with_music(_music("music/7/song.mp3")),
                                  _with_music(_music("music/7/song.mp3",
                                                     gain_db=-12.0)))


def test_the_write_gate_stamps_uploads_and_refuses_unpicked_library():
    ctx, fake = tool_context(default_edl(20.0))
    upload_asset(fake, "music/7/my-song.mp3", filename="my song.mp3")
    out = ctx.write_edl(_with_music(_music("music/7/my-song.mp3")), "music")
    assert out.startswith("EDL v7 -> v8"), out
    assert fake.rows[-1]["json"]["music"][0]["provenance"] == "user_upload"
    # A claimed provenance never overrides the asset's truth.
    upload_asset(fake, LIB_KEY, source="valmera-music-library",
                 library_slug="chill-bubbles-lofi-bright-relaxed")
    edl = _with_music(_music("music/7/my-song.mp3",
                             provenance="user_upload"),
                      _music(LIB_KEY, mid="mus2", provenance="user_upload"))
    out = ctx.write_edl(edl, "library bed")
    assert out.startswith("REJECTED (EDL v8 unchanged): music gate"), out
    assert "agents never choose music" in out.lower()
    assert len(fake.rows) == 2


def test_a_track_already_in_the_edit_keeps_its_provenance():
    legacy = _music("legacy-music/hiphop-abducted.mp3")   # pre-gate item
    ctx, fake = tool_context(_with_music(legacy))
    louder = _with_music(dict(legacy, gain_db=-10.0))
    out = ctx.write_edl(louder, "louder")
    assert out.startswith("EDL v7 -> v8"), out
    stored = fake.rows[-1]["json"]["music"][0]
    assert stored["provenance"] is None          # still legacy, no rewrite
    assert music_gate.provenance_of(stored) == "legacy"
    # a split piece of the same track is not a new choice either
    split = _with_music(dict(legacy, end=8.0),
                        dict(legacy, id="mus2", start=12.0, end=20.0))
    assert ctx.write_edl(split, "split").startswith("EDL v8 -> v9")
    # a forged provenance on an existing track is put back
    forged = _with_music(dict(legacy, provenance="user_picked_library",
                              end=8.0))
    ctx.write_edl(forged, "forged")
    assert fake.rows[-1]["json"]["music"][0]["provenance"] is None


def test_restoring_a_track_from_the_edit_history_is_not_a_new_choice():
    ctx, fake = tool_context(default_edl(20.0))
    upload_asset(fake, LIB_KEY, source="valmera-music-library",
                 library_slug="chill-bubbles-lofi-bright-relaxed")
    edl = _with_music(_music(LIB_KEY))
    assert ctx.write_edl(edl, "re-add").startswith("REJECTED")
    fake.history_keys.add(LIB_KEY)                 # it played in v3
    fake.rows[0]["json"]["music"] = []
    fake.rows.insert(0, {"version": 3, "json": validate_edl(
        edl, 20.0).model_dump()})
    out = ctx.write_edl(edl, "undo the removal")
    assert out.startswith("EDL v7 -> v8"), out


def test_a_gate_that_cannot_decide_refuses_without_a_traceback(monkeypatch):
    """A failure inside the provenance decision (an unreadable library, a
    database hiccup the helpers did not absorb) refuses only the new track:
    the write returns a REJECTED line, never raises, never passes."""
    ctx, fake = tool_context(default_edl(20.0))
    upload_asset(fake, "music/7/my-song.mp3")

    def boom(*_a, **_k):
        raise RuntimeError("library manifest unreadable")
    monkeypatch.setattr(music_gate, "classify_asset", boom)
    out = ctx.write_edl(_with_music(_music("music/7/my-song.mp3")), "music")
    assert out.startswith("REJECTED (EDL v7 unchanged): music gate"), out
    assert "could not be verified" in out and len(fake.rows) == 1


def test_the_shared_gate_core_loads_without_the_worker_db():
    """backend/routes/video.py loads music_gate by path for the MCP batch
    gate: it must import nothing worker-only at module level."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("worker_music_gate_probe",
                                                  music_gate.__file__)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.decide(LIB_KEY, None, is_picked=lambda s: False,
                      seen_before=lambda k: False)[1] == mod.REFUSE_PICK
    assert mod.decide("music/7/song.mp3", {"kind": "music", "meta": {}},
                      is_picked=lambda s: False,
                      seen_before=lambda k: False) == ("user_upload", None)
    src = open(music_gate.__file__, encoding="utf-8").read()
    assert "\nimport db" not in src


def test_agent_searched_web_music_and_sound_effects_are_refused():
    ctx, fake = tool_context(default_edl(20.0))
    upload_asset(fake, "music/7/found.mp3",
                 caption="found online by search_music")
    upload_asset(fake, "sfx/7/boom.mp3", caption="found online by search_sfx")
    out = ctx.write_edl(_with_music(_music("music/7/found.mp3")), "x")
    assert out.startswith("REJECTED") and "web search" in out
    out = ctx.write_edl(_with_music(_music("sfx/7/boom.mp3")), "x")
    assert out.startswith("REJECTED") and "add_sfx" in out


def test_named_songs_and_extracted_clip_audio_are_the_users():
    assert music_gate.classify_asset(
        {"kind": "music", "meta": {"fetched": True}}) == "user_named"
    assert music_gate.classify_asset(
        {"kind": "music", "meta": {"music_provenance": "user_named"}}) == \
        "user_named"
    assert music_gate.classify_asset(
        {"kind": "music", "meta": {"extracted_from_video": True}}) == \
        "user_upload"
    assert music_gate.classify_asset(
        {"kind": "music", "meta": {"filename": "x.mp3"}}) == "user_upload"
    assert music_gate.classify_asset(None, "legacy-music/x.mp3") == "library"
    assert music_gate.classify_asset(
        {"kind": "music", "meta": {"source": "valmera-sound-library"}}) == "sfx"


def test_the_sound_of_a_clip_an_agent_found_is_not_the_users():
    """Extracted audio is the user's only when the video was: the
    soundtrack of a stock or AI-generated clip the agent brought in is the
    agent choosing music."""
    ctx, fake = tool_context(default_edl(20.0))
    fake.assets["clips/7/stock.mp4"] = {
        "kind": "video_clip", "storage_key": "clips/7/stock.mp4",
        "meta": {"stock": True, "provider": "pexels"}}
    fake.assets["clips/7/mine.mp4"] = {
        "kind": "video_clip", "storage_key": "clips/7/mine.mp4",
        "meta": {"filename": "my clip.mp4"}}
    upload_asset(fake, "music/7/aaa.m4a", extracted_from_video=True,
                 from_asset_key="clips/7/stock.mp4")
    upload_asset(fake, "music/7/bbb.m4a", extracted_from_video=True,
                 from_asset_key="clips/7/mine.mp4")
    out = ctx.write_edl(_with_music(_music("music/7/aaa.m4a")), "bed")
    assert out.startswith("REJECTED") and "agent found" in out, out
    out = ctx.write_edl(_with_music(_music("music/7/bbb.m4a")), "bed")
    assert out.startswith("EDL v7 -> v8"), out
    assert fake.rows[-1]["json"]["music"][0]["provenance"] == "user_upload"


def test_find_song_and_fetch_url_record_user_named(monkeypatch, tmp_path):
    ctx, fake = tool_context(default_edl(20.0))
    music_gate.note_named_song(ctx, "Blinding Lights The Weeknd",
                               [{"url": "https://soundcloud.com/x/y"}])
    assert music_gate.named_song_for(ctx, "https://soundcloud.com/x/y") == \
        "Blinding Lights The Weeknd"
    # fetch_url stamps what it downloads as music
    src = tmp_path / "dl.mp3"
    src.write_bytes(b"ID3")
    monkeypatch.setattr(config, "URL_FETCH_ENABLED", True)
    monkeypatch.setattr(agent_tools, "_execution_policy", lambda c: "legacy")
    monkeypatch.setattr(agent_tools.url_media, "fetch", lambda url, wd,
                        prefer=None: {"kind": "music", "path": str(src),
                                      "filename": "Blinding Lights.mp3",
                                      "source_url": url, "duration_s": 200.0,
                                      "bytes": 3})
    monkeypatch.setattr(agent_tools.storage, "upload_file",
                        lambda *a, **k: None)
    monkeypatch.setattr(agent_tools, "_queue_download_review",
                        lambda *a, **k: 0)
    out = agent_tools.fetch_url(ctx, "https://soundcloud.com/x/y",
                                as_kind="music")
    assert "storage_key=" in out, out
    meta = next(a["meta"] for a in fake.assets.values())
    assert meta["music_provenance"] == "user_named"
    assert meta["named_song"] == "Blinding Lights The Weeknd"
    key = next(iter(fake.assets))
    assert music_gate.provenance_for(ctx, key)[0] == "user_named"


# ── AT-0C-4b: add_library_music without a user pick is refused ──────────

def test_add_library_music_without_a_pick_is_refused(store):
    for words in ("add some background music", "you choose a track",
                  "something chill please", ""):
        ctx, fake = tool_context(default_edl(20.0), user_message=words)
        out = music_library.add_library_music(
            ctx, "chill-bubbles-lofi-bright-relaxed")
        assert out.startswith("REJECTED") and "ask_user" in out, (words, out)
        assert store == [] and len(fake.rows) == 1


def test_the_users_pick_from_the_ask_user_offer_places_it(store):
    ctx, fake = tool_context(default_edl(20.0),
                             user_message="add some background music",
                             job_id=9)
    q = ("Which background track would you like? 1) Bubbles (chill) "
         "2) After midnight (cinematic) 3) Abducted (hiphop)")
    with pytest.raises(agent_tools.AskUser) as asked:
        agent_tools.ask_user(ctx, q)
    assert asked.value.question == q          # the user sees it unchanged
    # Same turn: the agent cannot pick for them.
    out = music_library.add_library_music(ctx, "cinematic-after-midnight")
    assert out.startswith("REJECTED")
    # Next turn: the user's reply is the pick.
    ctx2, fake2 = tool_context(default_edl(20.0),
                               user_message="the second one", job_id=10)
    fake2.kv = fake.kv                          # same project app_kv
    out = music_library.add_library_music(ctx2, "chill-bubbles-lofi-bright-relaxed")
    assert out.startswith("REJECTED") and "picked 'cinematic-after-midnight'" \
        in out
    out = music_library.add_library_music(ctx2, "cinematic-after-midnight")
    assert out.startswith("EDL v7 -> v8"), out
    item = fake2.rows[-1]["json"]["music"][0]
    assert item["provenance"] == "user_picked_library"
    assert item["storage_key"] == \
        "music/7/library-cinematic-after-midnight.mp3"
    picks = json.loads(fake2.kv["music_picks:7"])
    assert picks["cinematic-after-midnight"]["evidence"] == "the second one"


def test_a_user_who_names_a_library_track_has_picked_it(store):
    ctx, fake = tool_context(default_edl(20.0),
                             user_message="put Calm Currents under it")
    out = music_library.add_library_music(
        ctx, "chill-calm-currents-lofi-relax-calm")
    assert out.startswith("EDL v7 -> v8"), out
    assert fake.rows[-1]["json"]["music"][0]["provenance"] == \
        "user_picked_library"


def test_over_mcp_the_pick_token_comes_back_with_the_users_words(store):
    ctx, fake = tool_context(default_edl(20.0), mcp=True)
    q = "Pick a bed: Bubbles, After midnight or Abducted?"
    with pytest.raises(agent_tools.AskUser) as asked:
        agent_tools.ask_user(ctx, q)
    token = json.loads(fake.kv["music_pick_offer:7"])["token"]
    assert token in asked.value.question         # the MCP model is told
    out = music_library.add_library_music(ctx, "hiphop-abducted",
                                          user_pick="the third one")
    assert out.startswith("REJECTED") and "token" in out
    out = music_library.add_library_music(
        ctx, "hiphop-abducted", user_pick=f"{token} the third one")
    assert out.startswith("EDL v7 -> v8"), out
    assert fake.rows[-1]["json"]["music"][0]["provenance"] == \
        "user_picked_library"


def test_apply_look_never_picks_music():
    ctx, fake = tool_context(default_edl(20.0),
                             user_message="add a look and some music")
    for value in ("auto", "chill", "cinematic"):
        out = agent_tools.apply_look(ctx, "editorial", music=value)
        assert out.startswith("REJECTED") and "never" in out, value
    assert len(fake.rows) == 1
    out = agent_tools.apply_look(ctx, "editorial",
                                 music="chill-bubbles-lofi-bright-relaxed")
    assert out.startswith("REJECTED")              # nobody picked it


def test_music_tools_say_the_agent_never_picks():
    desc = agent_tools.TOOLS["add_library_music"][1]
    assert "never choose music" in desc and "ask_user" in desc
    props = agent_tools.TOOLS["add_library_music"][2]
    assert "user_pick" in props
    look = agent_tools.TOOLS["apply_look"][2]["music"]["description"]
    assert "auto" not in look and "user picked" in look
    assert "background music always does" in agent_tools.TOOLS["ask_user"][1]
    state = agent_prompt.project_state_block("v", "i", "e", [], [])
    assert "never pick for them" in state


# ── AT-0C-4c: web-SFX is refused while the flag is off ──────────────────

def test_web_sfx_is_off_and_out_of_the_live_catalog():
    assert config.WEB_SFX_ENABLED is False
    assert sfx_search.available() is False
    names = [t["function"]["name"] for t in agent_tools.openai_tools()]
    for name in WEB_SFX:
        assert agent_tools._tool_disabled(name), name
        assert name not in names, name
    assert not set(WEB_SFX) & set(agent_tools.capability_names())
    assert "search_sfx" not in agent_tools.capability_directory()


def test_web_sfx_calls_refuse_and_file_a_sound_request():
    ctx, fake = tool_context(default_edl(20.0))
    out = agent_tools.add_web_sfx(ctx, "crowd cheer", at=3.0)
    assert out.startswith("REJECTED") and "approved library" in out
    rows = json.loads(fake.kv["sound_requests"])
    assert len(rows) == 1
    row = next(iter(rows.values()))
    assert row["description"] == "crowd cheer" and row["status"] == "open"
    assert row["at"] == 3.0 and row["projects"] == [7]
    assert row["id"] in out
    for fn, args in ((agent_tools.search_sfx, ("crowd cheer",)),
                     (agent_tools.fetch_sfx, ("openverse:x",)),
                     (agent_tools.audition_sfx_candidates,
                      (["openverse:x"], "cheer"))):
        assert fn(ctx, *args).startswith("REJECTED"), fn.__name__
    rows = json.loads(fake.kv["sound_requests"])
    assert next(iter(rows.values()))["count"] == 2     # the same request


def test_a_missing_sound_becomes_a_sound_request_not_a_web_sound():
    import motion_tools
    ctx, fake = tool_context(default_edl(20.0))
    out = motion_tools.list_sound_library(ctx, role="crowd cheer")
    assert "No approved sound matches" in out and "sr_" in out
    assert "filed it for the owner" in out
    assert sound_requests.list_requests(fake)[0]["description"] == \
        "crowd cheer"
    # a sound the library has is found, never filed
    out = motion_tools.list_sound_library(ctx, role="woosh")
    assert "sound:whoosh" in out
    assert len(sound_requests.list_requests(fake)) == 1
    assert sound_requests.set_status(fake, sound_requests.request_id(
        "crowd cheer"), "approved", "added as crowd_cheer_1")
    assert sound_requests.list_requests(fake) == []
    assert sound_requests.list_requests(fake, status="approved")


def test_no_live_guidance_offers_a_web_sound():
    surfaces = {"core prompt": agent_prompt.CORE_PROMPT,
                "state block": agent_prompt.project_state_block(
                    "v", "i", "e", [], [])}
    for name in ("audio", "generate-fetch", "music"):
        surfaces["skill " + name] = agent_prompt.read_skill_text(name)
    root = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), os.pardir)
    plugin = os.path.join(root, "plugins", "valmera", "skills",
                          "valmera-podcast-shorts", "references")
    for rel in ("editing.md", "looks.md"):
        with open(os.path.join(plugin, rel), encoding="utf-8") as f:
            surfaces["plugin " + rel] = f.read()
    with open(os.path.join(root, "backend", "routes", "mcp.py"),
              encoding="utf-8") as f:
        text = f.read()
    surfaces["mcp workflow"] = text[text.index('WORKFLOW = """'):
                                    text.index("INSTRUCTIONS_MODE")]
    for where, text in surfaces.items():
        for name in WEB_SFX:
            assert name not in text, (where, name)
    for name, contract in agent_tools._COMPACT_CONTRACTS.items():
        for tool in WEB_SFX:
            assert tool not in contract, (name, tool)
    for name in tools_ext.core_names(agent_tools.TOOLS):
        if name in WEB_SFX or agent_tools._tool_disabled(name):
            continue
        for tool in WEB_SFX:
            assert tool not in agent_tools.TOOLS[name][1], (name, tool)


def test_the_concierge_mirror_follows_the_owner_flag():
    root = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), os.pardir)
    with open(os.path.join(root, "backend", "routes", "video.py"),
              encoding="utf-8") as f:
        src = f.read()
    body = src[src.index("def _sfx_search_enabled"):]
    body = body[:body.index("\ndef ")]
    assert 'os.getenv("WEB_SFX_ENABLED", "0") == "1"' in body


def test_web_sfx_removal_frees_the_core_compact_catalog():
    core = tools_ext.core_names(agent_tools.TOOLS)
    size = len(json.dumps(agent_tools.openai_tools(
        "any-model", compact=True, names=core), separators=(",", ":")))
    # 82,941 bytes at the Wave 0 base commit; turning web-SFX off frees
    # ≈1,237 bytes for the departments Wave 1 registers. Wave 1 spends part
    # of it on the growth INTERFACES_W1.md §4.3 pins (the fx layer +5, the
    # compositing / rhythm / verify department names +28/+18/+18, 1F's
    # look_at overlay and apply_edit_batch line <= 250): those bytes are
    # the saving being used, not the saving lost. Re-pinned by 1F; round
    # lead sign-off requested.
    w1_pinned_growth = 5 + 28 + 18 + 18 + 250
    assert size <= 82_941 - 1_200 + w1_pinned_growth, size


# ── restraint rules are pinned word for word ────────────────────────────

def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_restraint_and_sound_rules_are_pinned():
    assert _sha(agent_prompt.RESTRAINT_RULE) == PINNED["RESTRAINT_RULE"]
    assert _sha(agent_prompt.SOUND_RULE) == PINNED["SOUND_RULE"]
    assert agent_prompt.RESTRAINT_RULE in agent_prompt.CORE_PROMPT
    assert agent_prompt.SOUND_RULE in agent_prompt.CORE_PROMPT


# sha256 of the owner's exact wording (Oct 10 2026). A change here is an
# owner decision, never an edit to make a test pass.
PINNED = {
    "RESTRAINT_RULE": "c0d2efe09d4114459aa18aea9004ccc031ce56778bfd59cc77741d996f8d9683",
    "SOUND_RULE": "dd4149fccd842c3f5a3053dfeafc09d8ea49c0844792f6d080fae7004a358437",
}


# ── legacy music still renders ──────────────────────────────────────────

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_legacy_music_without_provenance_still_renders(tmp_path):
    import renderer
    src = str(tmp_path / "src.mp4")
    song = str(tmp_path / "song.m4a")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=320x180:r=30:d=4", "-f", "lavfi", "-i",
                    "sine=frequency=330:sample_rate=48000:duration=4",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", src],
                   check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=550:sample_rate=48000:duration=6",
                    "-c:a", "aac", song], check=True, capture_output=True)
    edl = default_edl(4.0)
    edl["music"] = [{"id": "mus1", "storage_key": "legacy-music/x.mp3",
                     "start": 0.0, "end": 4.0, "gain_db": -6.0,
                     "duck": False}]
    norm = validate_edl(edl, 4.0).model_dump()
    assert norm["music"][0]["provenance"] is None
    out = str(tmp_path / "out.mp4")
    dur = renderer.render_edl(
        norm, {"words": [], "video": {"duration": 4.0}}, src, out,
        str(tmp_path), True, suppress_outro=True,
        asset_locals={"legacy-music/x.mp3": song})
    assert dur == pytest.approx(4.0, abs=0.1)
    probe = renderer.media.probe(out)
    assert probe.get("has_audio", True)


def test_over_mcp_apply_look_points_the_pick_at_add_library_music(store):
    ctx, fake = tool_context(default_edl(20.0), mcp=True)
    out = agent_tools.apply_look(ctx, "editorial",
                                 music="chill-bubbles-lofi-bright-relaxed")
    assert out.startswith("REJECTED"), out
    assert "add_library_music(slug='chill-bubbles-lofi-bright-relaxed', " \
        "user_pick=" in out
    assert "Over MCP pass user_pick" not in out   # apply_look has none
    assert len(fake.rows) == 1
