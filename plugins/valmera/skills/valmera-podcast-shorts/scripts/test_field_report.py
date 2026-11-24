"""A private estimate and the following deployment question in the recorded m27 interview."""
import pytest

import wf1_film as film
from test_wf1_take import quiet_engines


@pytest.fixture
def conversation(tmp_path, monkeypatch, quiet_engines):
    import wf1_coverage
    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda *args: ([], []))
    clauses = ["With the coding they're seeing maybe like 20 30%.",
               "I ask customers every day.", "How many of you have thousands of agents doing core work?",
               "It's just not happened yet."]
    words = []
    for i,text in enumerate(clauses):
        for token in text.split():
            start = len(words)*.24
            words.append(dict(w=token,start=start,end=start+.23,seg=i))
    last = len(words)-1
    owner = "Ali Ghodsi"
    common = dict(speaker=owner, event="Private customer meetings", interview_only=True)
    estimate = dict(common, first=0,last=8,stance="discussed_estimate",subject="Databricks customers",
                    report="Customers report roughly 20–30% speed gains with AI coding.")
    question = dict(common,first=14,last=25,subject=owner,report=clauses[2])
    recall = dict(first=14,last=last,stance="recollection",speaker=owner,report="He has yet to see that scale among customers he asks.")
    opinion = dict(first=0,last=6,stance="characterization",speaker=owner,report="He characterizes their gains as modest.")
    source = dict(url="episode",publisher="All-In Podcast")
    pages = [dict(first=c["first"],last=c["last"],facts=[dict(chain=c,attribution=owner,
                  value=c["report"],source=source)]) for c in (opinion,estimate,recall)]
    duration = words[-1]["end"]+.7
    pack = dict(words=dict(words=words,duration=duration),work=str(tmp_path),ffmpeg="ffmpeg",pictures={},footage=[],
                coverage=dict(windows=[],moves=[],words=words,source_reading=dict(quote_segments=[0])),
                take=dict(path="/take.mp4",w=1920,h=1080,fps=30,dur=duration,faces=[]))
    script = dict(title="Use cases",source_url="episode",segments=[dict(say=t,pictures=[]) for t in clauses])
    return script, pack, dict(claims=[estimate,question,recall],display_facts=pages,coverage_final=True)


def test_estimate_credits_the_report_once(conversation):
    script,pack,d = conversation
    scene,_ = film.build(script,pack,"footage-cut",d)
    ink = [i["t"] for i in scene["items"] if i["k"]=="word" and i["f"][0] <= 20 < i["f"][1]]
    assert "20–30%" in " ".join(ink)
    assert " ".join(ink).count("Ali Ghodsi") == 1


def test_deployment_scope_comes_from_the_recorded_conversations(conversation):
    import wf1_field_report
    script,pack,d = conversation
    def reader(account,words,rows):
        return dict(scope=account,pictures={},workflow_at=18,outcome_at=26,
                    workflow="Order received → check stock → ship",
                    verdict="No company has deployed agents"), []
    scene,_ = film.build(script,pack,"footage-cut",d,read_report=reader)
    ink = " ".join(i["t"] for i in scene["items"] if i["k"]=="word" and i["f"][1] > 194)
    assert "Ali Ghodsi’s conversations with Databricks customers" in ink
    assert "No company" not in ink
    d["claims"][1] = dict(d["claims"][1],event="Industry survey",interview_only=False)
    assert not wf1_field_report.account(d)


def test_new_excerpt_inherits_only_its_own_framing_finding():
    import wf1_short as wf
    p = dict(mode="document", field_example={"kind":"example"}, window=1, path="/publisher",
             f=[288,325], source_slice=None, reframed=True, source_crop=[.06,.35,.92,.80])
    old = dict(fault="reframe", path=p["path"], window=1, f=p["f"], source_crop=[.09,.40,.99,.99])
    assert not wf.unrepaired(p,[old])
    assert wf.unrepaired(p,[dict(old,source_crop=p["source_crop"])])
    assert not wf.unrepaired(dict(p,source_crop=[0,0,1,1]),[dict(old,source_crop=p["source_crop"])])
