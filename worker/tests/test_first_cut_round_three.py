from types import SimpleNamespace

import numpy as np
import pytest

from score import compiler, picture_cuts, source_guard


def test_reading_column_preserves_full_lines_without_fitting_the_desktop():
    row = {"visible_text": "Instructions", "dense_ui": True, "text_geometry": [
        [.02,.2,.2,.23], [.4,.18,.79,.22], [.4,.3,.76,.33], [.41,.5,.57,.54]]}
    column = source_guard.reading_window(row, 9/16/(16/9), [])
    assert .2 < column[0] <= .4 < .79 <= column[2] < .9
    assert column[1] < .18 and column[3] > .54
    dialog = source_guard.reading_window(row, 9/16/(16/9), [], [.02,.17,.2,.3])
    assert dialog[2] < column[0]
    assert source_guard.reading_window(dict(row, visible_text=""), .316, []) is None


def test_wide_subtitles_and_subject_fit_in_same_source_window():
    row = {"visible_text":"Two lines", "text_geometry":[[.15,.77,.85,.83]]}
    rect = source_guard.reading_window(row, .316, [[.02,.24,.29,.55]])
    assert rect[0] <= .02 and rect[2] >= .85


def test_short_screen_push_reaches_target_and_holds_it():
    from first_cut_visual import screen_keyframes
    track = screen_keyframes([(0, [.3,.2,.69,.4], 2.5)], 3.53, 0)
    assert track[0]["strength"] < track[1]["strength"] == track[-1]["strength"]
    assert track[1]["t"] < 1 and track[-1]["t"] == 3.53


def test_source_dissolve_becomes_clean_frames_on_the_same_audio_clock():
    a, b = np.random.default_rng(2).integers(0, 255, (2,30,50,3), dtype=np.uint8)
    mixed = [((1-u)*a+u*b).astype(np.uint8) for u in np.linspace(0,1,16)]
    frames = [a]*15 + mixed + [b]*15
    spans = picture_cuts.baked_dissolves(frames, 15, [[1,2]])
    assert len(spans) == 1
    l1 = {"items":[{"id":"take","k":"shot","pic":"main","src":[0,3],"f":[0,90]}]}
    picture_cuts.cut_dissolves(l1, spans, 3, 30)
    shots = l1["items"]
    assert all(left["f"][1] == right["f"][0] for left, right in zip(shots, shots[1:]))
    assert shots[0]["f"][0] == 0 and shots[-1]["f"][1] == 90
    assert all(e <= spans[0][0]+1e-6 or s >= spans[0][1]-1e-6 for s,e in (it["src"] for it in shots))
    assert sum(it.get("source_hold", False) for it in shots) == 2


def test_counter_starts_on_first_frame_and_holds_exact_destination(monkeypatch):
    out = []
    ctx = SimpleNamespace(index={"first_film":True}, fps=30, dur_f=90, governed=True)
    monkeypatch.setattr(compiler, "span_of", lambda *_: (0, 90))
    monkeypatch.setattr(compiler, "display", lambda c,i,t,a,b,*args: out.append((a,b,t)))
    monkeypatch.setattr(compiler, "g_snd", lambda *_: None)
    compiler.g_counter(ctx, SimpleNamespace(id="demo"), {"_gid":"demo.counter","from":1920,
                        "to":3840,"fmt":"{n} × 2160","step":2}, 0)
    assert out[0][0:3:2] == (0, "1920 × 2160")
    assert out[-1][2] == "3840 × 2160" and out[-1][1]-out[-1][0] >= 45
    assert all(a[1]==b[0] for a,b in zip(out,out[1:]))


