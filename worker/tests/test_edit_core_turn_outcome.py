"""EDIT-CORE lane B (16 Nov 2026): the turn reaches the outcome and says what really changed.

Owner's rule: a fix removes the real error at its source so the product reaches the customer's
outcome; nothing here asserts a refusal.
"""
import json
import pytest
from types import SimpleNamespace as NS

import agent_loop
from score import asks


class _Ctx(NS):
    def latest_edl(self):
        return {"version": self.version, "json": self.edl}


def _ledger():
    led = asks.settle({"lang": "en", "asks": [{"said": "warm, sun-touched film look", "want": "a warm film look",
                                               "kind": "edit", "items": [], "how": "look", "where": "",
                                               "pass": ""}]}, {"keep": [[0.0, 35.0]]})
    led["cfg"] = {"check": True}
    return led


# ------------------------------------------------------------------ B2: no repair-pass cap

def test_the_turn_keeps_repairing_while_each_pass_changes_the_film(monkeypatch):
    """p5873 job 138856 replied "I stopped there because the one allowed repair pass has been used".
    Every pass that changes the film gets the check again, as many times as the job's time allows."""
    ledger = _ledger()
    ctx = _Ctx(edl={"keep": [[0.0, 35.0]]}, version=7, ask_ledger=ledger, ask_verdict=None,
               last_preview={"edl_version": 7, "asset_id": 1}, versions_written=[7])
    open_verdict = {"asks": [{"id": "a1", "state": "partly", "seen": "grain is missing", "line": "l"}],
                    "unasked": [], "usage": {}}
    monkeypatch.setattr(asks, "check", lambda *a, **k: dict(open_verdict))
    monkeypatch.setattr(agent_loop, "_turn_change_list", lambda c: "")
    notes = []
    for version in (7, 8, 9, 10, 11):
        ctx.version = version
        ctx.last_preview = {"edl_version": version, "asset_id": version}
        note = agent_loop._asks_check(ctx, f"draft {version}")
        assert note, f"pass on v{version} got no repair note"
        notes.append(note)
    assert ledger["repairs"] == 5
    for note in notes:
        assert "one repair pass" not in note and "check reads the new render again" in note


def test_a_pass_that_changed_nothing_is_the_editors_answer_and_the_reply_stands(monkeypatch):
    ledger = _ledger()
    ctx = _Ctx(edl={"keep": [[0.0, 35.0]]}, version=7, ask_ledger=ledger, ask_verdict=None,
               last_preview={"edl_version": 7, "asset_id": 1}, versions_written=[7])
    monkeypatch.setattr(asks, "check", lambda *a, **k: {
        "asks": [{"id": "a1", "state": "partly", "seen": "s", "line": "l"}], "unasked": [], "usage": {}})
    monkeypatch.setattr(agent_loop, "_turn_change_list", lambda c: "")
    assert agent_loop._asks_check(ctx, "first draft")
    # same version, same render: the editor replied without changing the film
    assert agent_loop._asks_check(ctx, "second draft") is None
    assert ledger["repairs"] == 1


def test_no_count_setting_caps_the_repairs():
    cfg = asks.parse(json.dumps({"mode": "on", "repairs": 0}))
    assert "repairs" not in cfg
    assert "repairs" not in asks.DEFAULTS


# ------------------------------------------------------------------ B1: provenance and the measured change
# The EDL versions of the 16 Nov golden replays (ecg-new-pro-1116, journeys p5744 and p5717), as the
# journeys record them, and the uploads' own sound as perception.analyze_audio measures the corpus
# media (p5744 walk: bpm_conf 0.37, -45.7 dB RMS; p5717 music video: bpm_conf 0.65).

import edit_provenance

P5744_INDEX = {"video": {"duration": 11.264, "width": 1080, "height": 1920, "has_audio": True},
               "words": [{"w": "Yeah", "t0": 0.9, "t1": 1.2}],
               "perception": {"bpm": 70.6, "bpm_conf": 0.37}}
P5744_V1 = {"keep": [[0.0, 11.264]]}
P5744_V3 = {"keep": [[0.0, 11.23]],
            "sfx": [{"id": "s1", "storage_key": "sound:riser_soft", "at": 0.67, "role": "riser"},
                    {"id": "s2", "storage_key": "sound:impact_low", "at": 1.98, "role": "impact"}]}
