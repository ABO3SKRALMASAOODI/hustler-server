"""The delivered ledger (delivered.py) and the shell's guards: the exact same thing is never delivered twice.

    /Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python -m pytest -q test_delivered.py

No network and no model: the repeat judgement's model is a stand-in that answers by candidate id.
"""
import json
import multiprocessing as mp
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import delivered as DL  # noqa: E402


def test_youtube_links_resolve_to_one_id_and_other_links_to_one_spelling():
    for link in ("https://youtu.be/A_156w0aYtU?si=abc", "https://www.youtube.com/watch?v=A_156w0aYtU&t=10s",
                 "https://m.youtube.com/watch?v=A_156w0aYtU", "https://youtube.com/live/A_156w0aYtU",
                 "https://www.youtube.com/shorts/A_156w0aYtU", "https://www.youtube.com/embed/A_156w0aYtU"):
        assert DL.source_id_of_link(link) == "yt:A_156w0aYtU"
    assert DL.canonical_url("https://WWW.Example.com/ep/1/?utm_source=x&b=2&a=1#t") == DL.canonical_url("http://example.com/ep/1?a=1&b=2")
    assert DL.canonical_url("https://example.com/ep/1") != DL.canonical_url("https://example.com/ep/2")
    assert DL.youtube_id("https://example.com/watch?v=A_156w0aYtU") is None and DL.canonical_url("not a link") is None


def test_the_exact_source_id_is_refused_and_other_workflows_are_not(tmp_path, monkeypatch):
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", "/x")                 # these rows' folders count as delivery folders
    DL.append(DL.make_row("wf1", "/x/Bill Gates - 42 Finals", ["yt:A_156w0aYtU"], dict(title="Bill Gates")))
    with pytest.raises(DL.AlreadyDelivered) as e:
        DL.append(DL.make_row("wf1", "/x/Bill Gates - Additions", ["yt:A_156w0aYtU", "sha256:ab"], dict(title="Bill Gates")))
    assert e.value.row["folder"] == "/x/Bill Gates - 42 Finals" and e.value.source_id == "yt:A_156w0aYtU"
    committed = []
    with pytest.raises(DL.AlreadyDelivered):                  # refused before the folder is renamed into place
        DL.deliver_once(DL.make_row("wf1", "/x/again", ["yt:A_156w0aYtU"]), commit=lambda: committed.append(1))
    assert committed == [] and len(DL.rows()) == 1
    DL.append(DL.make_row("wf3", "/x/a story", ["yt:A_156w0aYtU"], dict(title="A story")))      # another workflow's film may cite it
    DL.append(DL.make_row("wf1", "/x/another podcast", ["yt:akXMYvKjUxM"], dict(title="Elon")))
    assert [r["folder"] for r in DL.rows("wf1")] == ["/x/Bill Gates - 42 Finals", "/x/another podcast"]
    # a run whose delivery is already recorded is never recorded twice, and its rename still happens
    DL.append(DL.make_row("wf4", "/x/f", [], dict(title="T"), run_id="r1"))
    DL.deliver_once(DL.make_row("wf4", "/x/f", [], dict(title="T"), run_id="r1"), commit=lambda: committed.append(2))
    assert committed == [2] and len(DL.rows("wf4")) == 1


def _appender(path, k, n):
    os.environ["VALMERA_DELIVERED_LEDGER"] = path
    for i in range(n):
        DL.append(DL.make_row("wf2", f"/x/{k}-{i}", [f"url:https://example.com/{k}/{i}"], dict(title=f"t{k}-{i}", beats=["b" * 300] * 5)))


def _racer(path, q):
    os.environ["VALMERA_DELIVERED_LEDGER"] = path
    os.environ["VALMERA_DELIVERY_ROOTS"] = "/x"
    try:
        DL.deliver_once(DL.make_row("wf1", f"/x/{os.getpid()}", ["yt:SAMESOURCE1"]))
        q.put("delivered")
    except DL.AlreadyDelivered:
        q.put("refused")


def test_appends_under_concurrency_are_whole_and_one_source_is_delivered_once(tmp_path):
    p = str(tmp_path / "delivered.jsonl")
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_appender, args=(p, k, 25)) for k in range(8)]
    for x in procs:
        x.start()
    for x in procs:
        x.join(60)
    lines = open(p).read().splitlines()
    assert len(lines) == 200 and all(json.loads(line)["wf"] == "wf2" for line in lines)
    assert len({json.loads(line)["folder"] for line in lines}) == 200
    q = ctx.Queue()
    procs = [ctx.Process(target=_racer, args=(p, q)) for _ in range(6)]
    for x in procs:
        x.start()
    for x in procs:
        x.join(60)
    got = sorted(q.get(timeout=10) for _ in procs)
    assert got == ["delivered"] + ["refused"] * 5 and len(DL.rows("wf1")) == 1


def _stand_in(same_ids):
    """The judge's model, answered by candidate id; it also records what it was shown."""
    seen = {}

    def ask(system, content):
        seen["system"], seen["content"] = system, content
        cands = json.loads(content.split("# Candidates\n", 1)[1])
        return json.dumps(dict(verdicts=[dict(id=c["id"], same=c["id"] in same_ids, of="Bill Gates on AI" if c["id"] in same_ids else None,
                                              why="same beats" if c["id"] in same_ids else "another angle") for c in cands])), dict(usd=0.001)
    return ask, seen


