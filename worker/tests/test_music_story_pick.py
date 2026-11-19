"""The story first (owner, 20 Nov 2026: "disco music on a Mark Cuban short ... the music should really give you some
sort of goosebumps").

  * a described palette row (a model's `about` of the track and its measured `arc`) reaches the chooser;
  * on a described palette a model ranks every candidate by the film's own lines, turn, payoff and person, and the
    engine auditions them in that order: the story's first pick plays unless the engine cannot arrange it, however
    much better a later one's tempo or drop fits by signal;
  * a run's earlier films' tracks are left out while enough others remain;
  * no model, a model that fails or names nothing, and a set without descriptions: today's path, unchanged.

Signal only: synthetic rows, a stub engine, a fake model, no network."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import music_choose as MC  # noqa: E402
import music_film as MF  # noqa: E402
from score import music as MU  # noqa: E402
from test_music_ref_set import FILM, _Engine, _make_set, _marks, _set_row, lib  # noqa: E402,F401

LINES = [(0.5, "I bought the team for 285 million"), (12.0, "then it stopped being about basketball"), (40.0, "we sold it for 4.3 billion")]


def _described(cid, about, arc, bpm=120.0, palette="Inspiring build"):
    r = _set_row(cid)
    r["tags"]["tempo_bpm"] = bpm
    r.update(about=about, arc=arc, palette=palette, use_for="a founder's turn into the win")
    return r


def _palette(lib, monkeypatch, rows):
    root = _make_set(lib / "music_ref", rows)
    monkeypatch.setenv(MC.SET_ENV, str(root))
    MC._CACHE.clear()
    eng = _Engine()
    monkeypatch.setattr(MU, "analyse", eng.analyse)
    monkeypatch.setattr(MU, "arrange", eng.arrange)
    return eng


def _film():
    return dict(FILM, lines=LINES, brief="About: Mark Cuban. $285M team sold for $4.3B. Mood: reflective, building, triumphant")


def test_a_described_row_reaches_the_chooser(lib, monkeypatch):
    _palette(lib, monkeypatch, [_described("piano-rise", "Piano and strings that swell.", "opens 14 dB under its peak; builds 0:30-0:58 (+12 dB).")])
    t = MC.set_tracks()[0]
    assert t["about"] == "Piano and strings that swell." and t["arc"].startswith("opens 14 dB") and t["palette"] == "Inspiring build"
    assert t["use_for"] and MF.described([t])
    assert not MF.described([{"cid": "plain", "title": "Plain"}]) and not MF.described([])


def test_the_model_ranks_the_palette_by_the_story_and_its_first_pick_plays(lib, monkeypatch):
    rows = [_described("club-130", "A four-on-the-floor house groove.", "opens near full level; no build. kick on 90% of beats.", bpm=124.5),
            _described("piano-rise", "Piano and strings that swell into a full orchestral lift.", "opens 14 dB under its peak; builds 0:30-0:58 (+12 dB).", bpm=92.0),
            _described("trailer", "A hybrid trailer build with braams.", "builds 0:10-0:40 (+10 dB).", bpm=100.0)]
    eng = _palette(lib, monkeypatch, rows)
    asked = []

    def model(system, user, schema):
        asked.append((system, user))
        names = [ln.split('"')[1] for ln in user.split("Candidates:")[1].splitlines() if ln[:1].isdigit()]
        return {"order": [names.index("Piano-Rise") + 1, names.index("Trailer") + 1], "why": "a reflective story that lands on a triumphant reveal"}
    ch, path, tried = MF.choose(_film(), _marks(), "/nonexistent", call=model)
    system, user = asked[0]
    assert "goosebumps" in system and "Tempo and where its drop sits come after fit" in system
    # the model read the film itself and every candidate's account and measured arc
    assert "[12.0] then it stopped being about basketball" in user and "climax at 40.0 s" in user and "Mark Cuban" in user
    assert "A four-on-the-floor house groove." in user and "builds 0:30-0:58 (+12 dB)" in user
    assert ch["cid"] == "piano-rise" and ch["chosen_by"] == "model (story)" and "triumphant reveal" in ch["why"]
    assert [r["cid"] for r in tried] == ["piano-rise"] and path.endswith("piano-rise.m4a")
    # the engine plays the story pick by its own measured arc (no reference-matched section), and wherever it is laid
    assert eng.asked[0] == ("piano-rise.m4a", None) and eng.arcs == [True] and ch["arc"] is True
    assert MF.arranged(_marks(), ch)["arc"] is True and "arc" not in MF.arranged(_marks(), {"cid": "x"})


def test_the_story_pick_stands_unless_the_engine_cannot_arrange_it(lib, monkeypatch):
    rows = [_described("first", "Strings.", "builds."), _described("second", "Piano.", "builds.")]
    eng = _palette(lib, monkeypatch, rows)
    eng.bad = {"first"}

    def model(system, user, schema):
        return {"order": [1, 2], "why": "the strings"}
    ch, _p, tried = MF.choose(_film(), _marks(), "/nonexistent", call=model)
    assert [r["cid"] for r in tried] == ["first", "second"] and tried[0]["carries"] >= MF.NO_ARRANGEMENT
    assert ch["cid"] == "second" and "kept pick 2" in ch["why"]

    # a merely imperfect arrangement of the story's pick (one intro bar it must play) keeps the film, and no later pick
    # is fetched or arranged once none could overcome it
    class Imperfect(_Engine):
        def __init__(self, arc):
            super().__init__()
            self.arc = arc

        def arrange(self, song, marks, profile=None):
            plan = super().arrange(song, marks, profile)
            plan["arc"] = {"repeats": 0, "peak_before_turn": 0, "rise_db": 9.0, "joins": 0}
            if "first" in song["path"]:
                plan.update(intro_bars=1)
                plan["arc"].update(self.arc)
            return plan
    monkeypatch.setattr(MU, "arrange", Imperfect({}).arrange)
    ch2, _p, tried2 = MF.choose(_film(), _marks(), "/nonexistent", call=model)
    assert ch2["cid"] == "first" and tried2[0]["carries"] == 1.5 and len(tried2) == 1
    # one that plays clearly worse on the story's arc (bars played twice, its peak spent before the turn, no rise into
    # the payoff) hands the film to a later pick that plays it cleanly
    monkeypatch.setattr(MU, "arrange", Imperfect({"repeats": 4, "peak_before_turn": 3, "rise_db": 1.0, "joins": 6}).arrange)
    ch3, _p, tried3 = MF.choose(_film(), _marks(), "/nonexistent", call=model)
    assert tried3[0]["carries"] > 10 and ch3["cid"] == "second" and "kept pick 2" in ch3["why"]


def test_tracks_an_earlier_film_of_the_run_plays_go_behind_the_others(lib, monkeypatch):
    rows = [_described(f"cue-{i}", f"Cue {i}.", "builds.") for i in range(8)]
    _palette(lib, monkeypatch, rows)
    seen = []

    def model(system, user, schema):
        seen.append(user)
        return {"order": [1, 4], "why": "x"}
    ch, _p, _t = MF.choose(_film(), _marks(), "/nonexistent", used=["cue-0", "cue-1"], call=model)
    assert "Already under an earlier film of this run (choose another unless nothing else fits): 1, 2" in seen[0]
    assert ch["cid"] == "cue-3" and "already plays under an earlier film" in ch["why"]


def test_the_story_prompt_shares_one_cacheable_prefix_across_films(lib, monkeypatch):
    rows = [_described(f"cue-{i}", f"Cue {i}.", "builds.") for i in range(8)]
    _palette(lib, monkeypatch, rows)
    seen = []

    def model(system, user, schema):
        seen.append(user)
        return {"order": [1], "why": "x"}
    other = dict(_film(), lines=[(0.0, "a different film"), (9.0, "with its own lines")], brief="About: someone else.")
    MF.choose(_film(), _marks(), "/nonexistent", call=model)
    MF.choose(other, _marks(), "/nonexistent", used=["cue-5"], call=model)
    a, b = seen
    stable = a[:a.index("\n\nFilm:")]
    assert stable.startswith("Candidates:\n1. ") and b.startswith(stable) and "Mark Cuban" not in stable
    assert a.index("Mark Cuban") > a.index('"Cue-7"') and b.index("a different film") > b.index("Already under")


def test_without_a_story_ranking_the_path_is_todays(lib, monkeypatch):
    rows = [_described("a-cue", "Strings.", "builds."), _described("b-cue", "Piano.", "builds.")]
    _palette(lib, monkeypatch, rows)
    ch, _p, _t = MF.choose(_film(), _marks(), "/nonexistent", call=None)
    assert ch["chosen_by"] == "signal"

    def down(*_a):
        raise RuntimeError("down")
    ch, _p, _t = MF.choose(_film(), _marks(), "/nonexistent", call=down)
    assert ch["chosen_by"] == "signal" and ch["pool"] == "set"

    def nothing(system, user, schema):
        return {"order": [], "why": ""} if "Candidates:" in user else {"order": [2, 1], "why": "the rank path"}
    ch, _p, _t = MF.choose(_film(), _marks(), "/nonexistent", call=nothing)
    assert ch["chosen_by"] == "model"                       # the story ranking named nothing: the shortlist's ranking ran


def test_a_set_without_descriptions_keeps_the_shortlist_ranking(lib, monkeypatch):
    _palette(lib, monkeypatch, [_set_row("old-one"), _set_row("old-two")])
    asked = []

    def model(system, user, schema):
        asked.append(user)
        return {"order": [1], "why": "x"}
    ch, _p, _t = MF.choose(FILM, _marks(), "/nonexistent", call=model)
    assert asked and "Options:" in asked[0] and "Candidates:" not in asked[0] and ch["chosen_by"] == "model"


def test_the_film_facts_carry_its_lines_and_a_chapter_gets_its_own():
    marks = {"dur_s": 60.0, "words": [(1.0, 2.0)], "cuts": [3.0], "turn_s": 12.0, "payoff_s": 40.0}
    f = MF.facts("podcast", marks, brief="About: Mark Cuban.", mood="reflective", lines=LINES)
    assert f["lines"][1] == (12.0, "then it stopped being about basketball") and "Mood: reflective" in f["brief"]
    assert "lines" not in MF.facts("podcast", marks)