P5744_V6 = {"keep": [[1.0, 2.98]]}
# v1 as production stores it: the indexer writes the upload's EDL as 'agent' (indexer.py
# insert_edl(default_edl, 'agent')), 219 of 231 projects in the 14 days to 16 Nov.
P5744_ROWS = [{"version": 1, "created_by": "agent", "json": P5744_V1},
              {"version": 2, "created_by": "agent", "json": dict(P5744_V1, sfx=P5744_V3["sfx"])},
              {"version": 3, "created_by": "agent", "json": P5744_V3},
              {"version": 4, "created_by": "agent", "json": {"keep": [[1.0, 11.23]], "sfx": P5744_V3["sfx"]}},
              {"version": 5, "created_by": "agent", "json": {"keep": [[1.0, 2.98]], "sfx": P5744_V3["sfx"]}},
              {"version": 6, "created_by": "agent", "json": P5744_V6}]
P5744_JOBS = [{"id": 11, "first_cut": True, "before_version": 1, "after_version": 3},
              {"id": 14, "first_cut": False, "before_version": 3, "after_version": 6}]

P5717_INDEX = {"video": {"duration": 92.88, "width": 1280, "height": 720, "has_audio": True},
               "words": [{"w": w, "t0": i, "t1": i + .3} for i, w in enumerate("yeah uh huh go go go now".split())],
               "perception": {"bpm": 129.4, "bpm_conf": 0.65}}
P5717_V9 = {"keep": [[3.1 + 5 * i, 3.1 + 5 * i + 0.352] for i in range(18)],
            "frame": {"ratio": "9:16", "mode": "crop"},
            "texts": [{"id": "t1", "text": "PARTY NIGHT", "start": 0.0, "end": 1.6}],
            "sfx": [{"id": f"s{i}", "storage_key": "sound:whoosh", "at": 0.35 * i} for i in range(10)]}
P5717_ROWS = [{"version": 1, "created_by": "agent", "json": {"keep": [[0.0, 92.88]]}}] + [
    {"version": v, "created_by": "agent", "json": P5717_V9} for v in range(2, 10)]
P5717_JOBS = [{"id": 5, "first_cut": True, "before_version": 1, "after_version": 9}]


def test_the_upload_sound_is_measured_not_assumed():
    """p5744: the first cut said the walk 'plays over its song'. A low beat confidence says only
    that no steady beat was measured (a soft bed or piano reads low too), never that there is no
    music."""
    line, music = edit_provenance.upload_sound(P5744_INDEX)
    assert music is None and "no steady beat measured (beat confidence 0.37)" in line
    assert "no music" not in line
    assert edit_provenance.upload_has_music(P5744_INDEX) is False
    assert edit_provenance.upload_has_music(P5717_INDEX) is True
    import first_cut
    import inspect
    src = inspect.getsource(first_cut.video_chain)
    assert "edit_provenance.upload_has_music(ctx.index)" in src


def test_p5744_the_first_cuts_sound_effects_are_never_the_uploads():
    """p5744 R1 said "I removed two sound effects that were in the original": they were the first
    cut's. The editor now reads who placed every item, and that the rest of the sound is the upload's."""
    text = edit_provenance.from_lineage(P5744_ROWS[:3], P5744_JOBS[:1], index=P5744_INDEX, source_s=11.264)
    assert ("v2-v3, the automatic first cut (Valmera, before the customer asked anything): "
            "re-timed the kept sections; added 2 sound effects") in text
    assert ("In the film now, placed by the automatic first cut: sound effect s1 'riser' at 0.67 s; "
            "sound effect s2 'impact' at 1.98 s.") in text
    assert "never call Valmera's additions part of their upload" in text
    assert "no steady beat measured" in text
    # v1 is the upload as it came in, though production stores it as 'agent'
    assert "- The upload: 11.26 s; its own sound:" in text
    assert "v1," not in text and "v1-" not in text


def test_p5744_the_turns_change_is_measured_from_its_own_versions():
    line = edit_provenance.turn_change(P5744_V3, P5744_V6, 3, 6)
    assert line.startswith("THIS TURN, MEASURED (v3 -> v6): cut 9.2s (kept 2.0s of the source)")
    assert "removed 2 sound effects" in line and "the film plays 1.98 s (it played 11.23 s" in line
    # and the lineage tells the editor whose effects those were
    text = edit_provenance.from_lineage(P5744_ROWS, P5744_JOBS, index=P5744_INDEX, source_s=11.264,
                                        turn_versions=[4, 5, 6])
    assert "v4-v6, this turn: cut 9.2s (kept 2.0s of the source); removed 2 sound effects" in text
    assert "v2-v3, the automatic first cut" in text