def test_the_same_idea_is_refused_and_a_different_angle_is_allowed():
    DL.append(DL.make_row("wf3", "/x/f1", [], dict(title="FedEx's last $5,000 at a blackjack table", angle="the bet that saved FedEx",
                                                   beats=["broke", "Vegas", "won $27,000", "payroll made"], style="character-sheet")))
    ideas = dict(ideas=[dict(id="i1", title="FedEx's last $5,000 at a blackjack table", hook="Fred Smith flew to Vegas", style="character-sheet"),
                        dict(id="i2", title="FedEx's overnight hub at Memphis", hook="Why every package flew through one city", style="reel"),
                        dict(id="i3", title="The Walkman's engineer revolt", style="pinboard")], spend=[])
    ask, seen = _stand_in({"i1"})
    out = DL.guard_ideas("wf3", ideas, ask=ask, log=lambda m: None)
    assert [i["id"] for i in out["ideas"]] == ["i2", "i3"]              # the next idea leads
    assert out["no_repeat"]["repeats"][0]["title"].startswith("FedEx's last") and out["no_repeat"]["checked"] == 3
    assert "NOT a repeat" in seen["system"] and "the bet that saved FedEx" in seen["content"]
    assert "When unsure, it is not a repeat" not in seen["system"]
    assert out["spend"][-1]["what"] == "no-repeat judgement"
    # every idea an exact repeat: the stage stops (a resumed run looks for new ideas)
    with pytest.raises(DL.AlreadyDelivered):
        DL.guard_ideas("wf3", dict(ideas=[dict(id="i1", title="x")]), ask=_stand_in({"i1"})[0], log=lambda m: None)


def test_no_call_is_made_when_the_workflow_delivered_nothing():
    DL.append(DL.make_row("wf3", "/x/f1", [], dict(title="A story")))

    def ask(system, content):
        raise AssertionError("a model was asked")
    out = DL.guard_ideas("wf2", dict(ideas=[dict(id="i1", title="x")]), ask=ask)
    assert [i["id"] for i in out["ideas"]] == ["i1"] and out["no_repeat"]["delivered"] == 0
    assert DL.prompt_block("wf2") == "" and "A story" in DL.prompt_block("wf3") and "not a repeat" in DL.prompt_block("wf3")


def test_a_verdict_missing_for_a_candidate_stops_rather_than_guessing():
    DL.append(DL.make_row("wf2", "/x/f1", [], dict(title="B-roll from one sentence")))
    with pytest.raises(RuntimeError):
        DL.guard_ideas("wf2", dict(ideas=[dict(id="i1", title="x")]), ask=lambda s, c: ('{"verdicts": []}', {}))


def test_tests_never_touch_the_real_ledger(monkeypatch):
    monkeypatch.delenv("VALMERA_DELIVERED_LEDGER")
    with pytest.raises(RuntimeError):
        DL.rows()


# ----------------------------------------------------------------- the shell

def _shell_ctx(tmp_path, run_id, wf="wf3", args=None, source=None):
    import shell
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "film.mp4").write_bytes(b"film")
    ctx = shell.Ctx(str(tmp_path / run_id), dict(run_id=run_id, workflow=wf, args=args or {}, source=source), str(tmp_path / "state"))
    res = dict(script=dict(style=dict(id="reel"), title="The film", idea="The idea", idea_full=dict(title="The idea", hook="h", story="s")),
               ideas=dict(source={}), render={}, check=dict(final=str(tmp_path / "film.mp4"), checks={}))
    return shell, ctx, res


def test_the_shell_records_its_delivery_and_refuses_an_exact_source_again(tmp_path, monkeypatch):
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(tmp_path / "out"))
    src = tmp_path / "source.mp4"
    src.write_bytes(b"the podcast")
    shell, ctx, res = _shell_ctx(tmp_path, "r1-aaaaaa", wf="wf1", source=str(src))
    d = shell.deliver(ctx, {}, str(tmp_path / "out"), res)
    row = DL.rows("wf1")[0]
    assert row["folder"] == d["folder"] and row["idea"]["title"] == "The idea" and row["idea"]["style"] == "reel"
    assert row["source_ids"] == [DL.sha256_file(str(src))] and row["run_id"] == "r1-aaaaaa"
    _, ctx2, res2 = _shell_ctx(tmp_path, "r2-bbbbbb", wf="wf1", source=str(src))
    res2["script"]["title"] = "Another film"
    with pytest.raises(DL.AlreadyDelivered):
        shell.deliver(ctx2, {}, str(tmp_path / "out"), res2)
    assert len(os.listdir(tmp_path / "out")) == 1                     # nothing copied for the refused one


def test_a_driver_run_and_an_inner_short_are_not_recorded_by_the_shell_and_never_write_to_a_delivery_folder(tmp_path, monkeypatch):
    shell, ctx, res = _shell_ctx(tmp_path, "r3-cccccc", args=dict(delivered_ledger="driver"))
    shell.deliver(ctx, {}, str(tmp_path / "outbox"), res)
    _, ctx2, res2 = _shell_ctx(tmp_path, "r4-dddddd", wf="wf1", args=dict(moment=dict(id="m01")))
    res2["script"]["title"] = "A short"
    shell.deliver(ctx2, {}, str(tmp_path / "films"), res2)
    assert DL.rows() == []
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(tmp_path / "Story Films"))
    _, ctx3, res3 = _shell_ctx(tmp_path, "r5-eeeeee", args=dict(delivered_ledger="off"))
    res3["script"]["title"] = "A test"
    with pytest.raises(RuntimeError):
        shell.deliver(ctx3, {}, str(tmp_path / "Story Films"), res3)
    assert not os.path.exists(tmp_path / "Story Films")