def test_measured_accents_at_zero_and_the_negative_control(tmp_path):
    import subprocess
    import sound_library
    from first_film_sound import rendered_accents, delivery_music_windows
    sid = "impact_9"
    ref = sound_library.path(sid)
    cue = {"at":0., "gain_db":0., "offset_s":.11, "storage_key":sound_library.asset_key(1,sid)}
    delivered, silence = (str(tmp_path/f"{name}.m4a") for name in ("hit","silence"))
    subprocess.run(["ffmpeg","-v","error","-ss","0.11",
                    "-i",ref,"-t","0.75","-c:a","aac",delivered], check=True)
    subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","anullsrc","-t","0.75",silence], check=True)
    assert rendered_accents(delivered, [cue])[0]["at"] == pytest.approx(sound_library.hit_at(cue))
    assert rendered_accents(silence, [cue]) == []
    track = {"storage_key":"song", "start":0.,"end":.75,"gain_db":6.}
    music = delivery_music_windows([(1,track,.75)],[(2,cue,None)],{"song":delivered,cue["storage_key"]:ref})
    assert music[0][1]["_cue_windows"][0][1] >= 6.


def test_thai_tokens_and_measured_title_facts():
    from script_words import join_tokens
    from reply_truth import first_cut_summary
    assert join_tokens(["การ", "บันทึก", "OBS", "Studio"]) == "การบันทึก OBS Studio"
    rows = [dict(group="tx.b01.big", start=0,end=1,text=t) for t in ("TAKE A SEAT","the hard way")]
    rows.append(dict(group="tx.b02.counter",start=1,end=2,text="1647"))
    reply = first_cut_summary({"duration_s":7.2,"picture_qc":{"visible_title_moments":rows},
            "craft_report":{"audible_accents":[{"role":"impact","at":0.03}]}},12.84)
    assert "TAKE A SEAT / the hard way" in reply and "13 seconds" in reply
    assert "0.03 seconds" in reply and "1647" not in reply


def test_adjacent_opaque_sources_cover_every_frame_through_fractional_cut(tmp_path):
    import subprocess
    import renderer
    fps = 30000/1001
    paths = {}
    for name, color in (("base","black"), ("outgoing","red"), ("incoming","blue")):
        paths[name] = str(tmp_path / f"{name}.mp4")
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i",
                        f"color={color}:s=160x90:r=30000/1001:d=1.5","-an","-threads","1",
                        "-c:v","libx264",paths[name]],check=True)
    edl = dict(keep=[[0.,1.5]], frame=dict(ratio="9:16",mode="crop"), overlays=[
        dict(id="first",asset_key="outgoing",kind="video",fit="source_blur",start=0.,duration_s=19/fps),
        dict(id="next",asset_key="incoming",kind="video",fit="cover",start=19/fps,duration_s=1.5-19/fps)])
    out = str(tmp_path / "film.mp4")
    renderer.render_edl(edl, {"words":[],"video":{"duration":1.5}}, paths["base"], out,
                        str(tmp_path), True, suppress_outro=True, asset_locals=paths)
    raw = subprocess.check_output(["ffmpeg","-v","error","-i",out,"-vf","scale=4:4",
                                   "-pix_fmt","rgb24","-f","rawvideo","-"])
    rgb = np.frombuffer(raw,np.uint8).reshape(-1,4,4,3).mean(axis=(1,2))
    assert rgb[:19,0].min() > 200 and rgb[19:,2].min() > 200


def test_bed_claim_matches_music_in_two_separate_passages(tmp_path):
    import subprocess
    from first_film_sound import rendered_bed
    track = dict(start=0., end=2., offset_s=0.)
    paths = []
    for i in (16,42):
        path = str(tmp_path/f"recording-{i}.wav")
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i",
                        f"anoisesrc=color=pink:seed={i}:duration=2",path],check=True)
        paths.append(path)
    assert rendered_bed(paths[0], track, paths[0], 2)
    assert not rendered_bed(paths[0], track, paths[1], 2)


