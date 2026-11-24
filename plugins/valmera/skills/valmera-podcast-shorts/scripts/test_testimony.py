import wf1_coverage as coverage
import wf1_display as display


def test_route_research_keeps_independent_artifacts_and_groups_testimony(tmp_path):
    import json
    from types import SimpleNamespace
    words = [dict(w=str(i), start=i, end=i+1) for i in range(10)]
    route = dict(first=1, last=7, speaker="Guest", title="Customer feedback",
                 steps=[dict(at=2, label="Product"), dict(at=5, label="Engineering")])
    def window(i, **extra):
        return dict(first=i,last=i,name=str(i),must_show=str(i),external=True,**extra)
    result = dict(windows=[window(0),window(2,evidence_role="example"),window(4),
                           window(6,evidence_role="independent"),window(9)], handoffs=[route])
    model = SimpleNamespace(ask=lambda *a, **k:(json.dumps(result),{}),json_block=json.loads)
    actual = coverage.plan(words,"Title",tmp_path,model)
    assert [w["first"] for w in actual["windows"]] == [0,2,6,9]
    assert actual["handoffs"] == [route]


def test_timestamp_is_the_interview_and_independent_scope_survives():
    interview = dict(url="https://www.youtube.com/watch?v=episode")
    timestamp = dict(url=interview["url"]+"&t=1239s")
    primary = dict(url="https://example.org/paper")
    sources = dict(interview=interview, timestamp=timestamp, paper=primary)
    claim = dict(stance="characterization",speaker="Guest",report="Business context matters")
    def bind(key):
        return display.claim_fact(claim,dict(source=key,statement=claim["report"]),sources,[])
    assert bind("timestamp").get("chain",{}).get("speaker") == "Guest"
    assert bind("paper")["perspective"]["speaker"] == "Guest"


def test_scoped_estimate_and_route_replace_adjacent_testimony():
    interview = dict(url="interview")
    scope = dict(first=0,application=3,last=5,speaker="Guest",application_label="Workplace analogy",
                 statement="About 150 social ties",credit="Dunbar · estimate",sources=[dict(url="paper")])
    route = dict(first=6,last=9,speaker="Guest",title="Customer feedback",
                 steps=[dict(at=7,label="Product"),dict(at=8,label="Sales")])
    old = [dict(first=0,last=7,facts=[dict(source=interview,chain=dict(stance="framework"))]),
           dict(first=6,last=9,sequence=[dict(at=6)],facts=[dict(source=interview)]),
           dict(first=6,last=7,facts=[dict(source=dict(url="issue"),example=True)])]
    result = display.testimony_pages(old,dict(borrowed_estimates=[scope],handoffs=[route],sources=dict(interview=interview)))
    assert [(p["first"],p["last"]) for p in result if p.get("scope_cue")] == [(0,2),(3,5)]
    assert [p["facts"][0]["source"]["url"] for p in result if p.get("scope_cue")] == ["paper","interview"]
    assert [p["sequence"] for p in result if p.get("sequence_kind")=="handoff"] == [route["steps"]]
    assert any(p.get("route_example") and p["facts"] == old[-1]["facts"] for p in result)
    assert old[0] not in result and old[1] not in result


def test_introductory_example_reads_the_whole_phrase_and_exits_before_the_handoff():
    words = [dict(w=str(i),start=i,end=i+1,seg=i//4) for i in range(12)]
    route = dict(first=0,last=11,steps=[dict(at=8,label="Product"),dict(at=10,label="Sales")])
    windows = [dict(first=2,last=4),dict(first=6,last=7,evidence_role="example")]
    coverage.fit_route_examples(windows,[route],words)
    assert (windows[1]["first"],windows[1]["last"],windows[1]["say"]) == (5,7,"5 6 7")
    assert coverage.outside_comparisons(windows[1],0,11,dict(handoffs=[route])) == (5,7)