def test_the_driver_records_the_folder_where_it_lands(tmp_path, monkeypatch):
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(tmp_path / "Story Films"))
    shell, ctx, res = _shell_ctx(tmp_path, "r6-ffffff", args=dict(delivered_ledger="driver"))
    staged = shell.deliver(ctx, {}, str(tmp_path / "outbox"), res)["folder"]
    final = str(tmp_path / "Story Films" / os.path.basename(staged))
    os.makedirs(os.path.dirname(final))
    DL.deliver_once(DL.row_from_film(staged, "wf3", folder=final), commit=lambda: os.rename(staged, final))
    row = DL.rows("wf3")[0]
    assert os.path.isdir(final) and row["folder"] == final and row["idea"]["title"] == "The idea" and row["run_id"] == "r6-ffffff"


def test_the_shell_takes_a_repeated_idea_out_at_the_ideas_stage(tmp_path, monkeypatch):
    """A whole shell run on a stand-in workflow: the script stage never sees the repeated idea."""
    import shell
    wf = tmp_path / "wfx.py"
    wf.write_text(
        "import json, os\n"
        "def ideas(ctx):\n"
        "    return dict(source={}, ideas=[dict(id='i1', title='Same'), dict(id='i2', title='New angle')], spend=[])\n"
        "def script(ctx, ideas_out):\n"
        "    json.dump([i['id'] for i in ideas_out['ideas']], open(os.environ['SEEN'], 'w'))\n"
        "    raise RuntimeError('stop here')\n")
    monkeypatch.setenv("SEEN", str(tmp_path / "seen.json"))
    monkeypatch.setattr(DL, "IDEA_WORKFLOWS", ("wf3",))
    real_load = shell.load_workflow
    monkeypatch.setattr(shell, "load_workflow", lambda name: real_load(str(wf)))
    monkeypatch.setattr(shell.chain, "SLICE", str(tmp_path / "slice"))
    monkeypatch.setattr(DL, "judge", lambda cands, delivered, ask=None: (
        [dict(id=c["id"], same=c["id"] == "i1", of="Same" if c["id"] == "i1" else None, why="") for c in cands], None))
    DL.append(DL.make_row("wf3", "/x/same", [], dict(title="Same")))
    import argparse
    a = argparse.Namespace(state_root=str(tmp_path / "state"), resume=None, workflow="wf3", source=None, out=str(tmp_path / "out"), args="{}")
    assert shell.run(a) == 2
    assert json.load(open(tmp_path / "seen.json")) == ["i2"]


# ----------------------------------------------------------------- the holes the independent check found (14 Nov)

def test_a_title_only_row_is_judged_on_its_subject_and_story_and_missing_detail_never_allows():
    """Hole 1: a delivered film known only by its title and style reaches the judge marked as such, and the judge's rule
    says a candidate with the same subject and story is a repeat."""
    DL.append(DL.make_row("wf3", "/x/woody", [], dict(title="The Woody who nearly killed Toy Story", style="archive")))
    DL.append(DL.make_row("wf3", "/x/fedex", [], dict(title="FedEx at the blackjack table", angle="the bet", beats=["broke", "won"])))
    briefs = DL.delivered_ideas("wf3")
    assert briefs[0]["detail"] == DL.TITLE_ONLY and "detail" not in briefs[1]
    ask, seen = _stand_in({"i1"})
    out = DL.guard_ideas("wf3", dict(ideas=[dict(id="i1", title="How a bully almost sank Pixar's first film", style="reel"),
                                            dict(id="i2", title="Pixar's render farm", style="reel")]), ask=ask, log=lambda m: None)
    assert [i["id"] for i in out["ideas"]] == ["i2"]
    assert DL.TITLE_ONLY in seen["content"]
    assert "Missing detail never counts in the candidate's favour" in seen["system"]
    assert "same subject that tells the same story the title names, in the plain way the title names it, is a repeat" in seen["system"]


def test_the_same_narration_with_only_a_new_look_is_a_repeat_in_the_rule():
    """Hole 3 (decision a): a reskin is not a new way; a new angle, structure or story is."""
    assert "with only a new visual look" in DL.JUDGE_SYSTEM and "a reskin is not a new way" in DL.JUDGE_SYSTEM
    assert "a different treatment" not in DL.JUDGE_SYSTEM.lower()
    assert "different structure" in DL.JUDGE_SYSTEM
    DL.append(DL.make_row("wf2", "/x/f", [], dict(title="B-roll", angle="a", beats=["b"])))
    assert "new visual look" not in DL.prompt_block("wf3") and "even under a new title or a new visual look" in DL.prompt_block("wf2")


