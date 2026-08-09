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

import importlib
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_loop                                            # noqa: E402
import agent_prompt                                          # noqa: E402
import agent_tools                                           # noqa: E402
import llm                                                   # noqa: E402
import renderer                                              # noqa: E402



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


# ── the music library: deleted, not retired ──────────────────────────────

def test_music_library_module_is_gone():
    """2026-08-08: the bundled pack was deleted outright — its 24 tracks
    were copied to R2 under legacy-music/ and every EDL row in the
    database rewritten to those plain storage keys, so nothing needs to
    resolve `library:` ever again."""
    sys.modules.pop("music_library", None)
    with pytest.raises(ImportError):
        importlib.import_module("music_library")
    assert not os.path.isdir(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "music"))


def test_renderer_treats_every_music_key_as_a_storage_object():
    # The migrated legacy keys ride the ordinary fetch path — no scheme,
    # no special case, exactly one kind of music reference.
    seen = []
    out = renderer.music_source("legacy-music/hiphop-abducted.mp3",
                                lambda k: (seen.append(k), "/tmp/x.mp3")[1])
    assert out == "/tmp/x.mp3"
    assert seen == ["legacy-music/hiphop-abducted.mp3"]


# ── the SFX pack and AI sound generation: both deleted ───────────────────

def test_sfx_pack_and_generation_modules_are_gone():
    """2026-08-08: the bundled pack's 18 sounds were copied to R2 under
    legacy-sfx/ and every EDL reference rewritten to those plain keys;
    AI sound generation (eleven) went with it — synthesized one-shots
    read as uncanny under real footage. Real sounds are FETCHED now."""
    for mod in ("sfx_library", "bundled_library", "eleven"):
        sys.modules.pop(mod, None)
        with pytest.raises(ImportError):
            importlib.import_module(mod)
    assert not os.path.isdir(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "sfx"))


def test_stale_sfx_scheme_gets_the_honest_web_first_rejection():
    class _Db:
        def run(self, *a, **k):
            return None

    class _Ctx:
        db = _Db()
        project_id = 1
    sound, err = agent_tools._resolve_sfx(_Ctx(), "sfx:whoosh")
    assert sound is None and "search_sfx" in err


def test_deleted_tools_are_unregistered_and_search_advertised():
    for gone in ("list_sfx_library", "sound_design_pass", "generate_sfx",
                 "list_music_library"):
        assert gone not in agent_tools.TOOLS
    assert "search_sfx" in agent_tools.TOOLS
    assert "fetch_sfx" in agent_tools.TOOLS
    assert not agent_tools._tool_disabled("search_music")
    assert not agent_tools._tool_disabled("search_sfx")
    state = agent_prompt.project_state_block(
        "v", "idx", "edl", [], [])
    assert "sound-effects pack" not in state
    assert "search_music" in state              # found music advertised
    assert "search_sfx" in state                # found sounds advertised
    assert "music library" not in state


# ── failed repairs are bounded without hiding incomplete work ───────────

def test_the_prompt_allows_one_repair_then_stops_churn_honestly():
    p = agent_prompt.system_prompt()
    assert "fails twice" not in p
    assert "stop retrying" not in p
    assert "ONE focused repair batch" in p
    assert "preserve the best valid version" in p
    assert "say plainly what remains" in p


# ── round 91b: a short reply must not disable the language check ─────────
#
# _dominant_script needs a handful of letters to name a script, so the check
# measured the LAST user message only and a one-word answer measured nothing —
# it failed open and the flip went out. Real case, 2026-08-05 21:11: a user who
# had written "What happened", "How many pictures do you see" and "Enhance the
# video and make it look like a one that goes viral" replied "Sure" (four
# letters) and was answered in Russian.

RU_REPLY = ("Я проверил: красный «.io» находится на фиксированной "
            "Valmera-заставке, которую экспорт добавляет автоматически.")


def _users(*texts):
    return [{"role": "user", "content": t} for t in texts]


def test_a_four_letter_message_no_longer_blinds_the_check():
    # The message on its own is unmeasurable — that is the whole bug.
    assert agent_loop._dominant_script("Sure") is None
    # The conversation is not.
    history = _users("What happened", "How many pictures do you see",
                     "Enhance the video and make it look like a one that "
                     "goes viral", "Sure")
    joined = " ".join(m["content"] for m in history)
    assert agent_loop._dominant_script(joined) == "Latin"
    assert agent_loop._dominant_script(RU_REPLY, min_letters=10) == "Cyrillic"


def test_the_enforcement_reads_the_whole_conversation():
    import inspect
    src = inspect.getsource(agent_loop._enforce_reply_language)
    assert 'm.get("role") == "user"' in src, "must scan the message history"
    # ...and BOTH halves of the decision must use that same evidence, or a
    # bilingual user's deliberate Cyrillic reply gets rewritten because their
    # last message happened to be "ok". Round 96c moved the decision into
    # _language_flip; the whole-conversation `joined` text must feed every
    # clause there — the user-script fallback, the wrote-that-script-anywhere
    # veto, and the same-script marker measurement.
    assert "joined" in src and "_language_flip(joined" in src
    flip_src = inspect.getsource(agent_loop._language_flip)
    assert flip_src.count("joined") >= 3


def test_a_user_who_writes_cyrillic_is_still_left_alone():
    ru = " ".join(["Привет, сделай монтаж покороче", "ок"])
    assert agent_loop._script_counts(ru).get("Cyrillic", 0) > 0
