"""Regressions from the first trial films of 26 and 27 November."""
from types import SimpleNamespace as NS

import pytest


def timed_words(parts):
    return [dict(w=word, t0=i/10, t1=(i+1)/10) for i, word in enumerate(parts)]


def test_indexed_thai_and_english_fragments_recover_source_times():
    import script_words
    rows = timed_words(["ส", "วั", "ส", "ดี", "ครับ", "r", "es", "ol", "u", "tion", "pix", "el"])
    for r in rows:
        r.update(src_t0=r["t0"]*2 + 5, src_t1=r["t1"]*2 + 5)
    result = script_words.timed(rows, "th-TH")
    assert [r["w"] for r in result] == ["สวัสดี", "ครับ", "resolution", "pixel"]
    assert (result[2]["t0"], result[2]["t1"]) == (.5, 1.)
    assert (result[2]["src_t0"], result[2]["src_t1"]) == (6., 7.)


@pytest.mark.parametrize("lang,parts,transcript,word", [
    ("ja", ["東", "京", "に", "行", "く"], "東京に行く", "東京"),
    ("zh-CN", ["上", "海", "工", "作"], "上海工作", "工作"),
    ("th", ["ค่า", "pix", "el", "size"], "ค่า pixel size", "pixel"),
])
def test_recognizer_uses_words_from_the_transcript(lang, parts, transcript, word):
    from transcribe import _script_words
    from schemas import Word
    words = _script_words([Word(**r) for r in timed_words(parts)], lang, transcript)
    assert word in [w.w for w in words]
    assert "".join(w.w for w in words) == "".join(parts)
    assert words[0].t0 == 0 and words[-1].t1 == len(parts)/10


@pytest.mark.parametrize("text", ["ผู้ที่เก่งขึ้น", "東京都かな", "汉字图像"])
def test_director_shapes_title_with_the_worker_bundled_font(text):
    from score import compiler
    from scene.type import find_font
    rows = compiler.display_lines(NS(H=1920, dna={}), text, {})
    row, = rows
    assert row["font"].startswith("Noto") and row["track"] == 0
    assert find_font(row["font"]).endswith(row["font"])
    assert compiler.font_covers(row["font"], text)
    advance, ascender, descender = compiler.shaped(row["font"], text)
    assert advance > 0 and ascender + descender > 0


def test_reading_layout_follows_complete_line_and_keeps_opening_label(monkeypatch):
    import first_cut, first_cut_visual
    edl = {"keep": [[0, 10]], "frame": {"ratio":"9:16", "mode":"pad_blur"}}
    ctx = NS(first_cut_started=1, first_cut_spend={"usd":0}, first_cut_screen_label="Compare the replies",
             latest_edl=lambda: {"json":edl})
    calls = []
    def apply(c, steps, tool, args, started):
        calls.append((tool,args))
        edl.setdefault("texts",[]).append(args)
    def follow(*args):
        edl["effects"] = {"zooms":[{}]}
        calls.append(("follow",None))
    monkeypatch.setattr(first_cut, "_run", apply)
    monkeypatch.setattr(first_cut_visual, "screen_follow", follow)
    for _ in range(2):
        first_cut._compose_reading_screen(ctx, [])
    assert [x[0] for x in calls] == ["add_text", "follow"]
    assert calls[0][1]["y"] < .3
    box = first_cut_visual.column_top({}, 0., [.09,.94])
    win, _ = first_cut_visual.window_for(box, 16/9)
    assert win[0] <= .09 and win[2] >= .94


def test_wordless_fallback_places_matching_music_only_over_ambience(monkeypatch):
    import first_cut, music_choose
    from score import bed
    facts = {"rms_db":[-20]*16,"music":{"beat":.01,"tonal":.01}}
    ctx = NS(index={"director_preparation":{"perception":{"audio":facts}}}, duration=8.,
             first_cut_hook="Autumn leaves", latest_edl=lambda:{"json":{"keep":[[0., 4.],[5.,8.]]}})
    films, placements = [], []
    monkeypatch.setattr(bed, "available", lambda:True)
    def choose(film, ctx):
        films.append(film)
        return {"cid":"licensed-track"}
    monkeypatch.setattr(music_choose, "choose", choose)
    monkeypatch.setattr(music_choose, "place", lambda ctx, item: placements.append(item) or "EDL v3")
    first_cut._score_visual_sequence(ctx, [])
    facts["music"]["beat"] = .7
    first_cut._score_visual_sequence(ctx, [])
    assert len(placements) == len(films) == 1
    assert films[0]["brief"] == "Autumn leaves" and films[0]["duration_s"] == 7


