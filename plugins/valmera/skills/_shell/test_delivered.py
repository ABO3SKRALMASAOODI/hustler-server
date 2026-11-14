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


def test_the_exact_source_id_is_refused_and_other_workflows_are_not(tmp_path):
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


def test_the_shell_records_its_delivery_and_refuses_an_exact_source_again(tmp_path):
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


def test_the_driver_records_the_folder_where_it_lands(tmp_path):
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