def test_p5717_a_turn_that_wrote_nothing_is_told_so_and_the_cut_is_the_first_cuts():
    """p5717 R1: "Nothing in the edit changed ... I have cut the silences". The cut 92.9 s -> 6.3 s
    was the automatic first cut's; the turn wrote nothing."""
    text = edit_provenance.from_lineage(P5717_ROWS, P5717_JOBS, index=P5717_INDEX, source_s=92.88)
    assert ("v2-v9, the automatic first cut (Valmera, before the customer asked anything): "
            "cut 86.5s (kept 6.3s of the source)") in text
    assert "- The upload: 92.88 s" in text and "v1," not in text
    assert "placed by the automatic first cut: sound effect s0 'whoosh' at 0.00 s" in text
    assert "title t1 'PARTY NIGHT' at 0.00 s." in text
    assert "a steady beat measured (confidence 0.65)" in text
    line = edit_provenance.turn_change(P5717_V9, P5717_V9, 9, 9)
    assert "nothing in the film has changed this turn" in line
    assert "Whatever the film already shows was made before this turn" in line


# p5711 (a golden request): 44 versions, the automatic first cut v2-v20, then two requests. The
# newest 24 versions start at v21, so the first cut was diffed against the whole upload and
# credited to "an earlier request (job 138107)".
P5711_UPLOAD = {"keep": [[0.0, 60.0]]}
P5711_FIRST_CUT = {"keep": [[2.0, 12.0], [30.0, 40.0]],
                   "captions": {"style": "bold"},
                   "music": [{"id": "m1", "storage_key": "music:upbeat", "start": 0.0}],
                   "effects": {"zooms": [{"id": "zm2", "start": 3.0}]},
                   "sfx": [{"id": "s1", "storage_key": "sound:whoosh", "at": 1.0}]}
P5711_REQUEST = dict(P5711_FIRST_CUT, keep=[[2.0, 12.0], [30.0, 35.0]])
P5711_LAST = dict(P5711_REQUEST, texts=[{"id": "t9", "text": "THE END", "start": 14.0}])
P5711_VERSIONS = ([{"version": 1, "created_by": "agent", "json": P5711_UPLOAD}]
                  + [{"version": v, "created_by": "agent", "json": P5711_FIRST_CUT} for v in range(2, 21)]
                  + [{"version": v, "created_by": "agent", "json": P5711_REQUEST} for v in range(21, 34)]
                  + [{"version": v, "created_by": "agent", "json": P5711_LAST} for v in range(34, 45)])
P5711_JOBS = [{"id": 138100, "first_cut": True, "before_version": 1, "after_version": 20},
              {"id": 138107, "first_cut": False, "before_version": 20, "after_version": 33},
              {"id": 138120, "first_cut": False, "before_version": 33, "after_version": 44}]


class _LineageConn:
    """A cursor over one project's edls and receipts, answering edl_lineage's three reads."""

    def __init__(self, versions, jobs):
        self.versions, self.jobs, self.loaded = versions, jobs, None

    def cursor(self):
        conn = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, args):
                self.sql, self.args = sql, args

            def fetchall(self):
                if "video_jobs" in self.sql:
                    return [dict(j) for j in conn.jobs]
                _pid, window_from, wanted = self.args
                conn.loaded = [r["version"] for r in conn.versions
                               if r["version"] >= window_from or r["version"] in wanted]
                return [dict(r) for r in conn.versions if r["version"] in conn.loaded]

            def fetchone(self):
                vs = [r["version"] for r in conn.versions]
                return {"low": min(vs), "high": max(vs)}
        return Cur()