def test_a_delivery_folder_is_known_however_its_path_is_spelled(tmp_path, monkeypatch):
    """Hole 2: APFS is case-insensitive, so another spelling of a delivery folder is that folder."""
    for p in ("/Users/masaoodi/Documents/Valmera/podcast shorts/x", "/Users/masaoodi/Documents/Valmera/PODCAST SHORTS",
              "/Users/masaoodi/Documents/valmera/Story Films/a/b", "/Users/masaoodi/Documents/Valmera/marketing films",
              "/Users/masaoodi/Documents/Valmera/Long Films/../Long Films/x"):
        assert DL.in_delivery_root(p), p
    for p in ("/Users/masaoodi/Documents/Valmera/Podcast Shorts 2/x", "/Users/masaoodi/Documents/Valmera/.valmera/week-1112/codex-tests/x",
              "/Users/masaoodi/Documents/Valmera/Story"):
        assert not DL.in_delivery_root(p), p
    root = tmp_path / "Story Films"
    (root / "done").mkdir(parents=True)
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(root))
    assert DL.in_delivery_root(str(tmp_path / "story films" / "new"))           # case-folded, not yet existing
    assert DL.in_delivery_root(str(tmp_path / "STORY FILMS" / "done"))          # existing, the same folder by identity
    (tmp_path / "alias").symlink_to(root)
    assert DL.in_delivery_root(str(tmp_path / "alias" / "x"))                  # the same folder through a link
    assert not DL.in_delivery_root(str(tmp_path / "Story Films test")) and not DL.in_delivery_root(str(tmp_path))
    # the --test-out guard of every driver uses this: a test never writes into another spelling of a delivery folder
    shell, ctx, res = _shell_ctx(tmp_path, "r7-aaaaaa", args=dict(delivered_ledger="off"))
    with pytest.raises(RuntimeError):
        shell.deliver(ctx, {}, str(tmp_path / "STORY FILMS"), res)
    assert os.listdir(root) == ["done"]


def test_only_a_delivery_folder_is_recorded_and_everything_else_is_a_test(tmp_path, monkeypatch):
    """Hole 6: a shell run or a driver writing outside the delivery folders is a test: never recorded, never refused."""
    root = tmp_path / "Story Films"
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(root))
    src = tmp_path / "source.mp4"
    src.write_bytes(b"the podcast")
    shell, ctx, res = _shell_ctx(tmp_path, "r8-aaaaaa", wf="wf1", source=str(src))
    lane = tmp_path / "lanes" / "SKILL-WF3"
    d = shell.deliver(ctx, {}, str(lane), res)                                 # a lane test that forgot delivered_ledger=off
    assert os.path.isdir(d["folder"]) and DL.rows() == []
    _, ctx2, res2 = _shell_ctx(tmp_path, "r9-bbbbbb", wf="wf1", source=str(src))
    d2 = shell.deliver(ctx2, {}, str(tmp_path / "STORY FILMS"), res2)          # the real delivery, under another spelling
    assert DL.rows("wf1")[0]["folder"] == d2["folder"]
    _, ctx3, res3 = _shell_ctx(tmp_path, "r10-cccccc", wf="wf1", source=str(src))
    res3["script"]["title"] = "Another test"
    shell.deliver(ctx3, {}, str(tmp_path / "codex-tests"), res3)              # a test may reuse a delivered source
    assert len(DL.rows()) == 1
    moved = []
    got = DL.deliver_once(DL.make_row("wf3", str(tmp_path / "codex-tests" / "f"), [], dict(title="T")), commit=lambda: moved.append(1))
    assert moved == [1] and got["recorded"] is False and len(DL.rows()) == 1


def test_a_remake_reaches_a_delivery_folder_only_through_the_judgement(tmp_path, monkeypatch):
    """Hole 5 (decision b): a remake is test-only, or is judged like any idea."""
    root = tmp_path / "Story Films"
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(root))
    shell, ctx, res = _shell_ctx(tmp_path, "r11-aaaaaa", args=dict(remake="/x/old"))
    res["script"]["remake_of"] = "11141632-33f4b8"
    shell.deliver(ctx, {}, str(tmp_path / "codex-tests"), res)                # a test: fine, never recorded
    assert DL.rows() == []
    _, ctx2, res2 = _shell_ctx(tmp_path, "r12-bbbbbb", args=dict(remake="/x/old"))
    res2["script"]["remake_of"] = "11141632-33f4b8"
    with pytest.raises(DL.NotJudged):                                          # no judgement on record: refused, nothing copied
        shell.deliver(ctx2, {}, str(root), res2)
    assert not os.path.exists(root) or os.listdir(root) == []
    res2["ideas"]["no_repeat"] = dict(checked=1, delivered=1, repeats=[])     # judged and found a new way: delivered
    d = shell.deliver(ctx2, {}, str(root), res2)
    row = DL.rows("wf3")[0]
    assert row["folder"] == d["folder"] and row["remake_of"] == "11141632-33f4b8" and row["judged"] is True
    # a driver's row carries the same facts from film.json
    rec = json.load(open(os.path.join(d["folder"], "film.json")))
    assert rec["no_repeat"]["checked"] == 1
    fr = DL.row_from_film(d["folder"], "wf3")
    assert fr["remake_of"] == "11141632-33f4b8" and fr["judged"] is True
    with pytest.raises(DL.NotJudged):
        DL.deliver_once(dict(fr, judged=None, folder=str(root / "again")))


