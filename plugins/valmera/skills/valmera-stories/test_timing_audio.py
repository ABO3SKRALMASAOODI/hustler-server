"""A normal timed() t0/t1 repair. Only synthesis, listening and the heavy launcher are doubled."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf

import voice
import wf3
import lines
import narrate
import perform


def test_timing_rewrite_reads_only_the_changed_native_group(tmp_path, monkeypatch):
    sr = 48000
    generated, checked = [], []

    def render(args):
        # Deterministic native groups, with the same word, join and selection receipts as Phonon.
        groups = json.loads(Path(args.script).read_text())["segments"]
        generated.append([g["text"] for g in groups])
        out = Path(args.out)
        now, words, chunks, bounds, reports = 0.1, [], [], [], []
        for i, group in enumerate(groups):
            start = now
            for token in group["text"].split():
                words.append(dict(w=token, start=round(now, 3), end=round(now + .22, 3), seg=i, chunk=i))
                now += .25
            chunks.append(dict(seg=i, text=group["text"], start=start, end=words[-1]["end"]))
            bounds.append(dict(after_chunk=i, t=now+.12, pause_set=.25))
            now += .25
            take = dict(file=f"s{i:02d}_t0.wav", voice=[130., 900.], _ends=[140., 120.], errs=0)
            reports.append(dict(kept=take["file"], takes=[take], kept_voice=take["voice"], errs=0))
            perform.choose.__self__.picks.append(dict(file=take["file"]))
        x = np.zeros(round(now*sr), np.float32)
        for w in words:
            a, b = round(w["start"]*sr), round(w["end"]*sr)
            x[a:b] = .12 * np.sin(2*np.pi*120*np.arange(b-a)/sr)
        sf.write(out/"narration.wav", x, sr, subtype="PCM_24")
        (out/"words.json").write_text(json.dumps(dict(voice="phonon:Easton", duration=now, words=words, cut_points=[],
            phrases=[dict(text=c["text"], seg=c["seg"], start=c["start"], end=c["end"]) for c in chunks])))
        (out/"render.json").write_text(json.dumps(dict(engine="lines:phonon", voice="phonon:Easton", model=lines.PHONON_PIN["model"],
            band="03", lufs_target=-14, lines=reports, chunks=chunks, boundaries=bounds[:-1])))

    def launch(argv, **kw):
        if "render" in argv:
            i = argv.index("render")
            with monkeypatch.context() as m:
                for key in ("generate_phonon", "join_lines"):
                    m.setattr(lines, key, getattr(lines, key))
                for key in ("choose", "judge"):
                    m.setattr(perform, key, getattr(perform, key))
                if "STORIES_PREVIOUS_READ" in kw["env"]:
                    m.setenv("STORIES_PREVIOUS_READ", kw["env"]["STORIES_PREVIOUS_READ"])
                voice.render(SimpleNamespace(script=argv[i+1], out=argv[i+3], band=argv[i+5]))
        else:
            out = Path(argv[argv.index("check")+1])
            checked.append(json.loads((out/"words.json").read_text())["words"])
            (out/"check.json").write_text(json.dumps(dict(gate={"pass": True}, wer=dict(wer=0))))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(lines, "MODELS", tmp_path)
    monkeypatch.setattr(voice, "pace", lambda x, *_: (x, 1.0))
    monkeypatch.setattr(voice, "lift", lambda x, *_: (x, []))
    monkeypatch.setattr(narrate, "render", render)
    monkeypatch.setattr(narrate, "malloc_returns_memory", lambda: None)
    monkeypatch.setattr(wf3.PX, "subprocess", SimpleNamespace(run=launch))
    monkeypatch.setattr(wf3.PX, "NARRATE_LOCK", str(tmp_path/"lock"))
    monkeypatch.setenv("NARRATE_ENGINE", "phonon")
    monkeypatch.delenv("STORIES_PREVIOUS_READ", raising=False)
    hook = dict(say="Sony made the Walkman.", beat="hook")
    turn = dict(say="People carried it everywhere.", beat="turn")
    old = dict(segments=[hook, turn, dict(say=("The old close ran long. " * 5).strip(), beat="close")])
    new = dict(segments=[hook, turn, dict(say="The name stuck.", beat="close")])
    ctx = SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path/"supply"), tmp=str(tmp_path), args={}, run_id="test", log=print)
    wf3.timed(ctx, old, {}, [], rewrite=lambda *_: (new, [], [2]), final=lambda sc, rep: (sc, rep))
    assert generated == [[hook["say"], turn["say"]+" "+old["segments"][2]["say"]], [turn["say"]+" The name stuck."]]
    assert len(checked) == 2 and checked[0][:4] == checked[1][:4]
    rep = json.loads((Path(ctx.supply)/"narration"/"render.json").read_text())
    assert rep["stories_read"]["timing_repair"]["reused_groups"] == [0, None]


def test_successful_cli_render_keeps_take_count_out_of_exit_status(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "argv", ["voice.py", "render", "script", "--out", str(tmp_path)])
    monkeypatch.setattr(voice, "render", lambda args: 48)
    monkeypatch.setattr(narrate, "malloc_returns_memory", lambda: None)
    monkeypatch.setattr(voice.os, "chdir", lambda path: None)
    assert (voice.main() or 0) == 0
