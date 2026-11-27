"""Follow-ups typed in the same Studio session (20 Nov): a result the export check finds short is asked again, the way a
customer does, before the idea gives way. Offline: the studio, the model and the check are faked.

    $PY -m pytest -q test_follow_up.py
"""
import importlib.util
import json
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


from test_proof import P  # noqa: E402   the one proof module the folder's tests share (test_sound_pictures does the same)
S1 = "Open on his answer, fill the vertical frame, and show pictures of what he describes"
F1 = "Keep the whole firewall graphic in frame and off his face, and cut to the pictures cleanly"
F2 = "End on the end of his sentence"


class Ctx:
    def __init__(self, run):
        self.run_dir, self.tmp, self.run_id, self.args, self.logs = str(run), str(run / "tmp"), "11201527-491bd1", {}, []

    def log(self, m):
        self.logs.append(m)

    def log_raw(self, m):
        pass


def idea(n=0, **kw):
    return dict(dict(title=f"idea {n}", asks=[S1], must_see=[[]], style="reel", technique="pictures over his words", file="/src.mp4",
                     source="src", t0=0.0, t1=60.0), **kw)


def a_check(ok, sha, why=None, defects=()):
    return dict(ok=ok, v=P.CHECK_V, stack_v=P.STACK_V, why=why, defects=list(defects), elements=[dict(asked="pictures", visible=True)],
                all_visible=True, export=dict(tag="final", sha256=sha), spend=[dict(stage="export check")])


def a_record(ctx, k, asks, sha="sha-final"):
    """A kept record.json of a session with these typed asks, as studio.collect_one writes it."""
    sid = f"m{ctx.run_id[-6:]}-{k}-s{P.STACK_V}"
    d = os.path.join(ctx.tmp, "studio", "records", sid)
    os.makedirs(d, exist_ok=True)
    rec = dict(sid=sid, state="done", failures=[], source=dict(file="/src.mp4", t0=0.0, t1=60.0),
               upload=dict(file=os.path.join(d, "upload.mp4"), sha256="sha-up", w=1920, h=1080, dur=60.0),
               asks=[dict(text=t, phase="before_first_result" if n == 0 else "after_result", follow_up=n > 0,
                          written_from=None if n == 0 else f"ask{n}") for n, t in enumerate(asks)],
               exports=[dict(tag=f"ask{n + 1}", file=os.path.join(d, "exports", f"ask{n + 1}.mp4"), sha256=f"sha-{n + 1}", edl_version=n + 2,
                             w=1080, h=1920, dur=40.0) for n in range(len(asks) - 1)]
               + [dict(tag="final", file=os.path.join(d, "exports", "final.mp4"), sha256=sha, edl_version=len(asks) + 1, w=1080, h=1920, dur=40.0)])
    P.jsave(os.path.join(d, "record.json"), rec)
    return rec


def live_checks(ctx, rec, verdicts):
    """The checks the open session made, one a typed request: check_<i>.json in its live folder."""
    live = P.live_dir(ctx, rec["sid"])
    os.makedirs(live, exist_ok=True)
    for i, (ok, sha) in enumerate(verdicts, 1):
        P.jsave(os.path.join(live, f"check_{i}.json"), a_check(ok, sha, why=None if ok else "defects: a cut face"))


class Reached(Exception):
    pass


def to_the_writer(monkeypatch):
    """The script runs to the visual inventory, which hands back what it was given."""
    seen = {}
    monkeypatch.setattr(P, "able_cards", lambda ctx: [dict(id="reel")])
    monkeypatch.setattr(P, "spoken", lambda rec, ex: ("up words", ["export words"]))
    monkeypatch.setattr(P, "hook_beat", lambda rec, path: None)

    def prepare(ctx, idea, rc, *a):
        seen.update(rc=rc, idea=idea)
        raise Reached
    monkeypatch.setattr(P.VI, "prepare", prepare)
    return seen


# ----------------------------------------------------------------- the open session's turns

def turn(live, i, asks, sha):
    os.makedirs(live, exist_ok=True)
    P.jsave(os.path.join(live, f"turn_{i}.json"), dict(i=i, sid="s", asks=[dict(text=t) for t in asks], state="done", failures=[],
                                                        source=dict(file="/src.mp4", t0=0, t1=60), upload=dict(file="/u.mp4"),
                                                        exports=[dict(tag="final", file=f"/ask{i}.mp4", sha256=sha)]))


