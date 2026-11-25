"""The Walkman archive, copied cut and delivery regressions."""
import json

import fit
import story
import writer

HISTORY = "https://www.sony.com/en/SonyInfo/CorporateInfo/History/SonyHistory/2-03.html"
CAPSULE = "https://www.sony.com/en/SonyInfo/CorporateInfo/History/capsule/20/"


def test_archive_after_first_three_pages_reaches_writer(monkeypatch):
    urls = ["https://www.sony.com/en/SonyInfo/blog/2021/05/07/", "https://museum.cbmo.net/catalog/sony-walkman.html",
            "https://www.sony.com/en/SonyInfo/News/Press/199907/99-059/", HISTORY, CAPSULE]
    monkeypatch.setattr(writer, "_RAW", dict.fromkeys(urls, "page"))
    copied = []
    monkeypatch.setattr(writer, "extract_quotes", lambda pages, *a, **kw: (copied.extend(pages) or [], None))
    bank = dict(extracted=[], story_copied=[], facts=[])
    writer._extract(bank, urls, [], writer.STORY_SENTENCES, None, [], print, "page copy")
    assert copied == urls


def test_moved_folder_has_three_final_lines_and_their_sources(tmp_path):
    segments = [dict(beat=b, say=v, facts=[str(i)]) for i, (b, v) in enumerate([
        ("hook", "The recorder was heavy."), ("turn", "Sony took the music outside."), ("payoff", "Commuters tried it.")])]
    sc = dict(segments=segments, facts=[dict(id=str(i), url=u) for i, u in enumerate([HISTORY,CAPSULE,HISTORY])])
    (tmp_path / "film.json").write_text(json.dumps(dict(script=dict(script=sc, idea_full=dict(hook="stale idea")))))
    story.sources_md(str(tmp_path))
    lines = (tmp_path / "STORY.md").read_text().splitlines()
    assert len(lines) == 3
    assert all(s["say"] in line and f["url"] in line for line, s, f in zip(lines, segments, sc["facts"]))
    assert (tmp_path / "SOURCES.md").exists()


def test_repeated_take_on_one_line_retains_both_audit_results(monkeypatch):
    def judged(items, *a, **kw):
        return [(i, 10000+i, dict(grade=5 if i else 1, fits_line=bool(i), exact_thing=True, time_ok=True,
                                  version_ok=True, contradicts=False)) for i in range(len(items))], 0
    monkeypatch.setattr(fit, "judge", judged)
    shots = [dict(path="box.mp4", key="box.mp4@0", line=0, src=[i,i+1], f=[i*30,i*30+30],
                  words=w) for i,w in enumerate(["reported shipments", "cassette players"])]
    rows, _ = fit.audit(shots, dict(segments=[dict(say="reported shipments of cassette players")]))
    assert [r["fit"] for r in rows] == [False,True]


def test_a_second_cut_remeasures_only_changed_pixels(tmp_path, monkeypatch):
    import numpy as np
    import wf3
    frames = np.random.default_rng(9).integers(0, 255, (24, 48, 36), dtype=np.uint8)
    shots = [dict(kind="clip", f=[0, 12]), dict(kind="photo", f=[12, 24])]
    monkeypatch.setattr(wf3, "_gray", lambda *a: frames)
    cache = str(tmp_path / "motion.json")
    first = wf3.film_motion("copy", shots, None, cache_path=cache)
    again = wf3.film_motion("copy", shots, None, cache_path=cache)
    frames[13] = 0
    changed = wf3.film_motion("copy", shots, None, cache_path=cache)
    assert [x["reused"] for x in (first, again, changed)] == [0, 2, 1]
    assert first["static_list"] == again["static_list"]


def test_named_picture_uses_the_worker_recorder_in_a_stories_process():
    import os
    import subprocess
    import sys
    import pictures
    import writer
    worker = pictures.worker_dir()
    shell = os.path.join(os.path.dirname(writer.HERE), "_shell")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([writer.HERE, shell, worker]))
    subprocess.run([sys.executable, "-c", "import wf3,pictures; pictures._worker(); "
                    "from score import call; assert callable(call.llm.record)"], env=env, check=True)


