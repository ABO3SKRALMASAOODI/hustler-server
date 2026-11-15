"""The song a film is posted with (platform_song.py; owner, 15 Nov 2026).

    /Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python -m pytest -q test_platform_song.py
"""
import json
import os

import platform_song as P


def fake_ask(answer, seen=None):
    def ask(system, content, **kw):
        if seen is not None:
            seen.append((system, content))
        return json.dumps(answer), dict(provider="openai", model="gpt-6.1-sol", usd=0.0)
    return ask


def test_the_palette_is_the_judges_songs_titles_only():
    pal = P.palette()
    assert len(pal["songs"]) >= 40
    names = {(s["song"], s["artist"]) for s in pal["songs"]}
    assert ("Succession (Main Title Theme)", "Nicholas Britell") in names and ("METAMORPHOSIS", "INTERWORLD") in names
    assert all(s["uses"] and not any(k in s for k in ("file", "audio", "url")) for s in pal["songs"])


def test_only_palette_songs_survive_and_the_start_is_m_ss():
    pal = P.palette()
    ok = P.check(dict(song="succession (main title theme)", artist="Nicholas  Britell", start=75,
                      alternates=[dict(song="A Made Up Song", artist="Nobody", start="0:00"),
                                  dict(song="METAMORPHOSIS", artist="INTERWORLD", start="0:03"),
                                  dict(song="Succession (Main Title Theme)", artist="Nicholas Britell", start="0:00")]), pal)
    assert [o["song"] for o in ok] == ["Succession (Main Title Theme)", "METAMORPHOSIS"]
    assert ok[0]["start"] == "1:15" and ok[1]["start"] == "0:03"
    assert P.check(dict(song="A Made Up Song", artist="Nobody"), pal) == []


def test_choose_writes_the_record_and_remembers_the_pick(tmp_path):
    seen = []
    ans = dict(song="Back To Black", artist="Amy Winehouse", start="0:00", why="a fall from the top",
               alternates=[dict(song="Clubbed To Death (Kurayamino Variation)", artist="Rob Dougan", start="0:00"),
                           dict(song="Over", artist="Drake", start="0:00")])
    rec, use = P.choose("wf3", dict(title="The Zebrafish Ledger", style=dict(id="dossier"), spend=[1]), 61.2, "film_clean.mp4",
                        state_root=str(tmp_path), ask=fake_ask(ans, seen))
    assert rec["song"] == "Back To Black" and rec["upload"] == "film_clean.mp4" and rec["start"] == "0:00"
    assert rec["original_sound_pct"] == 100 and rec["added_sound_pct"] == 25 and len(rec["alternates"]) == 2
    assert P.recent(str(tmp_path)) == ["Back To Black - Amy Winehouse"]
    # the film is the variable part, last; the system prompt never carries it, and recent picks reach the next film
    system, content = seen[0]
    assert "Zebrafish" not in system and "Zebrafish" in content[0]["text"] and '"spend"' not in content[0]["text"]
    P.choose("wf3", dict(title="Another"), 40, "film.mp4", state_root=str(tmp_path), ask=fake_ask(ans, seen))
    assert "Back To Black - Amy Winehouse" in seen[1][1][0]["text"]


def test_the_prefix_is_byte_identical_for_every_film():
    """The palette and the rules are the cached prefix: two films, the same system prompt byte for byte."""
    pal = P.palette()
    assert P.system_prompt(pal) == P.system_prompt(P.palette())


def test_a_failure_never_stops_a_delivery(tmp_path):
    logs = []

    def broken(*a, **k):
        raise RuntimeError("openai 429")
    rec, spend = P.choose_safely("wf1", dict(title="x"), 30, "film.mp4", state_root=str(tmp_path), log=logs.append, ask=broken)
    assert rec is None and spend == [] and "platform song not chosen" in logs[0]
    rec, spend = P.choose_safely("wf1", dict(title="x"), 30, "film.mp4", ask=fake_ask(dict(song="Nope", artist="No")))
    assert rec is None


def test_delivery_writes_platform_music_beside_the_clean_film(tmp_path, monkeypatch):
    import shell as sh_mod
    for n, b in (("clean.mp4", b"clean"), ("music.mp4", b"music")):
        (tmp_path / n).write_bytes(b)
    (tmp_path / "state").mkdir()
    monkeypatch.setattr(P.llm, "ask", fake_ask(dict(song="Succession (Main Title Theme)", artist="Nicholas Britell", start="0:00",
                                                    why="power", alternates=[])))
    ctx = sh_mod.Ctx(str(tmp_path / "run"), dict(run_id="r1-abcdef", workflow="wf3"), str(tmp_path / "state"))
    res = dict(script=dict(style=dict(id="s1"), title="A film"), ideas={}, render={},
               check=dict(final=str(tmp_path / "clean.mp4"), checks=dict(final_s=58.0), final_music=str(tmp_path / "music.mp4"),
                          music=dict(title="T", author="A"), spend=[dict(stage="check", usd=0.01)]))
    folder = sh_mod.deliver(ctx, {}, str(tmp_path / "out"), res)["folder"]
    pm = json.load(open(os.path.join(folder, "platform_music.json")))
    assert pm["upload"] == "film_clean.mp4" and pm["song"] == "Succession (Main Title Theme)"
    rec = json.load(open(os.path.join(folder, "film.json")))
    assert rec["platform_music"]["song"] == pm["song"] and rec["spend_usd"] == 0.01
    # a run can say no, and a film without a separate score posts film.mp4 itself
    ctx2 = sh_mod.Ctx(str(tmp_path / "run2"), dict(run_id="r2-abcdef", workflow="wf3", args=dict(platform_song=False)), str(tmp_path / "state"))
    res2 = dict(res, script=dict(style=dict(id="s2"), title="B film"))
    f2 = sh_mod.deliver(ctx2, {}, str(tmp_path / "out"), res2)["folder"]
    assert not os.path.exists(os.path.join(f2, "platform_music.json"))
    ctx3 = sh_mod.Ctx(str(tmp_path / "run3"), dict(run_id="r3-abcdef", workflow="wf3"), str(tmp_path / "state"))
    res3 = dict(script=dict(style=dict(id="s3"), title="C film"), ideas={}, render={}, check=dict(final=str(tmp_path / "clean.mp4"), checks={}))
    f3 = sh_mod.deliver(ctx3, {}, str(tmp_path / "out"), res3)["folder"]
    assert json.load(open(os.path.join(f3, "platform_music.json")))["upload"] == "film.mp4"