def test_a_defect_verdict_types_a_follow_up_written_from_the_check_and_a_clean_check_ends_the_session(tmp_path, monkeypatch):
    ctx, live = Ctx(tmp_path), str(tmp_path / "live")
    checked, written = [], []

    def export_check(ctx, rec, ask_text, must):
        checked.append(ask_text)
        if isinstance(ask_text, str):
            return dict(ok=False, v=P.CHECK_V, why="defects: the firewall graphic reads 'irewall'", elements=[dict(asked="pictures", visible=True)],
                        defects=["the firewall graphic is cropped to 'irewall' and covers his face"], export=rec["exports"][0], spend=[])
        return dict(ok=True, v=P.CHECK_V, why=None, elements=[dict(asked="pictures", visible=True)], defects=[], export=rec["exports"][0], spend=[])

    def ask(system, content, **kw):
        written.append(content)
        return dict(say=F1), dict(stage=kw["what"])
    monkeypatch.setattr(P, "export_check", export_check)
    monkeypatch.setattr(P, "ask", ask)
    turn(live, 1, [S1], "sha-1")
    P.answer_turns(ctx, idea(), live, [], P.FOLLOW_UPS)
    assert json.load(open(os.path.join(live, "answer_1.json"))) == dict(text=F1)
    assert "irewall" in written[0] and S1 in written[0]          # the model wrote it from the check's findings
    turn(live, 2, [S1, F1], "sha-2")
    P.answer_turns(ctx, idea(), live, [], P.FOLLOW_UPS)
    assert json.load(open(os.path.join(live, "answer_2.json")))["stop"] is True
    assert checked == [S1, [S1, F1]]                               # the second export is checked against both sentences
    P.answer_turns(ctx, idea(), live, [], P.FOLLOW_UPS)            # answered turns are never checked again
    assert len(checked) == 2


def test_a_follow_up_with_digits_or_too_many_words_is_written_again(monkeypatch):
    replies = iter([dict(say="Show $169,000 big"), dict(say=" ".join(["word"] * 40)), dict(say="Show the dollar amounts as big bold figures")])
    monkeypatch.setattr(P, "ask", lambda system, content, **kw: (next(replies), {}))
    text, spend = P.follow_up(None, idea(), [S1], dict(elements=[dict(asked="figures", visible=False)], defects=[]))
    assert text == "Show the dollar amounts as big bold figures" and len(spend) == 3


def test_after_two_follow_ups_the_session_ends_on_its_result(tmp_path, monkeypatch):
    ctx, live = Ctx(tmp_path), str(tmp_path / "live")
    monkeypatch.setattr(P, "export_check", lambda ctx, rec, ask_text, must: dict(ok=False, v=P.CHECK_V, why="defects: x",
                                                                                  elements=[dict(asked="a", visible=True)], defects=["x"], spend=[]))
    monkeypatch.setattr(P, "ask", lambda *a, **k: pytest.fail("no third follow-up is written"))
    turn(live, 3, [S1, F1, F2], "sha-3")
    P.answer_turns(ctx, idea(), live, [], P.FOLLOW_UPS)
    ans = json.load(open(os.path.join(live, "answer_3.json")))
    assert ans["stop"] is True and "2 follow-ups" in ans["why"]


def test_a_check_that_fails_never_leaves_the_session_waiting(tmp_path, monkeypatch):
    ctx, live = Ctx(tmp_path), str(tmp_path / "live")

    def boom(*a, **k):
        raise TimeoutError("model")
    monkeypatch.setattr(P, "export_check", boom)
    turn(live, 1, [S1], "sha-1")
    P.answer_turns(ctx, idea(), live, [], P.FOLLOW_UPS)
    assert json.load(open(os.path.join(live, "answer_1.json")))["stop"] is True


# ----------------------------------------------------------------- the script