def test_source_and_portrait_land_on_their_own_spoken_words():
    import evidence_cut
    import film
    import reel
    say = "Sony met Akio Morita and sold one million players"
    words = [dict(w=w, start=i*.5, end=i*.5+.3, seg=0) for i, w in enumerate(say.split())]
    quote = "Sony sold one million players"
    seg = dict(say=say, facts=["f"], show=[dict(role="name", text="Akio Morita", word_i=2)],
               exhibit=dict(fact="f", excerpt=quote, passages=[dict(text="Sony sold", word_i=0),
                            dict(text="one million players", word_i=6)]))
    sc = dict(segments=[seg], facts=[dict(id="f", url=HISTORY, quote=quote)])
    clock = film.Clock(dict(words=words, duration=4.5), sc)
    head = dict(path="portrait.jpg", w=100, h=120)
    supply = reel.Supply({"Akio Morita": [head]}, {})
    start = film.Clock.f(clock.seg_words(0)[0]["t0"])
    end = start + 135
    timeline = [[start, end, "designed", None, dict(path="old", fallback=True, pick_line=0)]]
    def place(ids):
        return evidence_cut.place(sc, clock, timeline, supply, ids)
    name_frame = film.Clock.f(clock.seg_words(0)[2]["t0"])-1
    portrait = next(r for r in place({fit.take_key(head["path"], (0,0)): dict(work="Akio Morita")}) if r[0] == name_frame)
    assert portrait[2] == "photo" and portrait[4]["depicted_name"] == "Akio Morita"
    planned = place({})
    assert next(r for r in planned if r[0] == name_frame)[2] == "designed"
    result = next(r for r in planned if r[4].get("text") == "one million players")
    assert result[0] == film.Clock.f(clock.seg_words(0)[6]["t0"])-1
    assert all(a[1] == b[0] for a,b in zip(planned, planned[1:]))


def test_directed_sound_resolves_the_actual_device_on_its_word():
    import wf3
    director = wf3._director()
    cut = dict(beats=[dict(w=53, role="list", sound="air", sound_reason="window shrinks")],
               asks=[dict(beat=0, at=dict(w=47), dev="snap"), dict(beat=0, at=dict(w=53), dev="shrink")])
    cue, = director.sound_cues(cut, lambda w: w * 10, {}, use_asked_device=True)
    assert cue["partner"] == {"device": "shrink"} and cue["f"] == 530


def test_a_quiet_directed_move_keeps_the_composed_payoff_accent(monkeypatch):
    import film
    directed = [dict(sound="air", partner=dict(device="shrink"))]
    figure = [dict(sound="tick", partner=dict(text="220 MILLION"))]
    scene = dict(story_cues=directed, figure_cues=figure, composed_events=[dict(dev="shrink", strength=.4)],
                 sound_design=dict(replan=dict(np10=6.5)))
    def plan(scene, *args, **kw):
        if scene["story_cues"] == figure:
            return [dict(event_f=1823)], dict(picture_bindings=[dict(f=1823)])
        return [], dict(picture_bindings=[None])
    monkeypatch.setattr(film, "design_sound", plan)
    sounds, report = film.replan_sound(scene, {})
    assert sounds == [dict(event_f=1823)] and report["directed_bindings"] == [None]
    assert film.replan_sound(dict(scene, story_cues=[]), {})[0] == []


def test_first_repair_recovers_only_the_readable_rendered_window(tmp_path):
    import wf3
    from types import SimpleNamespace
    pics = [dict(f=[0, 8], line=7, kind="clip", path="/original.mp4", src=[2, 2.267]),
            dict(f=[8, 12], line=7, kind="clip", path="/other.mp4", src=[1, 1.133])]
    checks = [dict(f=p["f"], drop="original.mp4@7", judged=True, fit=True, readable=True) for p in pics]
    (tmp_path/"score.json").write_text(json.dumps(dict(beats=dict(pictures=pics))))
    (tmp_path/"check_attempt.json").write_text(json.dumps(dict(checks=dict(stories=dict(picture_fit=dict(shots=checks))))))
    ctx = SimpleNamespace(run_dir=str(tmp_path))
    assert wf3.checked_render_shots(ctx, dict(rounds=1)) == pics[:1]
    assert wf3.checked_render_shots(ctx, dict(rounds=2)) == []


def test_score_topup_reads_only_uncovered_seconds_and_declared_originals(tmp_path, monkeypatch):
    import wf3
    from types import SimpleNamespace
    ctx = SimpleNamespace(supply=str(tmp_path))
    (tmp_path/"sources").mkdir()
    clip = tmp_path/"original.mp4"
    clip.touch()
    rows = [dict(subjects=[name], path=str(clip), url=url, id=url, channel="Sony", title=name, where="youtube")
            for name, url in [("TPS-L2", "a"), ("factory", "b")]]
    (tmp_path/"sources/manifest.json").write_text(json.dumps(rows))
    words = [dict(w=w, start=i, end=i+.8, seg=1) for i,w in enumerate("show twin ports".split())]
    pack = dict(words=dict(words=words, duration=3.), pictures={})
    script = dict(script=dict(segments=[dict(say="setup"),dict(say="show twin ports", see=[dict(thing="TPS-L2", moment="plug in")])],
                  named=[dict(name="TPS-L2"), dict(name="factory")]))
    (tmp_path/"pack.json").write_text(json.dumps(dict(record={})))
    calls = []
    monkeypatch.setattr(wf3, "footage_voiced", lambda ctx, script, durs, **kw: calls.append((script,durs,kw)))
    wf3.topup_from_score(ctx,script,pack,[0,1], windows=[dict(line=1,f=[33,87])])
    assert len(calls) == 2 and calls[0][2]["source_only"] and not calls[1][2].get("source_only")
    request, durations, kw = calls[0]
    assert kw["lines"] == [1] and durations[1] == 1.8
    sc = request["script"]
    assert sc["segments"][1]["claim"] == "show twin ports"
    assert sc["segments"][1]["say"] == "twin ports"
    assert {s["url"] for n in sc["named"] for s in n.get("video_sources", [])} == {"a"}
    assert "factory" not in [n["name"] for n in sc["named"]]