def _remake_run(tmp_path, monkeypatch, out):
    import argparse
    import shell
    wf = tmp_path / "wfr.py"
    wf.write_text(
        "def ideas(ctx):\n"
        "    return dict(source={'remake_of': 'old'}, ideas=[dict(id='i1', title='The Woody who nearly killed Toy Story')], spend=[])\n"
        "def script(ctx, ideas_out):\n"
        "    raise RuntimeError('stop here')\n")
    monkeypatch.setattr(DL, "IDEA_WORKFLOWS", ("wf3",))
    real_load = shell.load_workflow
    monkeypatch.setattr(shell, "load_workflow", lambda name: real_load(str(wf)))
    monkeypatch.setattr(shell.chain, "SLICE", str(tmp_path / "slice"))
    asked = []
    monkeypatch.setattr(DL, "judge", lambda cands, delivered, ask=None: (asked.append(1) or
                        [dict(id=c["id"], same=True, of="The Woody who nearly killed Toy Story", why="the same script") for c in cands], None))
    a = argparse.Namespace(state_root=str(tmp_path / "state"), resume=None, workflow="wf3", source=None, out=out,
                           args=json.dumps(dict(remake="/x/old")))
    return shell.run(a), asked


def test_a_remake_run_into_a_delivery_folder_is_judged_and_one_into_a_test_folder_is_not(tmp_path, monkeypatch, capsys):
    root = tmp_path / "Story Films"
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(root))
    DL.append(DL.make_row("wf3", "/x/woody", [], dict(title="The Woody who nearly killed Toy Story", style="archive")))
    code, asked = _remake_run(tmp_path, monkeypatch, str(tmp_path / "codex-tests"))
    assert code == 2 and asked == []                                           # a test remake: stops at the stand-in script stage
    assert "stop here" in capsys.readouterr().out
    code, asked = _remake_run(tmp_path, monkeypatch, str(tmp_path / "story films"))
    assert code == 2 and asked == [1]                                          # judged, and the same script is a repeat
    assert "every idea found repeats a delivered one" in capsys.readouterr().out


def test_rewrite_keeps_the_lock_and_a_backup_and_never_loses_a_line(tmp_path):
    p = os.environ["VALMERA_DELIVERED_LEDGER"]
    DL.append(DL.make_row("wf3", "/x/a", [], dict(title="A")))
    with open(p, "a") as f:
        f.write("{half a line\n")
    DL.append(DL.make_row("wf3", "/x/b", [], dict(title="B")))
    bak = str(tmp_path / "bak")
    DL.rewrite(lambda rows: [r for r in rows if r["folder"] != "/x/b"], backup=bak)
    assert [r["folder"] for r in DL.rows()] == ["/x/a"] and "{half a line" in open(p).read()
    assert len(open(bak).read().splitlines()) == 3
    with pytest.raises(RuntimeError):
        DL.rewrite(lambda rows: rows, backup=bak)                              # a backup is never overwritten
    assert os.path.exists(p + ".lock")


def test_thin_film_rows_are_enriched_from_any_copy_of_the_film(tmp_path):
    """Hole 1: a moved film's row gets its idea, angle and beats from a copy of the same run, or of a remake of it."""
    import seed_delivered as SD
    DL.append(DL.make_row("wf3", "/gone/outbox/woody-33f4b8", [], dict(title="The Woody who nearly killed Toy Story", style="archive"),
                          seeded=True, run_id="11141632-33f4b8"))
    DL.append(DL.make_row("wf2", "/gone/outbox/brands-0eb566", [], dict(title="Show the brands he names", style="archive"),
                          seeded=True, run_id="11141807-0eb566"))
    DL.append(DL.make_row("wf3", "/gone/outbox/lost-000000", [], dict(title="A lost film", style="reel"), seeded=True, run_id="1-000000"))
    remake = tmp_path / "codex-tests" / "wf3-out" / "woody-df043a"
    remake.mkdir(parents=True)
    json.dump(dict(run_id="11141815-df043a", style=dict(id="archive"), script=dict(
        remake_of="11141632-33f4b8", title="The Woody who nearly killed Toy Story",
        idea_full=dict(title="The Woody who nearly killed Toy Story", hook="Disney shut down Toy Story because Woody was a bully.",
                       story="In 1993 Pixar showed Disney a reel", turn="a rewrite", payoff="$362 million"))), open(remake / "film.json", "w"))
    copy = tmp_path / "lanes" / "SKILL-WF2" / "brands-0eb566"
    copy.mkdir(parents=True)
    json.dump(dict(run_id="11141807-0eb566", style=dict(id="archive"), script=dict(idea_full=dict(
        title="Show the brands he names", pain="I'm still staring at his face.", claim="Valmera shows the brands",
        hook="He names the brands. Show the brands."))), open(copy / "film.json", "w"))
    done, left = SD.enrich(roots=[str(tmp_path / "codex-tests"), str(tmp_path / "lanes")])
    assert sorted(d["run_id"] for d in done) == ["11141632-33f4b8", "11141807-0eb566"] and left == [dict(run_id="1-000000", title="A lost film")]
    w, b, lost = DL.rows()
    assert w["folder"] == "/gone/outbox/woody-33f4b8" and w["run_id"] == "11141632-33f4b8" and w["seeded"] is True
    assert w["idea"]["beats"][0] == "Disney shut down Toy Story because Woody was a bully." and w["idea"]["angle"]
    assert w["seed"]["enriched_from"].endswith("woody-df043a/film.json")
    assert b["idea"]["subject"] == "I'm still staring at his face." and b["idea"]["beats"]
    assert lost["idea"].get("beats") == [] and DL.delivered_ideas("wf3")[-1]["detail"] == DL.TITLE_ONLY


