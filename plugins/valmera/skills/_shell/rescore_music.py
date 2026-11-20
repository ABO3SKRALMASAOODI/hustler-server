"""A delivered film's music, chosen again without remaking the film (owner, 20 Nov 2026: "the music is really a disco
music on a Mark Cuban short ... the music should really give you some sort of goosebumps").

    rescore_music.py <film folder> <new folder> [--used cid,cid] [--kind podcast] [--no-model]

Reads the delivered folder (film_clean.mp4, the film without music, and film.json) and writes a NEW folder: a copy of
film.json and film_clean.mp4, a new film.mp4 with music chosen again for this film, and rescore.json with what was
chosen, why, what the model was shown and what was measured. The delivered folder is only read: nothing in it is
written, moved or deleted, and the new folder must be somewhere else.

How the music is chosen: the same path a workflow film takes now (worker/music_film.choose): the reference palette at
.valmera/models/music_ref, a model reading the film's own lines (heard again here from film_clean.mp4 with local
faster-whisper), its turn and payoff (the ones the delivered film recorded), the person and the mood, ranks every
described track by the story; the engine (score.music) finds the stretch of each that lands on the payoff and
auditions them in that order, then arranges and levels the chosen one under the film's own sound, held on the story's
own arc (the model's reading of how far under the voice each part sits, and where the close starts), as a workflow
film's bed is. The picture is
copied byte for byte (the end card and the watermark are the delivered ones); only the sound is new. Model calls go
through the local codex-proxy (the workflows' own route, no API spend); no listening model is used anywhere.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

V = "/Users/masaoodi/Documents/Valmera"
VV = f"{V}/.valmera"
MUSIC_SET = f"{VV}/models/music_ref"
PROXY_ENV = f"{VV}/tools/codex-proxy/env.sh"
PY = f"{VV}/venv-worker/bin/python"
KIND = {"wf1": "podcast", "wf2": "marketing", "wf3": "stories", "wf4": "longform"}
FPS = 30.0


def _ff():
    import brand
    return brand.FF


def heard_lines(film, dur_s, cache_path):
    """The film's words on its own clock, heard with local faster-whisper: (words [(a, b)], lines [(second, text)])."""
    if os.path.exists(cache_path):
        got = json.load(open(cache_path))
        return [tuple(w) for w in got["words"]], [tuple(x) for x in got["lines"]]
    os.environ.setdefault("HF_HUB_OFFLINE", "1")           # the cached local model; nothing is fetched
    from faster_whisper import WhisperModel
    import numpy as np
    r = subprocess.run([_ff(), "-v", "error", "-i", film, "-t", f"{dur_s:.3f}", "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                       capture_output=True, check=True)
    pcm = np.frombuffer(r.stdout, np.float32).copy()          # handed as samples: no container decoding inside whisper
    model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
    segs, _info = model.transcribe(pcm, word_timestamps=True, vad_filter=True)
    words, lines = [], []
    for s in segs:
        ws = [w for w in (s.words or []) if str(w.word).strip()]
        words += [(round(float(w.start), 3), round(float(w.end), 3)) for w in ws]
        if ws:
            lines.append((round(float(ws[0].start), 2), " ".join(str(w.word).strip() for w in ws)))
    json.dump({"words": words, "lines": lines}, open(cache_path, "w"), ensure_ascii=False, indent=1)
    return words, lines


def picture_cuts(doc, dur_s):
    """The film's picture changes on its own clock, from what film.json records of its pictures."""
    rows = ((doc.get("checks") or {}).get("stories") or {}).get("pictures") or ((doc.get("checks") or {}).get("podcast") or {}).get("sources") or []
    cuts = sorted({round(float(r["f"][0]) / FPS, 3) for r in rows if isinstance(r, dict) and isinstance(r.get("f"), list) and r["f"]})
    return [c for c in cuts if 0.0 < c < dur_s]


def video_md5(path):
    r = subprocess.run([_ff(), "-v", "error", "-i", path, "-map", "0:v:0", "-c", "copy", "-f", "md5", "-"], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def lufs_of(path, t0=None, t1=None):
    cmd = [_ff(), "-v", "info", "-nostats"]
    if t0 is not None:
        cmd += ["-ss", f"{t0:.3f}"]
    cmd += ["-i", path]
    if t1 is not None:
        cmd += ["-t", f"{t1 - (t0 or 0):.3f}"]
    r = subprocess.run(cmd + ["-vn", "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True)
    for line in r.stderr.splitlines()[::-1]:
        s = line.strip()
        if s.startswith("I:") and "LUFS" in s:
            return float(s.split()[1])
    return None


def bed_measures(film, clean, dur_s, payoff_s, turn_s):
    """The music as it plays in the new film (the new film minus the clean one, sample aligned): its level under the
    voice, how it moves into the payoff, and how beat-led it is (kick share on the low band)."""
    import numpy as np

    def pcm(p):
        r = subprocess.run([_ff(), "-v", "error", "-i", p, "-t", f"{dur_s:.3f}", "-vn", "-ac", "1", "-ar", "24000", "-f", "f32le", "-"],
                           capture_output=True, check=True)
        return np.frombuffer(r.stdout, np.float32)
    a, b = pcm(film), pcm(clean)
    n = min(len(a), len(b))
    bed, voice = a[:n] - b[:n], b[:n]
    hop = 12000                                              # 0.5 s
    k = n // hop

    def db(x):
        return 10 * np.log10(np.mean(x[:k * hop].reshape(k, hop).astype(np.float64) ** 2, axis=1) + 1e-12)
    bd, vd = db(bed), db(voice)
    t = (np.arange(k) + 0.5) * 0.5
    playing = bd > bd.max() - 30
    out = {"bed_db_median": round(float(np.median(bd[playing])), 1) if playing.any() else None,
           "bed_under_voice_db": round(float(np.median(vd[playing & (vd > vd.max() - 25)] - bd[playing & (vd > vd.max() - 25)])), 1) if (playing & (vd > vd.max() - 25)).any() else None,
           "bed_spread_db": round(float(np.percentile(bd[playing], 90) - np.percentile(bd[playing], 10)), 1) if playing.any() else None}
    if payoff_s:
        pre = bd[(t >= max(0.0, payoff_s - 12)) & (t < payoff_s - 4)]
        at = bd[(t >= payoff_s) & (t < payoff_s + 4)]
        first = bd[(t >= 0) & (t < min(10.0, payoff_s))]
        if len(pre) and len(at):
            out["lift_into_payoff_db"] = round(float(np.median(at) - np.median(pre)), 1)
        if len(first) and len(at):
            out["payoff_over_opening_db"] = round(float(np.median(at) - np.median(first)), 1)
    # kick share: low-band (40-150 Hz) onsets of the bed, per second of music, and how periodic they are
    from numpy.fft import rfft, rfftfreq
    w = 1024
    fr = (len(bed) - w) // 512
    if fr > 32:
        idx = np.arange(w)[None, :] + 512 * np.arange(fr)[:, None]
        S = np.abs(rfft(bed[idx] * np.hanning(w), axis=1))
        f = rfftfreq(w, 1 / 24000)
        low = np.log1p(S[:, (f >= 40) & (f <= 150)].sum(1))
        flux = np.maximum(0, np.diff(low, prepend=low[:1]))
        f0 = flux - flux.mean()
        ac = [float(np.dot(f0[:-L], f0[L:]) / (np.dot(f0, f0) + 1e-9)) for L in range(10, 40)]   # 0.21-0.85 s: 70-280 bpm pulses
        out["low_pulse_steadiness"] = round(max(ac), 3)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rescore_music")
    ap.add_argument("film_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--used", default="", help="track ids to leave out (another film of the same run already plays them)")
    ap.add_argument("--kind", default=None, help="podcast | marketing | stories | longform (default: from film.json's workflow)")
    ap.add_argument("--no-model", action="store_true", help="choose by signal only")
    a = ap.parse_args(argv)
    src, dst = os.path.realpath(a.film_dir), os.path.realpath(a.out_dir)
    if dst == src or dst.startswith(src + os.sep):
        raise SystemExit("the new folder must be outside the delivered one: the delivered film is only read")
    if not a.no_model and os.environ.get("CODEX_PROXY_LOCAL") != "1" and os.path.exists(PROXY_ENV):
        # the workflows' own model route (the local codex-proxy, no API spend): this process again, under its environment
        cmd = " ".join(["source", f"'{PROXY_ENV}'", "&&", "exec", f"'{PY}'", f"'{os.path.abspath(__file__)}'"] + [f"'{x}'" for x in (argv or sys.argv[1:])])
        os.execvp("zsh", ["zsh", "-c", cmd])
    doc = json.load(open(os.path.join(src, "film.json")))
    clean = os.path.join(src, "film_clean.mp4")
    os.makedirs(dst, exist_ok=True)
    for name in ("film.json", "film_clean.mp4", "platform_music.json"):
        if os.path.exists(os.path.join(src, name)) and not os.path.exists(os.path.join(dst, name)):
            shutil.copy2(os.path.join(src, name), os.path.join(dst, name))
    clean = os.path.join(dst, "film_clean.mp4")

    import brand
    wk = brand.worker_dir()
    if wk not in sys.path:
        sys.path.insert(0, wk)
    os.environ.setdefault("VALMERA_MUSIC_SET", MUSIC_SET)
    os.environ.setdefault("FFMPEG_BIN", brand.FF)
    os.environ.setdefault("SKIP_DB_INIT", "1")
    import music_choose
    import music_film as MF
    from score import music as MU

    kind = a.kind or KIND.get(str(doc.get("workflow") or ""), "stories")
    fin = (doc.get("checks") or {}).get("finish") or {}
    dur = float(fin.get("programme_s") or (doc.get("checks") or {}).get("programme_s") or 0.0)
    if dur <= 0:
        raise SystemExit("film.json does not say where the end card starts (checks.finish.programme_s)")
    words, lines = heard_lines(clean, dur, os.path.join(dst, "rescore_heard.json"))
    old = doc.get("music") or {}
    om = ((old.get("does") or {}).get("marks") or {})
    sj = doc.get("script") or {}
    names = [str(n.get("name") if isinstance(n, dict) else n) for n in (sj.get("names") or [])][:6]
    brief = " ".join(str(sj.get(k) or "") for k in ("title", "hook_line", "direction"))[:1800]
    if names:
        brief = f"About: {', '.join(names)}. {brief}"
    call = None if a.no_model else MF.openai_call(timeout=1205)
    mood = om.get("mood") or ""
    turn_s, payoff_s = om.get("turn_s"), om.get("payoff_s")
    arc = None
    if call is not None:
        # the story's arc (how far under the voice each part sits) and where its close starts are read every time a
        # model is at hand, as a workflow film's are (music_film.score); the turn, payoff and mood the delivered film
        # recorded stand, the reading fills what it lacks
        got = MF.story_marks(lines, dur, call, brief=brief) or {}
        mood = mood or got.get("mood") or ""
        if payoff_s is None:
            turn_s, payoff_s = got.get("turn_s"), got.get("payoff_s")
        if got.get("arc"):
            close_s = got.get("close_s")
            if close_s is not None and payoff_s is not None and float(close_s) <= float(payoff_s):
                close_s = None                            # a close read against another payoff than the one that stands
            arc = dict(got["arc"], close_s=close_s)
    marks = {"dur_s": dur, "words": [w for w in words if w[0] < dur], "cuts": picture_cuts(doc, dur), "landings": [], "accents": [],
             "turn_s": turn_s, "payoff_s": payoff_s, "end_hit_s": None, "quiet": []}
    if kind == "podcast" and payoff_s is not None:
        marks["turn_drop"] = True                         # the podcast workflow's own opt-in (chain.music_version)
    if arc:
        # the engine holds the bed on the story's own arc (score.music.hold_under_words), as music_film.score hands it
        marks["story_arc"] = {k: arc[k] for k in MU.ARC_PARTS if k in arc}
        if arc.get("close_s") is not None:
            marks["close_s"] = float(arc["close_s"])
    film = MF.facts(kind, marks, brief=brief, mood=mood, lines=lines)
    used = [x for x in a.used.split(",") if x]
    shown = []

    def seen(system, user, schema):                       # the model, with what it was shown kept for the report
        shown.append({"system": system[:300], "user": user})
        return call(system, user, schema)
    prof = MU.profile_for(music_choose.ENGINE_KIND.get(film["kind"], kind))
    choice, song, tried = MF.choose(film, marks, os.path.join(dst, ".music_cache"), used=used, call=None if call is None else seen, profile=prof)
    rep = {"from": src, "made": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "kind": kind, "film": film,
           "marks": {"given": {k: om.get(k) for k in ("turn_s", "payoff_s", "mood")}, "dur_s": dur, "turn_s": turn_s, "payoff_s": payoff_s,
                     "mood": mood, "lines": len(lines), "turn_drop": bool(marks.get("turn_drop")), "arc": arc},
           "mood": mood, "tried": tried,
           "previous": {k: old.get(k) for k in ("title", "author", "cid", "why", "chosen_by")}, "shown": shown, "spend_usd": MF.spend(call)}
    if not choice:
        rep["why"] = "no track could be had"
        json.dump(rep, open(os.path.join(dst, "rescore.json"), "w"), ensure_ascii=False, indent=1, default=float)
        raise SystemExit(rep["why"])
    rep["choice"] = choice
    part = os.path.join(dst, "film.part.mp4")
    mk = MF.arranged(marks, choice)                       # the chooser's entry point, and the story path's arc
    mix = MU.mix_film(clean, song, mk, out=part, kind=kind, sung=choice.get("sung"), supplied=False)
    rep["mix"] = {k: v for k, v in mix.items() if k not in ("music",)}
    rep["mix"]["music"] = {k: v for k, v in (mix.get("music") or {}).items() if k in ("under_voice_db", "silent_from_s", "holes", "hits", "payoff_flow_s", "hold")}
    if not mix.get("out"):
        json.dump(rep, open(os.path.join(dst, "rescore.json"), "w"), ensure_ascii=False, indent=1, default=float)
        raise SystemExit("the engine could not arrange the chosen track: " + str((mix.get("plan") or {}).get("why")))
    final = os.path.join(dst, "film.mp4")
    os.replace(part, final)
    # measured on the files: the picture is the delivered one byte for byte, the length holds, the level, the bed
    same = video_md5(final) == video_md5(clean)
    rep["checks"] = {"picture_identical": same, "lufs": lufs_of(final, None, dur), "endcard_lufs": lufs_of(final, dur, None),
                     "clean_endcard_lufs": lufs_of(clean, dur, None), "bed": bed_measures(final, clean, dur, payoff_s, turn_s)}
    json.dump(rep, open(os.path.join(dst, "rescore.json"), "w"), ensure_ascii=False, indent=1, default=float)
    plan = (mix.get("plan") or {})
    doc["music"] = dict(picked_by_valmera=True, title=choice.get("title"), author=choice.get("author"), license=choice.get("license"),
                        credit=choice.get("credit"), page_url=choice.get("page_url"), cid=choice.get("cid"), why=choice.get("why"),
                        chosen_by=choice.get("chosen_by"), tried=tried, pool=choice.get("pool"), entry=choice.get("entry"), removable=True,
                        does=dict(marks=rep["marks"], mood=mood, drop_at=plan.get("drop_at"), build_at=plan.get("build_at"), end_hit_s=plan.get("end_hit_s"),
                                  jumps=plan.get("jumps"), tempo=plan.get("tempo")),
                        rescored=dict(at=rep["made"], from_folder=src, previous=rep["previous"]), film="film.mp4", clean_export="film_clean.mp4")
    json.dump(doc, open(os.path.join(dst, "film.json"), "w"), ensure_ascii=False, indent=1, default=float)
    shutil.rmtree(os.path.join(dst, ".music_cache"), ignore_errors=True)
    print(json.dumps({"choice": {k: choice.get(k) for k in ("title", "author", "cid", "chosen_by", "why")}, "checks": rep["checks"],
                      "previous": rep["previous"].get("title")}, ensure_ascii=False, default=float, indent=1))
    return rep


if __name__ == "__main__":
    main()
