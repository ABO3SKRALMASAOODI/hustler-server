"""Replies say only what happened; captions say only what was said (R2, 30 Oct 2026).

The cases below replay real production turns from the 120-day hallucination
audit (round3/R2_JOURNEYS_TRUTH.md), anonymised: user text, names and
storage keys are replaced, the EDL layers and the reply's claim shapes are
kept. Each test names the turn's model and month.
"""
from types import SimpleNamespace

import agent_loop
import caption_truth
import reply_grounding as g
import url_media


def _edl(**kw):
    base = {"keep": [{"start": 0.0, "end": 60.0}], "captions": {"mode": "from_transcript",
            "style": {"preset": "clean"}}, "effects": {"grade": None, "zooms": [],
            "transition": None, "fade_in_s": None, "fade_out_s": None},
            "music": [], "sfx": [], "texts": [], "inserts": [], "overlays": [], "speed": [],
            "voiceover": [], "frame": {"mode": "crop", "ratio": "9:16"}, "master": None}
    base.update(kw)
    return base


# ── the change list comes from the EDL diff ──────────────────────────────

def test_change_list_names_what_the_diff_shows():
    before = _edl()
    after = _edl(keep=[{"start": 0.0, "end": 20.0}, {"start": 25.0, "end": 60.0}],
                 captions={"mode": "from_transcript", "style": {"preset": "podcast"}},
                 music=[{"start": 0, "end": 15, "storage_key": "music/1/a.mp3"}])
    text = g.change_list_text(g.edl_changes(before, after))
    assert "cut 5.0s" in text
    assert "restyled the captions (podcast)" in text
    assert "added 1 music track" in text
    assert "grade" not in text and "zoom" not in text
    assert g.change_list_text(g.edl_changes(before, before)) == "nothing in the edit changed"


# ── effects a reply names must be in this turn's diff ─────────────────────

def test_named_effects_that_did_not_change_are_flagged():
    # gpt-5.6-luna, Aug: only the captions changed; the reply also claimed a
    # grade and two zooms.
    before = _edl()
    after = _edl(captions={"mode": "from_transcript", "style": {"preset": "podcast"}})
    draft = ("I restyled the captions in the podcast look, added a vibrant colour grade "
             "and two punch-in zooms on the key lines.")
    layers = {layer for _s, layer in g.effect_violations(draft, before, after)}
    assert layers == {"grade", "zoom"}


def test_true_claims_states_offers_and_negations_pass():
    before = _edl()
    after = _edl(keep=[{"start": 0.0, "end": 40.0}],
                 effects={"grade": "vibrant", "zooms": [{"id": "zm1", "start": 3, "end": 5}]})
    for ok in ("I added a vibrant colour grade and a punch-in zoom on the hook.",
               "I cut the slow middle and kept your captions and music exactly as they were.",
               "I can add background music if you want.",
               "I didn't add music: you asked me not to.",
               "Want me to add sound effects on the cuts?"):
        assert g.effect_violations(ok, before, after) == [], ok


def test_export_claims_are_flagged_invitations_are_not():
    assert g.export_violations("The export is ready, download it below.")
    assert g.export_violations("I've exported your video in 1080p.")
    for ok in ("Your edit is ready to export.", "Press Export when you're happy with it.",
               "Once you export it, the file keeps the watermark."):
        assert g.export_violations(ok) == [], ok


def test_quotes_must_be_in_the_transcript():
    index = {"words": [{"w": w} for w in
                       "so the thing about building a company is you have to love the problem".split()]}
    assert g.quote_violations('He says "you have to love the problem" at 0:04.', index) == []
    bad = g.quote_violations('She says "never give up on your dreams" at the end.', index)
    assert bad and bad[0][1] == "never give up on your dreams"
    # the editor's own title text is not a quote of the speaker
    edl = _edl(texts=[{"text": "NEVER GIVE UP ON YOUR DREAMS"}])
    assert g.quote_violations('The title says "never give up on your dreams".', index, edl) == []


def test_drop_sentences_keeps_the_rest():
    draft = "I cut the pauses. I added a vibrant grade. The preview is on the right."
    assert g.drop_sentences(draft, ["I added a vibrant grade."]) == \
        "I cut the pauses. The preview is on the right."


# ── the honesty guard uses all of it ──────────────────────────────────────

def _ctx(before, after, versions=(13,), previewed=True, index=None):
    return SimpleNamespace(
        job={"id": 9001}, versions_written=list(versions), images_generated=[], urls_fetched=[],
        web_recordings=None, audio_extracted=None,
        last_preview={"edl_version": 13, "duration_s": 40.0} if previewed else None,
        last_selfcheck=None, agent_model="gpt-6-luna", write_calls=["cut_range"],
        turn_start_edl={"version": 12, "json": before}, index=index or {},
        latest_edl=lambda: {"version": 13, "json": after})