def test_preview_uses_existing_main_input_for_its_own_insert(tmp_path):
    import renderer
    source = tmp_path / "adopted.mp4"
    source.write_bytes(b"already staged")
    key = "originals/1/deferred.mp4"
    assert renderer._render_asset_source_impl(key, "insert", 0, str(tmp_path), {key:str(source)}) == str(source)


@pytest.mark.parametrize("has_audio", [True, False])
def test_reindex_refreshes_full_size_player_once(monkeypatch, has_audio):
    import db, indexer
    from unittest.mock import MagicMock
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect = [{"id": 3}, None]
    monkeypatch.setattr(db, "latest_edl", lambda c, pid: {"version": 8})
    calls = []
    monkeypatch.setattr(db, "get_or_enqueue_preview_job", lambda c, pid, uid, body: calls.append(body))
    worker_db = NS(run=lambda fn, *args: fn(conn, *args))
    job = dict(payload={"repair_proxy_audio": True}, project_id=1, user_id=7)
    for _ in range(2):
        indexer._finish_proxy_audio(worker_db, job, {"id": 3}, False, {"has_audio": has_audio})
    payload, = calls
    assert payload["edl_version"] == 8 and payload["quality"] == "approval" and payload["force"]
    assert payload["first_cut_original"] and payload["render_dependency_key"] == "landed_original:3"


def test_reply_only_uses_facts_of_delivered_file():
    from reply_truth import first_cut_summary
    assert first_cut_summary({"duration_s":8.3, "shots":42, "sfx":9}) == "an 8-second edit"
    assert first_cut_summary({"duration_s":15., "width":720, "height":1280,
                              "craft_report":{"silent":False}}) == "a 15-second vertical edit with sound"


def test_silent_cutaway_places_by_picture_time_without_a_transcript(monkeypatch):
    import first_cut_visual, stock
    from tools_ext.broll_tools import _moment_window
    edl = {"keep":[[10.,30.]]}
    ctx = NS(index={"words":[]}, latest_edl=lambda:{"json":edl})
    monkeypatch.setattr(stock, "video_available", lambda:False)
    placed = []
    def execute(ctx, steps, name, args, started):
        for moment in args["moments"]:
            placed.append(_moment_window(ctx, edl, moment, 20., []))
    first_cut_visual.place_broll(ctx, [], 0., execute, [(17., 19., "a falling leaf", "maple leaf macro")],
                                 2., visual=True)
    assert len(placed) == 1 and placed[0][0] == 7. and placed[0][2] is None


