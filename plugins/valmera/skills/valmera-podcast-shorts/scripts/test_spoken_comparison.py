"""A context-dependent opening and an estimated multiplier from the All-In interview."""
import wf1_display as display
from test_wf1_take import quiet_engines


def test_opening_hands_its_seat_to_the_primary_excerpt():
    setup = dict(first=1,last=12,facts=[dict(chain=dict(stance="framework",context_start=871.0))])
    excerpt = dict(first=6,last=14,facts=[dict(perspective=dict(stance="concern"),excerpt=dict(words="Read-only tools + read-only workspace"))])
    pages = display.episode_pages([setup,excerpt], {}, [])
    assert [(p["first"],p["last"]) for p in pages] == [(0,5),(6,14)]


def test_hypothetical_uses_only_its_spoken_states():
    words = [dict(w=t,seg=0,t0=i,t1=i+.5) for i,t in enumerate("small show but imagine two hundred thousand people a thousand times".split())]
    claim = dict(first=0,last=10,speaker="Host",subject="Host",stance="personal_estimate",report="A thousand times")
    steps = [dict(at=0,label="Small show"),dict(at=4,label="Large employer"),dict(at=9,label="Illustrative multiplier")]
    source = dict(url="episode",publisher="All-In")
    plan = display.bind(dict(asks=[dict(dev="staircase",at=dict(w=0),until=dict(w=10),
                        args=dict(sequence_kind="comparison",sequence=steps))],arc=[],caption_ends=[2,6,10]),
                        dict(sources=dict(interview=source),speaker_claims=[claim]),words)
    page, = plan["display_facts"]
    assert page["sequence_kind"] == "comparison" and page["facts"][0]["attribution"] == "Host"
    assert page["sequence"] == steps
    assert {3,8} <= set(plan["caption_ends"])


def test_checked_permissions_and_spoken_multiplier_share_the_live_take(tmp_path, monkeypatch, quiet_engines):
    from test_wf1_take import _sup, cuts_two_shot
    import wf1_film as film
    import wf1_coverage
    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda *a: ([],[]))
    pack = _sup(tmp_path, cuts_two_shot())
    words = pack["words"]["words"]
    words[-3]["w"], words[-2]["w"], words[-1]["w"] = "a", "thousand", "times"
    # The short lead-in previously merged forwards, revealing the multiplier early.
    words[-3].update(start=words[-2]["start"]-.15,end=words[-2]["start"]-.02)
    script = dict(title="Access", source_url="episode",segments=[dict(say=" ".join(w["w"] for w in words if w["seg"]==i)) for i in (0,1)])
    source = dict(url="https://docs.openclaw.ai/gateway/security/tool-permissions",publisher="OpenClaw")
    excerpt = dict(words="Read-only tools + read-only workspace",url=source["url"],speaker="OpenClaw",
                   event="Tool and agent permissions",locator="Per-agent access profiles")
    fact = dict(value=excerpt["words"],excerpt=excerpt,source=source,perspective=dict(stance="concern",speaker="Guest"))
    last=len(words)-1
    pages=[dict(first=1,last=7,facts=[fact]),dict(first=9,last=last,sequence_kind="comparison",
           facts=[dict(source=dict(url="episode"),attribution="Host",value="Small company")],
           sequence=[dict(at=9,label="Small company"),dict(at=last-1,label="Multiplier")])]
    scene,beats=film.build(script,pack,"prints",dict(coverage_final=True,display_facts=pages,
                          caption_ends=[7,8,last-3,last-2,last]))
    frame=beats["display_facts"][0]["f"][0]
    assert any(i["k"]=="card" and i["pic"]!="plate" and i["f"][0]<=frame<i["f"][1] for i in scene["items"])
    cue=next(i for i in scene["items"] if i.get("role")=="cap" and "thousand" in i["t"])
    onset = film.Clock.f(words[-2]["start"] + film.LEAD_F / film.FPS)-1
    assert onset <= cue["f"][0] <= onset+1