# ----------------------------------------------------------------- the holes the second independent check found (15 Nov)

FEDEX = dict(title="FedEx's last $5,000 at a blackjack table", angle="the bet that saved FedEx", style="character-sheet",
             beats=["FedEx is days from missing payroll", "Fred Smith flies to Las Vegas with the last $5,000",
                    "He plays blackjack and wins $27,000", "Payroll is made on Monday"])


def _beats_judge(seen):
    """A stand-in for the judge's model that reads its rule: a candidate is the same when its beats are a delivered idea's
    beats (the same narration), whatever its style or title; a new structure carries beats of its own. It also checks that
    the rule it was given says both halves of the owner's decision."""
    def ask(system, content):
        seen["system"] = system
        assert "A reskin is a repeat" in system and "with only a new visual look" in system
        assert "A new way is NOT a repeat, even on exactly the same events" in system and "a dated timeline" in system
        assert "never \"only repackaging\" them" in system
        assert "A new structure is a repeat only when its narration and beats are still the delivered ones" in system
        delivered = json.loads(content.split("# Delivered\n", 1)[1].split("\n\n# Candidates\n", 1)[0])
        cands = json.loads(content.split("# Candidates\n", 1)[1])
        out = []
        for c in cands:
            of = next((d["title"] for d in delivered if d.get("beats") and d["beats"] == c.get("beats")), None)
            out.append(dict(id=c["id"], same=of is not None, of=of, why="the same narration" if of else "a new structure"))
        return json.dumps(dict(verdicts=out)), dict(usd=0.0)
    return ask


TIMELINE = dict(id="t1", title="FedEx, hour by hour: the weekend the company was saved", style="reel",
                angle="a 60-second dated timeline of the weekend",
                beats=["Friday, 1973: FedEx cannot make Monday's payroll", "Friday night: Fred Smith takes the last $5,000 to Las Vegas",
                       "Saturday: blackjack, and $27,000", "Monday morning: payroll is made"])
RESKIN = dict(id="r1", title="The $5,000 bet that saved FedEx", style="archive", angle="the bet that saved FedEx",
              beats=list(FEDEX["beats"]))


def test_a_new_structure_on_the_same_events_is_allowed_and_a_reskin_of_the_same_narration_is_not():
    """Hole 1 (15 Nov): the judge refused a dated timeline of the same events as 'only repackaging those beats'. The rule
    now says a new structure on the same events is a new way unless its narration and beats are the delivered ones, and a
    reskin of the same narration stays a repeat."""
    DL.append(DL.make_row("wf3", "/x/fedex", [], FEDEX))
    seen = {}
    out = DL.guard_ideas("wf3", dict(ideas=[dict(RESKIN), dict(TIMELINE)]), ask=_beats_judge(seen), log=lambda m: None)
    assert [i["id"] for i in out["ideas"]] == ["t1"]
    assert out["no_repeat"]["repeats"] == [dict(title=RESKIN["title"], of=FEDEX["title"], why="the same narration")]
    # the same events in the same order, re-titled as a "timeline" but with the delivered narration: still a repeat
    same_lines = dict(TIMELINE, id="t2", beats=list(FEDEX["beats"]))
    with pytest.raises(DL.AlreadyDelivered):
        DL.guard_ideas("wf3", dict(ideas=[same_lines]), ask=_beats_judge({}), log=lambda m: None)
    s = DL.JUDGE_SYSTEM
    assert "Judge how the story is told, not what happened" in s and "never by themselves a repeat" in s
    assert "a list, a countdown" in s and "another narrator's" in s
    b = DL.prompt_block("wf3")
    assert "a dated timeline, a list, a countdown, another narrator's frame" in b and "even under a new title or a new visual look" in b


@pytest.mark.skipif(not os.environ.get("VALMERA_LIVE_JUDGE"), reason="live gpt-6-sol check: VALMERA_LIVE_JUDGE=1 (about $0.05)")
def test_live_the_judge_allows_a_timeline_of_the_same_events_and_refuses_a_reskin():
    DL.append(DL.make_row("wf3", "/x/fedex", [], FEDEX))
    cands = [{k: v for k, v in dict(TIMELINE).items()}, {k: v for k, v in dict(RESKIN).items()},
             dict(id="c1", title="Five numbers that saved FedEx", style="pinboard", angle="a countdown of the five numbers of one weekend",
                  beats=["5: thousand dollars left, days before payroll", "1: flight to Las Vegas", "27: thousand won at blackjack",
                         "0: paychecks missed on Monday"])]
    verdicts, use = DL.judge([{k: v for k, v in c.items() if k != "sources"} for c in cands], DL.delivered_ideas("wf3"))
    by = {v["id"]: v for v in verdicts}
    print(json.dumps(dict(verdicts=verdicts, usage=use), ensure_ascii=False))
    assert by["t1"]["same"] is False, by["t1"]
    assert by["c1"]["same"] is False, by["c1"]
    assert by["r1"]["same"] is True, by["r1"]


