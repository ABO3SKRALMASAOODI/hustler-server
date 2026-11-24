import wf1_film  # initializes the workflow engine paths
import wf1_coverage as coverage
import wf1_display as display


def test_resolver_checks_best_evidence_per_assertion_and_recovers():
    views = ["replica", "other angle", "original", "access log"]
    pictures = {"period": [dict(path=p, page=p) for p in views]}
    window = dict(first=2, last=8, name="period", say="early web", must_show="period hardware",
                  t0=.5, t1=4, archive_views=[["original", "other angle", "replica"]])
    def resolve(unavailable=()):
        looked = []
        def check(rows):
            looked.extend(r[4]["path"] for r in rows)
            return rows, dict(placements=[dict(ok=True, fit=9) for _ in rows], spend=[])
        rows, _ = coverage.checked_proposals(dict(windows=[window]), {}, pictures, 6, check,
                                             rejected={(0, p) for p in unavailable})
        return [r[4]["path"] for r in rows], looked
    assert resolve() == (["original", "access log"], ["original", "access log"])
    assert resolve(["original"])[0] == ["other angle", "access log"]


def test_history_keeps_directed_turn_and_its_source():
    words = [dict(w=str(i), t0=i, t1=i+.5, seg=0) for i in range(7)]
    source = dict(url="episode", publisher="Podcast")
    steps = [dict(at=i, label=str(i), source="interview") for i in (0,2,4)]
    asks = [dict(dev="staircase", at={"w":0}, until={"w":6},
                 args=dict(sequence_kind="history", speaker="Host", sequence=steps))]
    direction = dict(arc=[], asks=asks, beats=[dict(role="figure",w=2),dict(role="turn",w=4)])
    page, = display.bind(direction, dict(sources={"interview":source}), words)["display_facts"]
    assert [s.get("beat") for s in page["sequence"]] == [None, "figure", "turn"]
    assert all(s["source"] == source for s in page["sequence"])


def test_selected_archive_still_settles_its_window_without_motion_research(monkeypatch):
    import wf1_short as wf
    from types import SimpleNamespace
    pack = dict(coverage=dict(windows=[dict(t0=0,t1=3)]))
    monkeypatch.setattr(wf.wf3, "load_pack", lambda ctx: pack)
    pic = dict(path="original.jpg", f=[3,93], window=0, selected_archive_view=True)
    report = dict(placements=[dict(id=0,ok=True,motion_ok=False)])
    assert not wf._rebuild_motion(SimpleNamespace(log=lambda _:None), {}, dict(beats=dict(pictures=[pic])), report, defer=True)
    measured = dict(holds=[[.1,3.1]], cuts_per_10s=1)
    assert wf.hold_windows(measured,pack["coverage"],[pic]) == []
    assert wf.hold_windows(measured,pack["coverage"],[dict(pic,f=[3,40])]) == [0]


def test_figure_performs_the_whole_cue_once_on_its_word():
    from film import Plan
    from types import SimpleNamespace
    plan = Plan(dict(cap=dict(font="InterDisplay-Bold.ttf",px=58),dsp=dict(font="InterDisplay-Bold.ttf",px=145)),{})
    words = ["you","got","an","$8,000","storage","bill","a","month."]
    clock = SimpleNamespace(words=[dict(w=w) for w in words])
    cue = plan.word([250,318], " ".join(words), "cap", .5,.8,role="cap")
    display.figure_cue(plan,cue,clock,3,259,dict(accent="#F4D793",ink="#FFFFFF"))
    assert cue["t"]=="you got an" and cue["f"]==[250,259]
    landed = [i for i in plan.items if i["f"]==[259,318]]
    assert " ".join(i["t"] for i in landed)==" ".join(words)
    amount, = [i for i in landed if i["t"]=="$8,000"]
    assert plan.voices[amount["v"]]["px"] > 2*plan.voices["cap"]["px"]
    assert amount["s"][0]>amount["s"][-1]