def _stub_model(monkeypatch, redraft):
    calls = []

    def create(client, model, msgs, **kw):
        calls.append(msgs)
        msg = SimpleNamespace(content=redraft)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)
    monkeypatch.setattr(agent_loop.llm, "create_with_dialect", create)
    monkeypatch.setattr(agent_loop.llm, "record", lambda *a, **k: None)
    return calls


def _h():
    return {"false_claims": 0, "corrective_note": False}


def test_unbacked_effect_regenerates_with_the_diff_and_then_drops_the_sentence(monkeypatch):
    before = _edl()
    after = _edl(keep=[{"start": 0.0, "end": 40.0}])
    draft = "I cut the slow middle (20 s). I also added a cinematic colour grade. Preview is on the right."
    calls = _stub_model(monkeypatch, draft)          # the redraft repeats the claim
    h = _h()
    out = agent_loop._enforce_honesty(_ctx(before, after), None, [], [], draft, 12, h)
    # the regeneration was told what really changed, from the EDL diff
    note = calls[0][-1]["content"]
    assert "what changed in the edit: cut 20.0s" in note
    assert "names the grade" in note
    assert "colour grade" not in out
    assert out.startswith("I cut the slow middle (20 s).")
    assert h.get("grounding_dropped") == 1 and h["false_claims"] == 1


def test_a_true_redraft_is_used(monkeypatch):
    before = _edl()
    after = _edl(keep=[{"start": 0.0, "end": 40.0}])
    good = "I cut the slow middle, so the video is now 40 seconds. The preview is on the right."
    _stub_model(monkeypatch, good)
    out = agent_loop._enforce_honesty(_ctx(before, after), None, [], [],
                                      "I cut 20 s and added two zooms.", 12, _h())
    assert out == good


def test_true_replies_are_untouched(monkeypatch):
    before = _edl()
    after = _edl(keep=[{"start": 0.0, "end": 40.0}], effects={"grade": "cinematic", "zooms": []})
    calls = _stub_model(monkeypatch, "unused")
    draft = "I cut the slow middle and added a cinematic colour grade. The preview is on the right."
    assert agent_loop._enforce_honesty(_ctx(before, after), None, [], [], draft, 12, _h()) == draft
    assert calls == []


def test_facts_reply_leads_with_the_change_list(monkeypatch):
    before = _edl()
    after = _edl(keep=[{"start": 0.0, "end": 40.0}],
                 music=[{"start": 0, "end": 40, "storage_key": "music/1/a.mp3"}])
    denial = "Nothing was changed this turn. I also added a vibrant grade."
    _stub_model(monkeypatch, denial)
    out = agent_loop._enforce_honesty(_ctx(before, after), None, [], [], denial, 12, _h())
    assert out.startswith("I changed the edit in this turn (version 13, was 12): "
                          "cut 20.0s (kept 40.0s of the source); added 1 music track")
    assert "vibrant grade" not in out and "Nothing was changed" not in out


# ── captions never change what was said ───────────────────────────────────

def test_real_corrections_that_rewrote_speech_are_refused():
    # owner factory projects, Oct (podcast shorts): the speech itself was rewritten
    for src, dst in (("recover the the the the the first stage",
                      "recover the Falcon 9 rocket's reusable first stage"),
                     ("a spade being a space sparing civilization",
                      "us finally becoming a true spacefaring civilization"),
                     ("we're gonna", "We won't"),
                     ("he loves it", "he hates it"),
                     ("so we did it", "so we never did it"),
                     ("we can do it", "we can't do it"),
                     ("I do", "I don't")):
        ok, why = caption_truth.faithful_fix(src, dst)
        assert not ok, (src, dst)
        assert why


def test_real_respellings_are_kept():
    for src, dst in (("Jeremis", "Jeremias"), ("Bolterra's", "Voltaire's"), ("ai", "AI"),
                     ("Doge", "DOGE"), ("CUDNN", "cuDNN"), ("b three", "V 3"),
                     ("arthur c clark", "Arthur C. Clarke"), ("star trek", "Star Trek"),
                     ("computer thing And", "computer thing. And"), ("the the the", "the"),
                     ("light", "flight"), ("gonna", "going to"), ("coached ya", "coached you")):
        ok, why = caption_truth.faithful_fix(src, dst)
        assert ok, (src, dst, why)


def test_set_caption_fixes_applies_respellings_and_refuses_rewrites():
    import agent_tools
    from schemas import default_edl
    from w0c_support import tool_context
    edl = default_edl(20.0)
    edl["keep"] = [[0.0, 20.0]]
    edl["captions"] = {"mode": "from_transcript"}
    ctx, _fake = tool_context(edl, duration=20.0)
    words = "we're gonna build it with nvidia and ship it".split()
    ctx.index = dict(ctx.index, words=[
        {"w": w, "t0": 1.0 + .5 * i, "t1": 1.4 + .5 * i, "i": i, "p": .99}
        for i, w in enumerate(words)])
    out = agent_tools.set_caption_fixes(ctx, [["nvidia", "NVIDIA"], ["we're gonna", "We won't"]])
    assert out.startswith("EDL v"), out
    assert "NOT APPLIED (would change what was said)" in out and "We won't" in out
    caps = ctx.latest_edl()["json"]["captions"]
    assert [r["to"] for r in caps["corrections"]] == ["NVIDIA"]
    ctx2, _f = tool_context(edl, duration=20.0)
    ctx2.index = ctx.index
    out = agent_tools.set_caption_fixes(ctx2, [["we're gonna", "We won't"]])
    assert out.startswith("REJECTED") and "would change what was said" in out