def test_the_clean_check_after_a_follow_up_is_chosen_and_the_film_carries_both_sentences(tmp_path, monkeypatch):
    ctx = Ctx(tmp_path)
    seen = to_the_writer(monkeypatch)
    rec = a_record(ctx, "i0", [S1, F1], sha="sha-final")
    live_checks(ctx, rec, [(False, "sha-1"), (True, "sha-final")])
    monkeypatch.setattr(P, "record", lambda ctx, idea, k, ask_text: rec)
    monkeypatch.setattr(P, "export_check", lambda *a, **k: pytest.fail("the open session's check of the final export is reused"))
    with pytest.raises(Reached):
        P.script(ctx, dict(ideas=[idea(0), idea(1)]))
    st = json.load(open(tmp_path / "script_state.json"))
    assert st["i0"]["ok"] is True and st["i0"]["asks"] == [S1, F1]
    assert [t["ok"] for t in st["i0"]["turns"]] == [False, True]
    assert st["i0"]["check"]["export"]["file"].endswith("exports/final.mp4")   # pointed at the record's own file
    rc = seen["rc"]
    assert rc["typed"] == S1 and [f["typed"] for f in rc["follow_ups"]] == [F1] and rc["sentences_typed"] == 2
    assert [a["text"] for a in rc["asks"]] == [S1, F1] and rc["asks"][1]["follow_up"]
    assert [e["sha256"] for e in rc["exports"]] == ["sha-1", "sha-final"]
    assert P.typed_line(rc) == f"{S1} / {F1}"
    assert P.format_of(dict(idea_full=dict(seen["idea"], format="showcase")), {"format": "showcase"}) == "tutorial"


def test_two_short_follow_ups_move_to_the_next_idea(tmp_path, monkeypatch):
    ctx = Ctx(tmp_path)
    seen = to_the_writer(monkeypatch)
    r0 = a_record(ctx, "i0", [S1, F1, F2], sha="sha-0")
    live_checks(ctx, r0, [(False, "sha-1"), (False, "sha-2"), (False, "sha-0")])
    r1 = a_record(ctx, "i1", ["Big bold captions"], sha="sha-b")
    live_checks(ctx, r1, [(True, "sha-b")])
    made = []
    monkeypatch.setattr(P, "record", lambda ctx, idea, k, ask_text: made.append(k) or {"i0": r0, "i1": r1}[k])
    with pytest.raises(Reached):
        P.script(ctx, dict(ideas=[idea(0), idea(1, asks=["Big bold captions"])]))
    st = json.load(open(tmp_path / "script_state.json"))
    assert made == ["i0", "i1"] and st["i0"]["ok"] is False and st["i1"]["ok"] is True
    assert "follow_ups" not in seen["rc"] and seen["rc"]["typed"] == "Big bold captions"


def test_a_resumed_run_reuses_the_recorded_follow_up_and_never_types_it_again(tmp_path, monkeypatch):
    ctx = Ctx(tmp_path)
    seen = to_the_writer(monkeypatch)
    rec = a_record(ctx, "i0", [S1, F1], sha="sha-final")
    live_checks(ctx, rec, [(False, "sha-1"), (True, "sha-final")])
    chk = dict(a_check(True, "sha-final"), export=rec["exports"][-1])
    chk.pop("spend")
    P.jsave(str(tmp_path / "script_state.json"), dict(i0=dict(ok=True, check=chk, record="x", asks=[S1, F1])))
    monkeypatch.setattr(P.subprocess, "Popen", lambda *a, **k: pytest.fail("a kept session is never driven again"))
    monkeypatch.setattr(P, "export_check", lambda *a, **k: pytest.fail("a kept check is never made again"))
    monkeypatch.setattr(P, "follow_up", lambda *a, **k: pytest.fail("a kept follow-up is never written again"))
    with pytest.raises(Reached):
        P.script(ctx, dict(ideas=[idea(0)]))
    assert [f["typed"] for f in seen["rc"]["follow_ups"]] == [F1]