def test_p5711_a_first_cut_older_than_the_newest_versions_is_still_the_first_cuts():
    import db as dbx
    conn = _LineageConn(P5711_VERSIONS, P5711_JOBS)
    rows, jobs = dbx.edl_lineage(conn, 5711)
    assert conn.loaded == [1, 20] + list(range(21, 45))       # the boundaries and the newest 24
    text = edit_provenance.from_lineage(rows, jobs, index={"video": {"duration": 60.0}}, source_s=60.0)
    assert ("v2-v20, the automatic first cut (Valmera, before the customer asked anything): "
            "cut 40.0s (kept 20.0s of the source)") in text
    assert "v21-v33, an earlier request (job 138107): cut 5.0s" in text
    assert "changed the music" not in text.split("v21-v33", 1)[1].split("\n", 1)[0]
    assert "v34-v44, an earlier request (job 138120): added 1 title" in text
    first_cuts = text.split("placed by the automatic first cut: ", 1)[1].split("\n", 1)[0]
    assert "music track m1 'upbeat'" in first_cuts and "zoom zm2 at 3.00 s" in first_cuts
    assert "sound effect s1 'whoosh' at 1.00 s" in first_cuts
    assert "placed by an earlier request (job 138120): title t9 'THE END'" in text
    assert "placed by an earlier request (job 138107)" not in text


def test_versions_no_receipt_covers_are_named_earlier_history_not_a_request():
    """Without a receipt for the gap the span is earlier history, never a diff against the whole
    upload credited to whoever wrote the first loaded version."""
    rows = [P5711_VERSIONS[0]] + P5711_VERSIONS[29:]           # v1, then v30-v44 loaded
    jobs = [P5711_JOBS[2]]                                    # only the last request's receipt
    text = edit_provenance.from_lineage(rows, jobs, source_s=60.0)
    assert "v2-v30, earlier history (versions not loaded here): cut 45.0s" in text
    assert "v34-v44, an earlier request (job 138120): added 1 title" in text
    assert "an earlier request (job 138107)" not in text
    assert "placed by earlier history (versions not loaded here): sound effect s1" in text
    assert "placed by the upload" not in text


def test_a_receipted_span_that_is_not_loaded_is_named_by_its_jobs():
    rows = [P5711_VERSIONS[0], P5711_VERSIONS[19]] + P5711_VERSIONS[40:]   # v1, v20, v41-v44
    text = edit_provenance.from_lineage(rows, P5711_JOBS, source_s=60.0)
    assert "v2-v20, the automatic first cut" in text
    assert ("v21-v41, an earlier request (job 138107), then an earlier request (job 138120): "
            "cut 5.0s") in text


def test_p5744_the_first_cut_reply_names_the_sound_effects_it_laid(monkeypatch, tmp_path):
    """p5744: the first cut kept the walk whole and its sound design laid a riser and an impact,
    but the reply said the film played "with its own sound"; the customer then called them
    "two sound effects that were in the original". The reply now names what the cut laid."""
    import inspect
    import agent_tools
    import claude_trial
    import config
    import db as dbx
    import first_cut
    import first_cut_visual
    assert "with its own sound" not in inspect.getsource(first_cut.video_chain)
    said = []
    state = {"version": 1, "json": dict(P5744_V1)}

    class Db:
        def run(self, fn, *args, **kw):
            if fn is dbx.add_message:
                said.append(args)
            if fn is dbx.user_billing:
                return False, "free", False
            return None

    class Ctx(NS):
        def latest_edl(self):
            return dict(state)

    ctx = Ctx(duration=11.264, project_id=1, index=dict(P5744_INDEX), versions_written=[],
              last_preview=None)
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(agent_tools, "ToolContext", lambda *a: ctx)
    monkeypatch.setattr(claude_trial, "resolve", lambda *a, **k:
                        claude_trial.Decision(claude_trial.Decision.NOT_TRIAL))
    monkeypatch.setattr(claude_trial, "credit_status", lambda *a, **k: {})
    monkeypatch.setattr(first_cut_visual, "engine_on", lambda *a: False)
    monkeypatch.setattr(first_cut, "bound_to_source_screen", lambda *a: True)
    monkeypatch.setattr(first_cut, "project_admission_safe", lambda *a: None)
    monkeypatch.setattr(first_cut, "video_chain", lambda *a, **k: (
        [], ["kept whole (it already plays as a finished piece, in its own time)"],
        {"how": "finished_piece"}))

    def render_cut(c, steps, picked):
        # first_film_sound.chain's write (p5744 v2-v3), then the preview of it
        state.update(version=3, json=dict(P5744_V3))
        c.last_preview = {"edl_version": 3, "duration_s": 11.23}
        return "ok"
    monkeypatch.setattr(first_cut, "_render_cut", render_cut)
    first_cut.run(Db(), {"id": 2, "user_id": 3, "project_id": 1, "payload": {}},
                  {"id": 1, "chat_session_id": 9}, {"json": P5744_INDEX})
    text = said[-1][2]
    assert text.startswith("Here's your first cut: an 11-second vertical version: kept whole")
    assert "2 sound effects added: a riser at 0.67 s and an impact at 1.98 s" in text
    assert "its own sound" not in text