def test_encoded_mix_makes_space_for_the_attack(tmp_path, monkeypatch):
    import subprocess
    import numpy as np
    from first_film_sound import level
    from score import sound
    import renderer, sound_library
    from timeline import Timeline
    sr = sound.AR
    t = np.arange(2*sr)/sr
    masker = .55 * np.sin(2*np.pi*600*t)
    cue = (.22 * np.sin(2*np.pi*1600*t[:sr]) * np.exp(-t[:sr]*110)).astype("float32")
    monkeypatch.setattr(sound, "library", lambda: {"fixture":{}})
    monkeypatch.setattr(sound, "sample_pcm", lambda sid:cue)
    fx, _ = level([dict(id="hit",s="fixture",db=-20,tier="hero",event_f=30)],masker,30,speaks=False)
    assert fx[0]["source_duck_db"] > 1
    paths = []
    for name, x in (("source",masker),("effect",cue)):
        path = tmp_path / (name + ".wav")
        import wave
        with wave.open(str(path),"wb") as f:
            f.setframerate(sr); f.setnchannels(1); f.setsampwidth(2)
            f.writeframes((x*32767).astype("<i2").tobytes())
        paths.append(path)
    result = []
    for name, depth in (("control",0),("mix",fx[0]["source_duck_db"])):
        item = {"storage_key":"fixture.wav","at":1., "dur_s":.8,
                "gain_db":fx[0]["db"], "source_duck_db":depth}
        edl = {"keep":[[0.,2.]], "sfx":[item]}
        graph = renderer.build_filtergraph(edl,2.,True,Timeline(edl["keep"]),None,[],{},True,
                 W=160,H=90,sfx_inputs=[(1,item,1.)],loudness=None)
        # Supply a tiny video input and encode the complete production mix as AAC.
        graph = graph.replace("[0:v]", "[2:v]") + ";[vout]nullsink"
        path = tmp_path / (name + ".m4a")
        subprocess.run(["ffmpeg","-v","error","-i",str(paths[0]),"-i",str(paths[1]),
                        "-f","lavfi","-i","color=s=160x90:r=30:d=2","-filter_complex",graph,
                        "-map","[aout]","-c:a","aac","-b:a","192k",str(path)],check=True,capture_output=True)
        raw = subprocess.check_output(["ffmpeg","-v","error","-i",str(path),"-ac","1","-ar",str(sr),"-f","f32le","-"])
        x = np.frombuffer(raw,np.float32)
        # Separate the cue and masker in a 50ms window at the event itself.
        window = x[sr:sr+sr//20]*np.hanning(sr//20)
        powers = abs(np.fft.rfft(window))**2
        cue_to_source = 10*np.log10(powers[76:85].sum()/powers[29:32].sum())
        result.append(cue_to_source)
    assert result[1] > result[0] + 4


def test_scene_render_dips_the_source_at_two_overlapping_cues(monkeypatch, tmp_path):
    import numpy as np
    from scene import audio
    from score.music import _Plan
    sr = audio.SR
    (tmp_path / "cue.flac").touch()
    monkeypatch.setattr(audio, "SOUNDS", str(tmp_path))
    monkeypatch.setattr(audio, "_filter", lambda: NS(swap=lambda item, root: (item, False)))
    monkeypatch.setattr(audio, "pcm", lambda path: np.full((sr*2, 2), .25, dtype=np.float32))
    cues = [dict(file="cue.flac", start_s=1., event_f=30, source_duck_db=depth) for depth in (7., 11.)]
    plan = _Plan(dict(fps=30, dur_f=60, spine=[{"f":[0, 60], "src":[0., 2.]}], sound=cues), "source")
    source, sounds, report = audio.split_audio(plan)
    assert report["sounds"] == 2 and sounds[sr, 0] == pytest.approx(.5)
    assert source[sr, 0] == pytest.approx(.25 * 10**(-11/20))
    assert source[sr//2, 0] == source[sr*3//2, 0] == .25


def test_arranged_music_makes_room_without_lowering_speech(monkeypatch):
    import numpy as np
    import first_film_sound
    from scene import audio
    from score import sound
    time = np.arange(2 * sound.AR) / sound.AR
    music = .55 * np.sin(2*np.pi*600*time)
    cue = (.22 * np.sin(2*np.pi*1600*time) * np.exp(-time*110)).astype("float32")
    monkeypatch.setattr(sound, "library", lambda: {"cue": {}})
    monkeypatch.setattr(sound, "sample_pcm", lambda sid: cue)
    sounds, _ = first_film_sound.level([dict(s="cue", db=-20, tier="hero", event_f=30)],
                                        music*.1, 30, speaks=True, bed=music)
    hit, = sounds
    assert "source_duck_db" not in hit and hit["music_duck_db"] > 4
    song = np.ones((2*audio.SR, 2), np.float32)
    audio.make_cue_space(song, [(1., hit["music_duck_db"])])
    assert song[audio.SR, 0] == pytest.approx(10**(-hit["music_duck_db"]/20))
    assert song[audio.SR//2, 0] == 1.


def test_landed_original_repair_keeps_original_raster(monkeypatch):
    import renderer
    monkeypatch.setattr(renderer.config,"APPROVAL_MAX_LONG_EDGE",1280)
    capture = {}
    def encode(db, job):
        capture["frame"] = renderer.preview_geometry(1080,1920,30)[:2]
        capture["scaled_again"] = renderer._needs_preview_downscale(1920)
    monkeypatch.setattr(renderer,"_run_render_job",encode)
    renderer.run_render_job(None,{"type":"preview","payload":{"quality":"approval","first_cut_original":True}})
    assert capture == {"frame":(1080,1920), "scaled_again":False}
    assert renderer._FIRST_CUT_ORIGINAL.get() is False


def test_phrase_push_pins_the_speakers_eyes_on_the_retained_source_clock(monkeypatch):
    import first_cut_visual as visual
    import device_planner
    edl = {"keep":[[20.,30.]], "frame":{"ratio":"9:16","mode":"pad"}}
    words = timed_words(["said,"]*60)
    ctx = NS(latest_edl=lambda:{"json":edl}, index={"video":{"width":1080,"height":1920},
             "spatial":{"samples":[{"t":24.5,"faces":[[.15,.14,.45,.44]]}]}})
    monkeypatch.setattr(device_planner,"program_words",lambda *_:words)
    monkeypatch.setattr(visual,"_move_room",lambda *_:1.)
    monkeypatch.setattr(visual,"plan_cut_ins",lambda *args,**kwargs:[(3.,6.,True)])
    result = []
    visual.cut_ins(ctx,[],0,lambda c,s,tool,args,st:result.append(args) or "EDL v3",[],(.5,.6),0)
    assert [result[0]["cx"],result[0]["cy"]] == pytest.approx([.3,.26])


def test_explicit_screen_fit_keeps_the_page_even_without_a_static_slide_measure(monkeypatch):
    import agent_tools
    ctx = NS(index={"_first_film_source_guard":True,"video":{"width":1920,"height":1080}})
    written = []
    monkeypatch.setattr(agent_tools,"set_frame",lambda c,ratio,mode,**kw:written.append((ratio,mode)) or "EDL v3")
    assert agent_tools._auto_reframe(ctx,"9:16","pad_blur") == "EDL v3"
    assert written == [("9:16","pad_blur")]


def test_screen_film_reaches_composition_and_player_with_unchanged_timeline(monkeypatch):
    from contextlib import nullcontext
    import first_cut, first_cut_spelling, first_cut_visual
    edl = {"frame": {"ratio": "9:16", "mode": "pad_blur"}, "keep": [[0, 5]]}
    ctx = NS(versions_written=0, latest_edl=lambda: {"json": edl}, first_cut_started=1.,
             index={"video": {"width": 1920, "height": 1080}}, first_cut_screen_label="Open the conversation",
             first_cut_spend={})
    tools = []
    monkeypatch.setattr(first_cut, "_text_wider_than_crop", lambda c: True)
    monkeypatch.setattr(first_cut, "_run", lambda c, s, tool, body, start: tools.append((tool, body)))
    monkeypatch.setattr(first_cut_visual, "screen_follow", lambda *args: tools.append(("screen_follow", {})))
    monkeypatch.setattr(first_cut_spelling, "persist", lambda c: None)
    monkeypatch.setattr(first_cut, "_clear_corner_mark", lambda *args: False)
    monkeypatch.setattr(first_cut, "_timed", lambda *args: nullcontext())
    monkeypatch.setattr(first_cut.agent_tools, "execute", lambda c, tool, body: tools.append((tool, body)) or "rendered")
    assert first_cut._render_cut(ctx, [], {"how": "director"}) == "rendered"
    assert [tool for tool, _ in tools] == ["add_text", "screen_follow", "render_preview"]
    assert tools[0][1]["text"] == "Open the conversation"


def test_native_song_has_headroom_in_social_master_aac(tmp_path):
    import subprocess
    import numpy as np
    import first_film_sound, renderer, schemas
    from timeline import Timeline

    # Both tones are present on the event. The completed AAC must retain
    # the foreground recording over the song even after the master limiter.
    rate = 48000
    edl = {"keep": [[0., 3.]], "master": {"loudness": "social"}}
    cue = {"storage_key": "click.wav", "id": "click", "at": 1., "gain_db": 0.}
    edl["sfx"] = [cue]
    proof = [{"peak50_over_background_db": 3.}]
    controls = [0., first_film_sound.recorded_music_trim(proof)]
    separation = []
    for i, trim in enumerate(controls):
        edl["master"]["recorded_music_gain_db"] = trim
        master = schemas.Master.model_validate(edl["master"])
        assert master.recorded_music_gain_db == trim
        graph = renderer.build_filtergraph(edl, 3., True, Timeline(edl["keep"]),
                    None, [], {}, True, W=160, H=90, sfx_inputs=[(1, cue, .15)],
                    dialogue_gain=[(0., 2.), (3., 2.)])
        graph = graph.replace("[0:v]", "[2:v]") + ";[vout]nullsink"
        path = tmp_path / f"mixed-{i}.m4a"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "aevalsrc=0.4*sin(2*PI*400*t):s=48000:d=3", "-f", "lavfi",
                        "-i", "aevalsrc=0.4*sin(2*PI*1800*t):s=48000:d=0.15",
                        "-f", "lavfi", "-i", "color=s=160x90:d=3", "-filter_complex", graph,
                        "-map", "[aout]", "-c:a", "aac", str(path)], check=True, capture_output=True)
        data = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path),
                        "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"])
        frames = np.frombuffer(data, np.float32)[rate+960:rate+3360]
        spectrum = abs(np.fft.rfft(frames*np.hanning(len(frames))))**2
        separation.append(10*np.log10(spectrum[89:92].sum()/spectrum[19:22].sum()))
    assert separation[1] > 6 and separation[1] > separation[0]+8