def test_the_firmlink_spelling_of_a_delivery_folder_is_that_folder_whether_it_exists_yet_or_not(tmp_path, monkeypatch):
    """Hole 3 (15 Nov): /System/Volumes/Data/Users/... is /Users/... on a Mac, for roots that do not exist yet too."""
    V = "/Users/masaoodi/Documents/Valmera"
    for p in (f"/System/Volumes/Data{V}/Marketing Films/x", f"/System/Volumes/Data{V}/Long Films",
              f"/SYSTEM/Volumes/data{V}/long films/a/b", f"/System/Volumes/Data{V}/Story Films/new",
              f"/System/Volumes/Data{V}/Podcast Shorts/x", f"//System/Volumes/Data{V}/Marketing Films/y"):
        assert DL.in_delivery_root(p), p
    for p in (f"/System/Volumes/Data{V}/Marketing Films 2/x", f"/System/Volumes/Data{V}/.valmera/week-1112/lanes/I/x",
              f"/System/Volumes/Data{V}", "/System/Volumes/Data", "/System/Volumes/DataUsers/masaoodi"):
        assert not DL.in_delivery_root(p), p
    root = tmp_path / "Long Films"                                                # not created: a root that does not exist yet
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(root))
    real = os.path.realpath(str(tmp_path))
    assert DL.in_delivery_root("/System/Volumes/Data" + real + "/long films/x")
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", "/System/Volumes/Data" + real + "/Long Films")   # a root spelled through the firmlink
    assert DL.in_delivery_root(real + "/LONG FILMS/x") and not DL.in_delivery_root(real + "/Long Films 2")
    assert DL.inside("/System/Volumes/Data/Users/x/.valmera/a", ["/users/X/.valmera"]) and not DL.inside(None, ["/x"])
    with pytest.raises(SystemExit) as e:                                          # a driver's --test-out is refused there too
        DL.driver_cli(["--test-out", f"/System/Volumes/Data{V}/Marketing Films/t"], "market.py", "/x")
    assert "inside a delivery folder" in str(e.value)


def test_driver_mode_must_be_proven_by_the_drivers_token_else_the_shell_records_it(tmp_path, monkeypatch):
    """Hole 4 (15 Nov): delivered_ledger=driver without a driver delivered without recording. Without the token of the
    driver that started this process the shell records it as its own (into a delivery folder) or treats it as a test."""
    root = tmp_path / "Story Films"
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(root))
    import shell
    # no driver: into the delivery folder, recorded by the shell
    shell_, ctx, res = _shell_ctx(tmp_path, "d1-aaaaaa", args=dict(delivered_ledger="driver"))
    assert shell.ledger_mode(ctx) == "shell" and not shell.may_deliver(ctx, str(tmp_path / "outbox"))
    d = shell.deliver(ctx, {}, str(root), res)
    assert [r["folder"] for r in DL.rows("wf3")] == [d["folder"]]
    # a token that is not this process's parent's (another driver, a copied environment): not proven
    monkeypatch.setenv(DL.DRIVER_ENV, f"wf3:{os.getpid()}")
    _, ctx2, res2 = _shell_ctx(tmp_path, "d2-bbbbbb", args=dict(delivered_ledger="driver"))
    res2["script"]["title"] = "Second"
    assert shell.ledger_mode(ctx2) == "shell"
    d2 = shell.deliver(ctx2, {}, str(root), res2)
    assert len(DL.rows("wf3")) == 2 and DL.rows("wf3")[-1]["folder"] == d2["folder"]
    # another workflow's driver: not proven for wf3
    monkeypatch.setenv(DL.DRIVER_ENV, f"wf2:{os.getppid()}")
    assert shell.ledger_mode(ctx2) == "shell"
    # the proven driver: the shell stages outside the delivery folders and leaves the record to the driver
    monkeypatch.setenv(DL.DRIVER_ENV, f"wf3:{os.getppid()}")
    _, ctx3, res3 = _shell_ctx(tmp_path, "d3-cccccc", args=dict(delivered_ledger="driver"))
    res3["script"]["title"] = "Third"
    assert shell.ledger_mode(ctx3) == "driver" and shell.may_deliver(ctx3, str(tmp_path / "outbox"))
    shell.deliver(ctx3, {}, str(tmp_path / "outbox"), res3)
    assert len(DL.rows("wf3")) == 2
    # even a proven driver's shell records a folder that lands in a delivery folder itself
    _, ctx4, res4 = _shell_ctx(tmp_path, "d4-dddddd", args=dict(delivered_ledger="driver"))
    res4["script"]["title"] = "Fourth"
    assert shell.ledger_mode(ctx4, str(root / "x")) == "shell"
    d4 = shell.deliver(ctx4, {}, str(root), res4)
    assert len(DL.rows("wf3")) == 3 and DL.rows("wf3")[-1]["folder"] == d4["folder"]


def test_the_drivers_token_proves_only_the_shell_the_driver_started(tmp_path):
    """The token names the driver's workflow and pid; only a direct child of that driver, given it, is proven."""
    import subprocess
    code = f"import sys; sys.path.insert(0, {HERE!r}); import delivered; print(delivered.driver_proven('wf3'))"
    env = {k: v for k, v in os.environ.items() if k != DL.DRIVER_ENV}
    run = lambda e: subprocess.run([sys.executable, "-c", code], env=e, capture_output=True, text=True).stdout.strip()  # noqa: E731
    assert run(dict(env, **{DL.DRIVER_ENV: DL.driver_token("wf3")})) == "True"
    assert run(env) == "False"
    assert run(dict(env, **{DL.DRIVER_ENV: DL.driver_token("wf2")})) == "False"
    assert run(dict(env, **{DL.DRIVER_ENV: "wf3:1"})) == "False"