def test_fresh_read_covers_only_the_remaining_part_of_a_failed_window():
    import wf3
    failed = [dict(line=7, f=[1090,1113])]
    picked = [dict(line=7, f=[1077,1095]), dict(line=8, f=[1095,1113]),
              dict(line=7, f=[1104,1108])]
    assert wf3.uncovered_windows(failed, picked) == [
        dict(line=7, f=[1095,1104]), dict(line=7, f=[1108,1113])]


def test_interrupted_score_merge_reuses_completed_acquisition(tmp_path):
    import wf3
    from types import SimpleNamespace
    ctx = SimpleNamespace(supply=str(tmp_path))
    request = dict(script=dict(segments=[dict(say="the Walkman", repair_windows=[[1090,1113]])], named=[]))
    args = (request, [0], [.77], {}, dict(source_only=True), {}, [])
    name = wf3.repair_pass(ctx, "footage7", *args)
    folder = tmp_path / name
    (folder/"one.mp4").write_bytes(b"staged original")
    (folder/"footage.json").write_text(json.dumps(dict(lines=[dict(clips=[dict(file="one.mp4")])])))
    assert wf3.repair_pass(ctx, "footage8", *args) == "footage7"
    request["script"]["segments"][0]["repair_windows"] = [[1050,1065]]
    assert wf3.repair_pass(ctx, "footage8", *args) == "footage8"


def test_a_fitting_unused_take_is_placed_in_the_gap_before_a_new_read(tmp_path):
    import wf3
    held = dict(kind="clip", path="held", line=7, f=[0,30], src=[0.,1.], take=0)
    failing = dict(kind="clip", path="wrong", line=7, f=[30,53], src=[0.,.767], take=0)
    replacement = dict(path=str(tmp_path/"native.mp4"), seconds=.70, subject="TPS-L2", motion=dict(p90=3.))
    (tmp_path/"native.mp4").touch()
    verdict = dict(grade=4, fits_line=True, exact_thing=True, time_ok=True, version_ok=True, contradicts=False)
    pack = dict(footage=[dict(i=7, clips=[replacement])], fit_verdicts={replacement["path"]:{0:{7:verdict}}})
    knobs = {}
    plan = wf3.repair_layout(dict(pictures=[held, failing]), [failing], knobs,
                             lambda: ({},dict(pictures=knobs["checked_shots"])))
    assert plan(pack, None) == []
    a,b = knobs["checked_shots"]
    assert a == held and b["path"] == replacement["path"] and b["f"] == [30,53]
    assert abs((b["src"][1] - b["src"][0]) - .70) < .001


def test_repaired_bridge_group_keeps_its_verified_native_frame(tmp_path):
    import evidence_cut
    import reel
    import wf3
    clip = dict(path=str(tmp_path / "bridge.mp4"), w=1920, h=1080, seconds=1.1, subject="Walkman listeners")
    (tmp_path / "bridge.mp4").touch()
    gap = dict(kind="clip", path="cropped", line=7, f=[1033,1065])
    fits = dict(grade=4, fits_line=True, exact_thing=True, time_ok=True, version_ok=True, contradicts=False)
    pack = dict(footage=[dict(i=7, clips=[clip])], fit_verdicts={clip["path"]: {0: {7: fits}}})
    knobs = {}
    rows = [[1033,1065,"clip","Walkman listeners",dict(path="cropped", pick_line=7)]]
    supply = reel.Supply({}, {7: [clip]})
    def build():
        placed, = evidence_cut.retain_checked(rows, knobs["checked_shots"], supply)
        assert placed[4]["keep_full_frame"] and placed[4]["src"] == knobs["checked_shots"][0]["src"]
        return {}, dict(pictures=knobs["checked_shots"])
    plan = wf3.repair_layout(dict(pictures=[gap]), [gap], knobs, build)
    assert plan(pack, None) == []
