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