def test_the_seed_never_seeds_a_test_film_and_prune_takes_such_rows_out_under_the_lock(tmp_path, monkeypatch):
    """Hole 5 (15 Nov): a film that stayed under .valmera (a lane, an outbox) never reached a delivery folder."""
    import seed_delivered as SD
    V = "/Users/masaoodi/Documents/Valmera"
    lanes = [f"{V}/.valmera/week-1112/lanes/I/20261113-wf2-reading-wipe-b-roll-from-one-sentence-7764c4",
             f"{V}/.valmera/week-1112/lanes/O/20261113-wf3-character-sheet-fedex-ca42d0",
             f"{V}/.valmera/week-1112/lanes/R/20261113-wf4-the-ledger-saving-skylab-44e50f",
             f"{V}/.valmera/workflow/stories/outbox/20261114-wf3-pinboard-nintendo-2d5821",
             f"/System/Volumes/Data{V}/.valmera/workflow/marketing/outbox/20261114-wf2-reel-x-2535f1", None]
    kept = [f"{V}/Podcast Shorts/Jensen Huang - 12 shorts", f"{V}/exports/elon-shorts", f"{V}/Elon Musk - 18 Finished Shorts",
            f"{V}/Story Films/20261115-wf3-reel-a-000001"]
    for f in lanes:
        assert not SD.seedable(f), f
    for f in kept:
        assert SD.seedable(f), f
    for k, f in enumerate(lanes + kept):
        DL.append(DL.make_row("wf3", f, [f"url:https://x/{k}"], dict(title=f"T{k}"), seeded=True, run_id=f"r{k}"))
    DL.append(DL.make_row("wf3", f"{V}/.valmera/lanes/live", [], dict(title="live"), run_id="live"))   # not seeded: left alone
    before = open(os.environ["VALMERA_DELIVERED_LEDGER"]).read()
    assert len(SD.prune(dry=True)) == len(lanes) and open(os.environ["VALMERA_DELIVERED_LEDGER"]).read() == before
    bak = str(tmp_path / "delivered.jsonl.bak-1115")
    gone = SD.prune(backup=bak)
    assert [r["folder"] for r in gone] == lanes and open(bak).read() == before
    assert [r["folder"] for r in DL.rows()] == kept + [f"{V}/.valmera/lanes/live"]
    # the shell ledgers' rows of test films are never seeded again
    led = tmp_path / "ledger.json"
    json.dump(dict(wf3=[dict(run_id="a", idea="Lane film", delivered=lanes[3], at="t"),
                        dict(run_id="b", idea="Delivered film", delivered=kept[3], at="t")]), open(led, "w"))
    monkeypatch.setattr(SD, "SHELL_LEDGERS", [str(led)])
    assert [r["idea"]["title"] for r in SD.shell_ledger_rows(set())] == ["Delivered film"]


def test_a_drivers_command_line_reads_both_flag_forms_and_refuses_any_other_flag(tmp_path):
    """Hole 6 (15 Nov): --test-out=/path was read as the topic and the run delivered."""
    t = str(tmp_path / "codex-tests" / "t")
    for argv in (["--test-out=" + t, "Claude", "Shannon"], ["Claude", "--test-out", t, "Shannon"], ["Claude", "Shannon", "--test-out=" + t]):
        got = DL.driver_cli(argv, "story.py", "/default")
        assert got == dict(out_root=t, test=t, topic="Claude Shannon"), argv
    o = str(tmp_path / "out")
    assert DL.driver_cli(["--out=" + o, "Pixar"], "story.py", "/default") == dict(out_root=o, test=None, topic="Pixar")
    assert DL.driver_cli([], "story.py", "/default") == dict(out_root="/default", test=None, topic=None)
    assert DL.driver_cli(["--", "--why", "-5", "rules"], "story.py", "/d")["topic"] == "--why -5 rules"
    for argv in (["--test_out", t, "Pixar"], ["--testout=" + t], ["--test", t], ["--dry-run", "Pixar"], ["Pixar", "--out"],
                 ["--out", o, "--test-out", t], ["--test-out=", "Pixar"], ["-x", "Pixar"]):
        with pytest.raises(SystemExit) as e:
            DL.driver_cli(argv, "story.py", "/default")
        assert json.loads(str(e.value))["failed"], argv


def test_driver_cli_reads_autocorrected_dashes_as_flags(tmp_path):
    """An em or en dash typed for "--" (autocorrect) is a flag, never topic words: a test stays a test."""
    import delivered as DL
    x = str(tmp_path / "x")
    for spelled in ("\u2014test-out=" + x, "\u2013test-out=" + x):
        got = DL.driver_cli([spelled, "Toy", "Story"], "story.py", str(tmp_path / "out"))
        assert got["test"] == x and got["topic"] == "Toy Story"
    got = DL.driver_cli(["\u2014", "a", "dash"], "story.py", str(tmp_path / "out"))
    assert got["test"] is None
    import pytest
    with pytest.raises(SystemExit):
        DL.driver_cli(["\u2014bogus"], "story.py", str(tmp_path / "out"))