def test_the_state_block_carries_the_provenance(monkeypatch):
    import db as dbx

    class DB:
        def run(self, fn, *a):
            assert fn is dbx.edl_lineage
            return P5744_ROWS, P5744_JOBS
    ctx = NS(project_id=2, index=P5744_INDEX, duration=11.264, versions_written=[])
    text = agent_loop._provenance_lines(ctx, DB())
    assert "WHERE THE EDIT CAME FROM" in text and "v2-v3, the automatic first cut" in text
    assert "v4-v6, an earlier request (job 14): cut 9.2s (kept 2.0s of the source); removed 2 sound effects" in text


def test_the_write_result_carries_the_turns_measured_change():
    from w0c_support import tool_context
    ctx, fake = tool_context(dict(P5744_V3), duration=11.264)
    start = ctx.latest_edl()
    ctx.turn_start_edl = {"version": start["version"], "json": dict(start["json"])}
    out = ctx.write_edl(dict(start["json"], keep=[[1.0, 2.98]], sfx=[]), "tighten")
    assert out.startswith("EDL v"), out
    assert "THIS TURN, MEASURED" in out and "removed 2 sound effects" in out


def test_no_reply_is_replaced_by_a_hedge():
    """The stand-ins that replaced whole replies on p5744 and p5717 are gone from the reply path."""
    import inspect
    import edit_scope
    import reply_truth
    assert "_call(" not in inspect.getsource(edit_scope.disclose)      # no rewrite model
    assert asks.checked_reply(None, None) == ""
    assert reply_truth._unverified({}, "My reply.") == "My reply."


# ------------------------------------------------------------------ B4: long-film previews
# p5857 (81 min source, 59 min cut): preview jobs of 332-694 s on the box (encode 86-426 s, then
# picture/audio QC 105 s, sheets 70 s, verification 55 s, peak guard 15 s); a turn spent ~35 min in
# six of them. The final export is untouched by everything below.

import subprocess

import config
import media
import renderer
import sheets
import audio_qc


def test_a_long_programmes_draft_is_drawn_smaller_and_nothing_else_is():
    long_edl = {"keep": [[0.0, 1800.0], [2000.0, 3765.4]]}
    assert renderer.long_program(long_edl) and not renderer.long_program({"keep": [[0.0, 600.0]]})
    q = renderer._PREVIEW_QUALITY.set("draft")
    try:
        assert renderer.preview_geometry(960, 540, 29.97)[:2] == (854, 480)
        t = renderer._PREVIEW_LONG.set(True)
        try:
            assert renderer.preview_geometry(960, 540, 29.97)[:2] == (640, 360)
            assert renderer.preview_geometry(1080, 1920, 30.0)[:2] == (202, 360)
            a = renderer._PREVIEW_QUALITY.set("approval")
            try:   # the approval render keeps its frame
                assert renderer.preview_geometry(960, 540, 29.97)[:2] == (960, 540)
            finally:
                renderer._PREVIEW_QUALITY.reset(a)
        finally:
            renderer._PREVIEW_LONG.reset(t)
    finally:
        renderer._PREVIEW_QUALITY.reset(q)


def test_sparse_stills_of_a_long_render_are_read_by_seeking(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "duration_of", lambda p: 3565.4)
    monkeypatch.setattr(media, "frames_at", lambda *a, **k: pytest.fail("whole-file decode"))
    seen = []

    def frame_at(src, t, dst, width=None, **k):
        seen.append(round(t, 1))
        open(dst, "wb").write(b"jpg")
    monkeypatch.setattr(media, "frame_at", frame_at)
    times = sheets.result_sheet_times(3565.4) + [3565.4 * (i + .5) / 32 for i in range(32)]
    video = tmp_path / "preview.mp4"
    video.write_bytes(b"x")
    assert sheets.prefetch(str(video), times, str(tmp_path / "stills")) == len(times)
    assert len(seen) == len(times)
    sheets.release(str(video))


