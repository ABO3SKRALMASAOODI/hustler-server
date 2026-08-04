"""Round 85 — the Russian reply, and the library the agent may not raid.

thevalmera@gmail.com, project 351 / session 456, Aug 4 2026. A silent 19s
576x1024 reel; the user's ONE message was English ("Make a nice edit for a
tiktok reel"); twenty tool steps later the reply arrived in Russian. The
system prompt's language rule (round 74's fix) was present and ignored — it
sat 9k tokens from the moment of writing, under a context full of foreign
on-screen text. So the anchor now travels WITH the request (a script note
appended to the user message) and a deterministic cross-script check rewrites
a flipped reply once (_enforce_reply_language), fail-open.

Same round: "50 Over The Speed Limit" (the model's pick that session) is
retired from the music library, and the whole synthesized SFX pack is retired
— retired means never listed, never advertised, never addable again, while
every EDL that already references the asset keeps rendering forever.
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_loop                                            # noqa: E402
import agent_prompt                                          # noqa: E402
import agent_tools                                           # noqa: E402
import llm                                                   # noqa: E402
import music_library                                         # noqa: E402
import sfx_library                                           # noqa: E402


# ── script detection: measurements, not guesses ─────────────────────────

def test_dominant_script_reads_the_obvious_cases():
    assert agent_loop._dominant_script(
        "Make a nice edit for a tiktok reel") == "Latin"
    assert agent_loop._dominant_script(
        "Создан TikTok-рилс на 19 секунд с тёплой цветокоррекцией"
    ) == "Cyrillic"
    assert agent_loop._dominant_script("اجعل الفيديو أقصر من فضلك") == "Arabic"


def test_too_little_text_means_no_verdict():
    # An emoji or a two-letter "ok" must never anchor a language.
    assert agent_loop._dominant_script("👍") is None
    assert agent_loop._dominant_script("ok") is None
    assert agent_loop._dominant_script("") is None
    assert agent_loop._dominant_script(None) is None


def test_heavy_mix_returns_none_rather_than_a_coin_flip():
    assert agent_loop._dominant_script("привет hello привет hello") is None


# ── the anchor note travels with the user's message ──────────────────────

def test_language_note_names_the_script_and_bans_footage_text():
    note = agent_loop._reply_language_note(
        ["Make a nice edit for a tiktok reel"])
    assert "Latin script" in note
    assert "NEVER" in note and "footage" in note


def test_language_note_is_silent_when_there_is_nothing_to_measure():
    assert agent_loop._reply_language_note(["👍"]) == ""
    assert agent_loop._reply_language_note([]) == ""


# ── the reply-time check: session 456 replayed ───────────────────────────

_EN_USER = "Make a nice edit for a tiktok reel"
_RU_REPLY = ("Создан TikTok-рилс на 19,03 секунды: тёплая цветокоррекция, "
             "энергичный трек, два мягких зума и нормализация громкости.")
_EN_REPLY = ("19s TikTok reel: warm grade, an upbeat track, two soft "
             "zooms and social loudness. The fade-out is gone so it loops.")


def _ctx():
    return types.SimpleNamespace(job={"id": 1}, agent_model=None)


def _fake_resp(text):
    msg = types.SimpleNamespace(content=text)
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=msg)], usage=None)


def test_the_russian_reply_is_rewritten_into_the_users_language(monkeypatch):
    calls = {}

    def fake_create(client, model, messages, **kw):
        calls["prompt"] = messages[-1]["content"]
        return _fake_resp(_EN_REPLY)

    monkeypatch.setattr(llm, "create_with_dialect", fake_create)
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    honesty = {}
    out = agent_loop._enforce_reply_language(
        _ctx(), None, [], [], _RU_REPLY, _EN_USER, honesty)
    assert out == _EN_REPLY
    assert honesty["language_flip"] == "Latin->Cyrillic"
    assert honesty["language_fixed"] is True
    assert "Cyrillic" in calls["prompt"]      # the correction names the flip


def test_a_matching_reply_is_never_touched(monkeypatch):
    def boom(*a, **kw):                        # any LLM call here is a bug
        raise AssertionError("no rewrite call belongs on a clean reply")
    monkeypatch.setattr(llm, "create_with_dialect", boom)
    honesty = {}
    out = agent_loop._enforce_reply_language(
        _ctx(), None, [], [], _EN_REPLY, _EN_USER, honesty)
    assert out == _EN_REPLY and honesty == {}
    # Same-script pair the other way round: a Russian user answered in Russian.
    out = agent_loop._enforce_reply_language(
        _ctx(), None, [], [], _RU_REPLY,
        "Сделай красивый монтаж для тиктока", honesty)
    assert out == _RU_REPLY and honesty == {}


def test_a_script_the_user_quoted_is_their_call(monkeypatch):
    monkeypatch.setattr(llm, "create_with_dialect",
                        lambda *a, **kw: _fake_resp(_EN_REPLY))
    honesty = {}
    user = 'Add a caption that says "Привет друзья" at the start'
    out = agent_loop._enforce_reply_language(
        _ctx(), None, [], [], _RU_REPLY, user, honesty)
    assert out == _RU_REPLY and honesty == {}


def test_a_failed_rewrite_fails_open(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("provider down")
    monkeypatch.setattr(llm, "create_with_dialect", boom)
    honesty = {}
    out = agent_loop._enforce_reply_language(
        _ctx(), None, [], [], _RU_REPLY, _EN_USER, honesty)
    assert out == _RU_REPLY                    # posted as-is, never crashed
    assert honesty["language_flip"] == "Latin->Cyrillic"
    assert "language_fixed" not in honesty


def test_a_rewrite_that_still_flips_is_discarded(monkeypatch):
    monkeypatch.setattr(llm, "create_with_dialect",
                        lambda *a, **kw: _fake_resp(_RU_REPLY))
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    out = agent_loop._enforce_reply_language(
        _ctx(), None, [], [], _RU_REPLY, _EN_USER, {})
    assert out == _RU_REPLY                    # original, not a second flip


# ── the retired track: gone from the shop, alive in the archive ──────────

_BAD = "library:upbeat-50-over-the-speed-limit"


def test_retired_track_is_not_offered_anywhere():
    slugs = {t["slug"] for t in music_library.CATALOG}
    assert "upbeat-50-over-the-speed-limit" not in slugs
    assert all(not t.get("retired") for t in music_library.CATALOG)
    assert not any(t["slug"] == "upbeat-50-over-the-speed-limit"
                   for t in music_library.browse("upbeat"))


def test_retired_track_still_resolves_for_the_renderer():
    t = music_library.resolve(_BAD)
    assert t and t.get("retired") is True
    path = music_library.local_path(_BAD)
    assert path and os.path.exists(path)       # old EDLs keep rendering


def test_add_music_refuses_the_retired_track():
    track, err = agent_tools._resolve_music(None, _BAD)
    assert track is None and "retired" in err
    # A living sibling still resolves normally.
    ok, err2 = agent_tools._resolve_music(
        None, "library:upbeat-a-small-town-on-pluto")
    assert err2 is None and ok["library"] is True


# ── the SFX pack: fully retired, nothing breaks ──────────────────────────

def test_sfx_catalog_is_empty_but_every_sound_still_resolves():
    assert sfx_library.CATALOG == []
    assert len(sfx_library._LIB.entries) >= 18   # shipped files still known
    s = sfx_library.resolve("sfx:whoosh")
    assert s and s.get("retired") is True
    path = sfx_library.local_path("sfx:whoosh")
    assert path and os.path.exists(path)


def test_add_sfx_refuses_retired_sounds_and_points_at_generation():
    sound, err = agent_tools._resolve_sfx(None, "sfx:whoosh")
    assert sound is None and "retired" in err and "generate_sfx" in err


def test_empty_pack_disables_its_tools_and_its_advert():
    assert agent_tools._tool_disabled("list_sfx_library")
    assert agent_tools._tool_disabled("sound_design_pass")
    assert not agent_tools._tool_disabled("list_music_library")
    state = agent_prompt.project_state_block(
        "v", "idx", "edl", [], [])
    assert "sound-effects pack" not in state
    assert "music library" in state             # music still advertised
    assert agent_tools.sound_design_pass(None).startswith("REJECTED")
    assert "empty" in agent_tools.list_sfx_library(None)


# ── the fail-twice rule is gone ──────────────────────────────────────────

def test_the_prompt_no_longer_orders_a_stop_after_two_failures():
    p = agent_prompt.system_prompt()
    assert "fails twice" not in p
    assert "stop retrying" not in p
    # What replaced it: change the approach, keep going.
    assert "different route" in p