def test_a_resumed_session_that_ended_before_its_record_is_driven_fresh(tmp_path, monkeypatch):
    """A session that stopped mid-way left turns of a project the stack took down: the next session starts clean."""
    ctx = Ctx(tmp_path)
    sid = f"m{ctx.run_id[-6:]}-i0-s{P.STACK_V}"
    live = P.live_dir(ctx, sid)
    turn(live, 1, [S1], "sha-old")
    P.jsave(os.path.join(live, "answer_1.json"), dict(text=F1))
    monkeypatch.setattr(P, "sources", lambda: [dict(file="/src.mp4", captions="/c.json3")])
    monkeypatch.setattr(P, "json3_words", lambda path: [])
    monkeypatch.setenv("GAUNTLET_LOCK_HELD", "1")
    specs = []

    class Proc:
        returncode = 0

        def __init__(self, cmd, **kw):
            specs.append(json.load(open(cmd[cmd.index("--spec") + 1])))
            assert not os.path.exists(os.path.join(live, "answer_1.json"))
            a_record(ctx, "i0", [S1])

        def poll(self):
            return 0
    monkeypatch.setattr(P.subprocess, "Popen", Proc)
    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: None)
    rec = P.record(ctx, idea(), "i0", S1)
    row = specs[0]["sessions"][0]
    assert [a.get("live", False) for a in row["asks"]] == [False, True, True] and row["follow_ups"]["dir"] == live
    assert rec["asks"][0]["text"] == S1


# ----------------------------------------------------------------- what the film says

def test_receipts_and_the_audit_carry_every_typed_sentence(tmp_path, monkeypatch):
    ctx = Ctx(tmp_path)
    monkeypatch.setattr(P, "spoken", lambda rec, ex: (None, None))
    rec = a_record(ctx, "i0", [S1, F1])
    rc = P.receipts(rec, dict(export=rec["exports"][-1]), S1)
    seen = []
    monkeypatch.setattr(P, "ask", lambda system, content, **kw: seen.append((system, content)) or ({"claims": []}, {}))
    P.audit(dict(segments=[]), rc)
    assert F1 in seen[0][1] and S1 in seen[0][1] and "follow_ups" in seen[0][0]
    segs = [dict(say="a b c d e f g h", show=[], fallback_text="x") for _ in range(8)]
    segs[-1]["show"] = ["VALMERA.IO"]
    segs[5]["say"] = f"I typed {S1}"
    sc = dict(segments=segs, proof=dict(before_after=2, after_after=5))
    assert any("follow-up" in p for p in P.script_problems(sc, S1, rc))
    segs[5]["say"] = f"I typed {S1} then {F1}"
    assert not any("follow-up" in p or "typed sentence" in p for p in P.script_problems(sc, S1, rc))
    assert P.form_words(rc) == P.FORM["words"][1] + len(F1.split())
    one = a_record(ctx, "i1", [S1])
    single = P.receipts(one, dict(export=one["exports"][-1]), S1)
    assert "follow_ups" not in single and P.typed_line(single) == S1


def test_the_typed_panel_fits_every_sentence_typed():
    class Clock:
        def seg_words(self, i):
            return [dict(t0=4.0, t1=8.0)]
    text = f"{S1} / {F1} / {F2}"
    scene = dict(voices={}, items=[])
    panel = P.instruction_panel(scene, Clock(), dict(proof=dict(after_after=2)), text)
    assert " ".join(panel["lines"]) == text and panel["minimum_px"] >= 44


# ----------------------------------------------------------------- the studio side