def test_a_short_render_keeps_the_one_decode(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "duration_of", lambda p: 40.0)
    called = []
    monkeypatch.setattr(media, "frames_at", lambda src, items, width=None: called.append(len(items)) or set())
    video = tmp_path / "short.mp4"
    video.write_bytes(b"x")
    sheets.prefetch(str(video), [i * 0.9 for i in range(40)], str(tmp_path / "stills"))
    assert called == [40]


def test_the_black_check_of_a_long_render_reads_keyframes(monkeypatch):
    cmds = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: cmds.append(cmd) or NS(stderr="", returncode=0))
    media.black_seconds("long.mp4", 3565.4)
    media.black_seconds("short.mp4", 40.0)
    assert cmds[0][:3] == ["ffmpeg", "-skip_frame", "nokey"] and "fps=4" not in " ".join(cmds[0])
    assert "-skip_frame" not in cmds[1] and "fps=4,scale=64:-2" in " ".join(cmds[1])


def _ffmpeg_ok():
    try:
        return subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode == 0
    except OSError:
        return False


@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg not on PATH")
def test_one_audio_pass_gives_loudness_peak_and_silences(tmp_path):
    path = str(tmp_path / "tone.m4a")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=6",
                    "-af", "volume=-12dB,apad=pad_dur=3", "-t", "9", "-c:a", "aac", path], check=True)
    err = audio_qc.analysis(path)
    rec = audio_qc.measure(path, duration_s=9.0, analysis=err)
    assert rec["i"] is not None and -45 < rec["i"] < -20     # a -30 dBFS tone, measured
    assert rec["tp"] is not None and abs(rec["tp"] - audio_qc.true_peak_of(err)) < 1e-6
    import true_peak
    assert abs(true_peak.measure(path) - rec["tp"]) < 0.2      # the guard's own meter agrees
    silent = str(tmp_path / "silent.m4a")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-t", "4", "-c:a", "aac", silent], check=True)
    assert audio_qc.measure(silent, duration_s=4.0)["i"] == -100.0


def test_the_render_job_measures_a_previews_sound_once():
    import inspect
    src = inspect.getsource(renderer._run_render_job)
    assert "measured=audio_qc.true_peak_of(qc_pass)" in src
    assert "analysis=qc_pass" in src