# ── the stop message keeps its real words and numbers ──────────────────────

def test_blocker_rows_are_shown_as_written():
    verification = {"unresolved_findings": [{
        "code": "dead_air", "category": "audio",
        "message": "dead air: 9.5-11.4s is silent for 1.9s — tighten the cut"}]}
    rows = agent_loop._blocker_display_rows({}, verification)
    assert rows == ["dead air: 9.5-11.4s is silent for 1.9s — tighten the cut"]
    fp, state = agent_loop._objective_blocker_fingerprint({}, verification)
    assert "#" in state["verification"][0][-1]     # the fingerprint stays normalised
    long = "x " * 200 + "“an unclosed quote that runs on and on"
    shown = agent_loop._display_blocker_text(long)
    assert shown.endswith("…") and "“" not in shown


# ── Vimeo page links fall back to the player URL ───────────────────────────

def test_vimeo_page_links_map_to_the_player():
    assert url_media.vimeo_player_url("https://vimeo.com/76979871") == \
        "https://player.vimeo.com/video/76979871"
    assert url_media.vimeo_player_url("https://vimeo.com/76979871/abcdef1234") == \
        "https://player.vimeo.com/video/76979871?h=abcdef1234"
    assert url_media.vimeo_player_url("https://vimeo.com/channels/staffpicks/76979871") == \
        "https://player.vimeo.com/video/76979871"
    assert url_media.vimeo_player_url("https://player.vimeo.com/video/76979871") is None
    assert url_media.vimeo_player_url("https://www.youtube.com/watch?v=abc") is None


def test_a_failed_vimeo_page_retries_the_player_once(monkeypatch, tmp_path):
    seen = []

    def fake_extract(url, workdir, prefer=None, **kw):
        seen.append(url)
        if "player.vimeo.com" not in url:
            raise url_media.FetchMediaError("[vimeo] 1: Failed to fetch macos OAuth token: HTTP Error 401")
        return str(tmp_path / "v.mp4"), {"extractor_key": "VimeoPlayer"}
    monkeypatch.setattr(url_media, "_extract", fake_extract)
    path, info = url_media._extract_with_fallback("https://vimeo.com/76979871", str(tmp_path))
    assert seen == ["https://vimeo.com/76979871", "https://player.vimeo.com/video/76979871"]
    assert info["extractor_key"] == "VimeoPlayer"
    # any other site keeps its single call and its own error
    seen.clear()
    try:
        url_media._extract_with_fallback("https://soundcloud.com/a/b", str(tmp_path))
    except url_media.FetchMediaError:
        pass
    assert seen == ["https://soundcloud.com/a/b"]


# ── the zero-write guard reads every claim it was meant to ────────────────

def test_typographic_apostrophes_no_longer_hide_claims_or_negations():
    # qwen-plus, Jul (zero-write turn): "I’ve added a subtle caption shadow"
    assert agent_loop._reply_violations(
        "I’ve added a subtle caption shadow — the preview confirms it.", False, False, False)
    # an honest "I haven’t added anything" is a negation, not a claim
    assert not agent_loop._reply_violations("I haven’t added anything yet.", False, False, False)


def test_adverbs_bare_participles_and_fresh_previews_are_claims():
    # deepseek-v4-pro, Jul (zero-write, nothing rendered): both claims missed before
    v = agent_loop._reply_violations(
        "I've also set the frame back to 9:16 portrait — here's a fresh preview so you can "
        "check it before exporting.", False, False, False)
    assert len(v) == 2
    # gpt-5.6-luna, Aug (zero-write): "Removed the first second."
    assert agent_loop._reply_violations("Removed the first second.", False, False, False)
    assert agent_loop._reply_violations("The edits are saved and previewed below.", True, False, True)


def test_questions_offers_and_failures_are_not_claims():
    for text in ("Should I cut the ENTIRE shot that has a person in it, or just the part around 1:01?",
                 "Want me to cut the silences?", "Cut the silences, or keep them?",
                 "I couldn't finish rendering the preview for that edit.",
                 "Nothing was removed."):
        assert not agent_loop._reply_violations(text, False, False, False), text


def test_music_video_and_descriptions_are_not_effect_claims():
    before = _edl()
    after = _edl(keep=[{"start": 0.0, "end": 50.0}])
    for text in ("I created a 50-second teaser from the music video, using its own rapid cuts.",
                 "Created the teaser from native footage, which ends on the group poster title.",
                 "Added transitions were removed because they looked abrupt."):
        assert g.effect_violations(text, before, after) == [], text