def load_studio(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))      # collect_one puts the Gauntlet's runner on the path: never past this test
    monkeypatch.setenv("VALMERA_STUDIO", str(tmp_path / "studio"))
    spec = importlib.util.spec_from_file_location("wf_studio_test", os.path.join(HERE, "studio.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "probe", lambda path: dict(w=1080, h=1920, fps=30.0, dur=40.0))
    return m


def test_a_session_with_follow_ups_writes_live_steps_and_its_record_keeps_every_ask_and_export(tmp_path, monkeypatch):
    S = load_studio(tmp_path, monkeypatch)
    sid = "m1-i0-s5"
    media = os.path.join(S.CORPUS, sid, "media", "1")
    os.makedirs(media)
    open(os.path.join(media, f"{sid}.mp4"), "wb").write(b"clip")
    S.write_session(dict(sid=sid, source="/src.mp4", t0=0.0, t1=60.0, asks=[dict(text=S1, phase="before_first_result"),
                                                                             dict(live=True, phase="after_result")],
                         follow_ups=dict(dir="/live", wait_s=10)))
    doc = json.load(open(os.path.join(S.CORPUS, sid, "session.json")))
    assert [(s.get("text"), s.get("live")) for s in doc["steps"][1:]] == [(S1, False), (None, True)]
    assert doc["studio"]["follow_ups"]["dir"] == "/live"
    # the session's journey after a follow-up, as collect_one keeps it
    sdir = tmp_path / "run" / "sessions" / sid
    os.makedirs(sdir)
    for n in ("export_ask1.mp4", "export_ask2.mp4", "delivered.mp4"):
        (sdir / n).write_bytes(n.encode())
    P.jsave(str(sdir / "journey.json"), dict(state="done", failures=[], steps=[
        dict(prompt=S1, typed_phase="before_first_result"), dict(prompt=F1, typed_phase="after_result", follow_up=True, written_from="ask1")],
        exports=[dict(tag="ask1", file="export_ask1.mp4", edl_version=2), dict(tag="ask2", file="export_ask2.mp4", edl_version=3)],
        delivered=dict(file="delivered.mp4", edl_version=3), turns=[dict(i=1, answer=dict(text=F1)), dict(i=2, answer=dict(stop=True))]))
    monkeypatch.setitem(sys.modules, "stack", types.SimpleNamespace(run_dir=lambda run: str(tmp_path / run)))
    S.collect_one("run", sid)
    rec = json.load(open(os.path.join(S.RECORDS, sid, "record.json")))
    assert [(a["text"], a["phase"], a["follow_up"]) for a in rec["asks"]] == [(S1, "before_first_result", False), (F1, "after_result", True)]
    assert [e["tag"] for e in rec["exports"]] == ["ask1", "ask2", "final"] and all(len(e["sha256"]) == 64 for e in rec["exports"])
    assert rec["turns"][0]["answer"] == dict(text=F1)
    assert P.asks_of(rec) == [S1, F1]


def test_recorded_insert_and_word_edges_are_visible():
    ex = dict(dur=13.433, fps=30)
    edl = dict(overlays=[dict(id="ov2", kind="image", start=7.96, duration_s=.70)])
    times = P.review_times(ex, edl, [dict(w="mining", t0=8.30, t1=8.66)])
    assert {239, 249, 259, 260} <= {round(t * 30) for t in times}


def test_studio_keeps_factory_attribution(monkeypatch):
    monkeypatch.setenv("CODEX_PROXY_PROOF", "factory-c1-marketing")
    settings = P.record_settings("11252023-e640ed")
    for key in ("env", "backend_env"):
        assert settings[key]["VALMERA_RUN"] == "11252023-e640ed"
        assert settings[key]["CODEX_PROXY_PROOF"] == "11252023-e640ed"
    assert "VALMERA_RUN" not in P.STACK["env"]


def test_marketing_story_sidecar(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(os.path.join(os.path.dirname(HERE), "_shell"))
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("marketing_cli", os.path.join(HERE, "market.py"))
    market = module_from_spec(spec)
    spec.loader.exec_module(market)
    story = dict(segments=[dict(beat=k, say=s) for k, s in
                           [("hook", "The old cut."), ("turn", "One request."), ("payoff", "The result.")]])
    (tmp_path / "film.json").write_text(json.dumps(dict(script=dict(script=story))))
    market.sources_md(tmp_path)
    lines = (tmp_path / "STORY.md").read_text().splitlines()
    assert len(lines) == 3
    assert all(s["say"] in line for s, line in zip(story["segments"], lines))
    sources = (tmp_path / "SOURCES.md").read_text()                # the sourced receipt carries the same arc (26 Nov)
    assert "## Story" in sources and all(line in sources for line in lines)


def test_missing_display_phrases_use_authored_copy_without_rewriting_the_script():
    from copy import deepcopy
    segments = [
        dict(say="The next says hundreds of millions of dollars.",
             show=[dict(text="HUNDREDS OF MILLIONS OF DOLLARS")]),
        dict(say="Cuban says both in one breath.", emphasis=["one breath"], show=[]),
        dict(say="Try Valmera.", fallback_text="VALMERA.IO"),
    ]
    original = deepcopy(segments)
    P.fill_display_phrases(dict(segments=segments))
    assert [s["fallback_text"] for s in segments] == [
        "HUNDREDS OF MILLIONS OF DOLLARS", "one breath", "VALMERA.IO"]
    for before, after in zip(original, segments):
        assert all(after[k] == v for k, v in before.items())