@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg not on PATH")
def test_a_preview_starting_at_the_head_reads_only_its_kept_stretches(tmp_path, monkeypatch):
    """p5857: keeps from 0 s with 5-8 minute stretches cut; the preview decoded the whole source."""
    src = str(tmp_path / "src.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30",
                    "-f", "lavfi", "-i", "sine=f=330", "-t", "130", "-c:v", "libx264", "-preset",
                    "ultrafast", "-g", "30", "-c:a", "aac", "-shortest", src], check=True)
    edl = {"keep": [[0.0, 6.0], [70.0, 76.0], [120.0, 126.0]]}
    info = media.probe(src)
    index = {"video": {"duration": info["duration"], "width": 160, "height": 90, "fps": 30,
                       "has_audio": True}}
    seen = []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, cmd, *a, **k):
            if isinstance(cmd, list) and "-filter_complex" in cmd:
                seen.append(cmd)
            super().__init__(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", Spy)
    out = str(tmp_path / "preview.mp4")
    dur = renderer.render_edl(edl, index, src, out, str(tmp_path), preview=True)
    assert abs(dur - 18.0) < 0.2 and abs(media.duration_of(out) - 18.0) < 0.3
    cmd = " ".join(seen[-1])
    assert "-ss 69.000" in cmd and "-ss 119.000" in cmd       # each distant stretch read on its own
    assert "-threads:v 2" not in cmd                          # a small frame decodes on ffmpeg's own count
    # the frames are the ones the whole-source read gives
    monkeypatch.setattr(config, "RENDER_SEEK_DISTANT_KEEPS", False)
    ref = str(tmp_path / "ref.mp4")
    renderer.render_edl(edl, index, src, ref, str(tmp_path), preview=True)
    assert "-ss 69.000" not in " ".join(seen[-1])
    psnr = subprocess.run(["ffmpeg", "-v", "info", "-i", out, "-i", ref, "-lavfi", "psnr", "-f", "null", "-"],
                          capture_output=True, text=True).stderr
    avg = float(psnr.rsplit("average:", 1)[1].split()[0]) if "average:" in psnr else 0.0
    assert avg == float("inf") or avg > 45, psnr[-300:]


def _psnr(a, b):
    """Average PSNR of b against a, b drawn at a's size (the export is delivered larger)."""
    out = subprocess.run(["ffmpeg", "-v", "info", "-i", a, "-i", b, "-lavfi",
                          "[1:v][0:v]scale2ref[b][a];[a][b]psnr", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(out.rsplit("average:", 1)[1].split()[0]) if "average:" in out else 0.0


@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg not on PATH")
@pytest.mark.parametrize("keep", [[[0.0, 6.0], [70.0, 76.0], [120.0, 126.0]],
                                  [[20.0, 26.0], [70.0, 76.0], [120.0, 126.0]]])
def test_a_seeking_preview_of_an_offset_source_plays_the_exports_frames(tmp_path, monkeypatch, keep):
    """A source whose container starts at 1.48 s: the seeking path read the container clock, so it
    played 16.5 s of an 18 s cut and its frames left the export's from 5 s on. It now reads the
    clock the plain read gives, so the preview, the export and the plain read show the same film."""
    src = str(tmp_path / "src.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30",
                    "-f", "lavfi", "-i", "sine=f=330", "-t", "130", "-c:v", "libx264", "-preset",
                    "ultrafast", "-g", "30", "-c:a", "aac", "-shortest", "-output_ts_offset", "1.48",
                    src], check=True)
    info = media.probe(src)
    assert abs(float(info.get("format_start") or 0) - 1.48) < 0.05, info.get("format_start")
    edl = {"keep": keep}
    index = {"video": {"duration": info["duration"], "width": 160, "height": 90, "fps": 30,
                       "has_audio": True}}
    seen = []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, cmd, *a, **k):
            if isinstance(cmd, list) and "-filter_complex" in cmd:
                seen.append(cmd)
            super().__init__(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", Spy)
    seeking = str(tmp_path / "seeking.mp4")
    renderer.render_edl(edl, index, src, seeking, str(tmp_path), preview=True, suppress_outro=True)
    assert "-copyts" in seen[-1]                                 # the seeking path ran
    final = str(tmp_path / "final.mp4")
    renderer.render_edl(edl, index, src, final, str(tmp_path), preview=False, suppress_outro=True)
    assert abs(media.duration_of(seeking) - 18.0) < 0.15, media.duration_of(seeking)
    assert abs(media.duration_of(final) - 18.0) < 0.15
    # the export's frames (misread, the two measured 16-18 dB from 5 s on)
    assert _psnr(seeking, final) > 28, _psnr(seeking, final)
    if keep[0][0] == 0.0:
        monkeypatch.setattr(config, "RENDER_SEEK_DISTANT_KEEPS", False)
        plain = str(tmp_path / "plain.mp4")
        renderer.render_edl(edl, index, src, plain, str(tmp_path), preview=True, suppress_outro=True)
        assert "-copyts" not in seen[-1]
        assert _psnr(seeking, plain) > 45


def test_silence_gaps_say_whether_the_current_film_still_holds_them(monkeypatch):
    """p5717 R1 offered to trim "the 11.4 s gap between 13.1 s and 24.5 s" of a film the first cut
    had already reduced to 6.3 s. Each gap now says where it is in the film, or that it was cut."""
    import agent_tools
    edl = {"keep": [[3.0, 6.0], [30.0, 33.0]]}
    ctx = NS(index={"words": [{"w": "go", "t0": 2.0, "t1": 2.5}, {"w": "now", "t0": 40.0, "t1": 40.4}]},
             latest_edl=lambda: {"version": 9, "json": edl}, duration=92.88)
    monkeypatch.setattr(agent_tools, "_dead_air", lambda c, m: (
        [{"start": 13.1, "end": 24.5, "quiet_frac": 1.0}, {"start": 31.0, "end": 32.0, "quiet_frac": 1.0}], "speech"))
    out = agent_tools.find_silences(ctx, 0.7)
    lines = out.splitlines()
    assert "not in the current film: the edit already cut it" in next(l for l in lines if l.startswith("13.10"))
    assert next(l for l in lines if l.startswith("31.00")).endswith("[in the film at output 4.00-5.00]")