def test_two_salient_subjects_leave_the_empty_centre_out_of_a_single_subject_crop():
    from score.perceive import salient_framing
    gray = np.zeros((90,160), np.uint8)
    gray[26:61,10:36] = 200
    gray[23:58,124:151] = 200
    framing = salient_framing(gray)
    assert abs(framing["focus"][0] - .5) > .15
    boxes = framing["salient_regions"]
    assert min(b[0] for b in boxes) < .25 and max(b[2] for b in boxes) > .75


def test_chapter_reading_time_scales_with_copy_and_keeps_one_centre(monkeypatch):
    out = []
    monkeypatch.setattr(compiler, "span_of", lambda *_: (0, 14))
    monkeypatch.setattr(compiler, "display", lambda ctx,i,t,a,z,opt,b: out.append((z,opt["seat"])))
    monkeypatch.setattr(compiler, "g_snd", lambda *_: None)
    ctx = SimpleNamespace(index={"first_film":True}, fps=30, dur_f=90, governed=True)
    for label in ("{n} SCOUT", "{n} REBUILD THE WEST WALL"):
        compiler.g_counter(ctx, SimpleNamespace(id="game"), {"_gid":"game.counter",
            "chapter":True,"fmt":label,"from":1,"to":1,"seat":[.32,.5]}, 0)
    assert out[1][0] > out[0][0] > 30 and out[1][1][0] == out[0][1][0] == .5


def test_reading_push_keeps_both_ends_of_the_line_on_every_frame():
    target = [.36,-.16,.78,1.16]
    path = source_guard.reading_move(target,.42/1.32,36,30)
    assert path[0][2]-path[0][0] > path[-1][2]-path[-1][0]
    assert all(c[0] <= .36+1e-6 and c[2] >= .78-1e-6 for c in path)
    assert np.allclose(path[-1],target) and path[16] == path[-1]


def test_reading_crop_keeps_a_subject_absent_from_the_storyboard_still():
    l1 = {"frame": [1080, 1920], "fps": 30, "items": [
        {"id": "demo", "k": "shot", "pic": "main", "src": [0, 2], "f": [0, 60]}]}
    index = {"_first_film_source_guard": True, "video": {"width": 1920, "height": 1080, "duration": 2},
             "spatial": {"samples": [{"t": 1.2, "faces": [[.02, .24, .25, .55]]}]},
             "visual_storyboard": {"evidence": [{"representative_t": .5, "visible_text": "Building",
                                                 "text_geometry": [[.45, .77, .85, .83]]}]}}
    source_guard.apply(l1, index, {})
    left, top, right, bottom = l1["items"][0]["crop"]
    assert left <= .02 and top <= .24 and right >= .85 and bottom >= .83


def test_counter_stays_visible_across_a_nearby_picture_cut(monkeypatch):
    from score import lower
    def item(l1, word, start, end, report):
        return {"id": "tx." + word["id"], "start": start / 30, "end": end / 30}
    monkeypatch.setattr(lower, "text_item", item)
    monkeypatch.setattr(lower, "no_double_type", lambda out, report: out)
    words = [{"k": "word", "role": "big", "id": f"b01.counter.{i}.0", "t": t,
              "x": .4, "y": .4, "f": span}
             for i, (t, span) in enumerate([("12", [0, 24]), ("16", [24, 70])])]
    panels = lower.lower_texts({"fps": 30, "items": words, "dur_f": 70}, {},
                              [({"f": [a, b]}, a, b, 1) for a, b in ((0, 22), (22, 69), (69, 70))])
    assert panels[0]["end"] == panels[1]["start"] == 24/30
    assert panels[1]["end"] == 70/30


def test_reply_knows_the_reframe_from_the_actual_player_size():
    from reply_truth import first_cut_summary
    player = {"width": 720, "height": 1280, "duration_s": 7}
    assert "landscape footage" in first_cut_summary(player, 13, {"width": 1920, "height": 1080})
    assert "landscape footage" not in first_cut_summary(player, 13, {"width": 1080, "height": 1920})
