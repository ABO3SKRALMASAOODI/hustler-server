"""Line-by-line narration with an open-weight model (lane VO2, 14 Nov 2026).

Every segment of the script is read as its own takes, each take told that line's direction (the writer's pace, melody,
emphasis and words for the actor, in the model's own controls), with the persona measured on the metromedia narrators
(voice_ref.json: ~191 words a minute while speaking, a 13.8 semitone range, the stressed word +4.9 dB and +8 st, four
pauses a minute). The kept take of each line is chosen by signal (perform.py: every word heard first, then the
prosody nearest the references, minus drift from the lines already kept). A line no take reads whole gets more takes.
The lines are joined with the
writer's pauses and get the same finish as every engine (narrate.deliver: pauses to the band, stress ride, mastering).

Phonon 31M (Apache-2.0, the ptts runtime, no PyTorch) is the default narrator since 14 Nov: the owner chose its Easton
voice by ear from the A/B of the four strongest candidates (.valmera/week-1112/side-by-side/voice/voice_compare_ab2.mp4,
"C ... seems ok"). It runs in this process on the CPU, about a tenth of a second a take, from the weights and voice on
disk (MODELS/phonon, set up by setup_phonon.sh; pinned to the Hub revisions in phonon.json when the folder is missing).
The other engines generate in .valmera/venv-tts2 (mlx-audio on the GPU) through gen_takes.py; listening and judging here.

The VOICE gate's listener (whisper large-v3) then hears the joined lines in their context, its transcript is put in its
spoken form by the gate's own model (spoken.py), and a line it does not hear whole gets its next take that the gate
listener hears whole, or new takes (gate_repair). The film reaches `check` with the line the gate would fail already
replaced.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
VENV_TTS2 = os.environ.get("NARRATE_TTS2_PY", "/Users/masaoodi/Documents/Valmera/.valmera/venv-tts2/bin/python")
MODELS = Path(os.environ.get("NARRATE_MODELS", "/Users/masaoodi/Documents/Valmera/.valmera/models/narrate"))
TAKES = 3
MORE_TAKES = 3            # extra takes for a line no take read whole
# A slow kept take may be brought toward its asked pace by up to this factor (perform.tempo). Off (1.0): on FedEx it
# lifted the rate group (20 -> 66, cbx3) but the room and pause groups fell with it and the score did not rise
# (65.5 -> 63.2; minitts 63.3 -> 62.9), 14 Nov. NARRATE_MAX_TEMPO turns it on.
MAX_TEMPO = float(os.environ.get("NARRATE_MAX_TEMPO", "1.0"))

PERSONA = {
    "03": ("Fast and alive, about 190 words a minute. Big natural pitch movement: each line climbs to its key word, hits it "
           "clearly louder and higher, then falls firmly at the end. Tight pauses only where a thought ends."),
    "04": ("Measured but moving, about 200 words a minute while speaking, with a real pause where a thought lands. Natural, "
           "varied melody; the key word of each line carries clear weight; sentences fall firmly at their ends."),
}
PACE = {"slow": "slower and weighty", "steady": "steady and clear", "brisk": "brisk and driving", "fast": "fast and urgent"}
MEL = {"falling": "land it with a firm falling cadence", "rising": "lift and lean forward", "level": "even and restrained",
       "wide": "wide, expressive pitch movement"}
ENERGY = {"low": "quiet and intimate", "mid": "warm and engaged", "high": "high energy, intense"}

# id: the model, how it is driven, and what varies between its takes (each take a different reading, chosen by signal)
ENGINES = {
    # Phonon: the takes differ by seed only, at the runtime's own temperature (0.3), as the owner heard them (14 Nov)
    "phonon": dict(kind="phonon", inproc=True, voice="Easton", quant="q8", lang="en", repo="gradium/phonon-31m-en"),
    "higgs3": dict(kind="higgs3", repo="bosonai/higgs-tts-3-4b", params={"max_new_tokens": 2048}, tags="higgs",
                   take_params=[{"temperature": 0.8}, {"temperature": 0.9}, {"temperature": 1.0}]),
    "breeze": dict(kind="breeze", repo="mlx-community/Breeze-TTS-2-mlx-8bit", params={"temperature": 0.9}, instruct=True,
                   take_params=[{"cfg_scale": 3.0}, {"cfg_scale": 4.0}, {"cfg_scale": 5.0}]),
    "fishs2": dict(kind="fish_s2", repo="mlx-community/fish-audio-s2-pro-8bit", params={"max_tokens": 2048}, tags="fish",
                   take_params=[{"temperature": 0.7}, {"temperature": 0.8}, {"temperature": 0.8}]),
    "audio8": dict(kind="arktts", repo="mlx-community/Audio8-TTS-Realistic-0.6b-bf16", params={"max_tokens": 2048},
                   take_params=[{"temperature": 0.6}, {"temperature": 0.75}, {"temperature": 0.9}]),
    "cbx3": dict(kind="chatterbox", repo="mlx-community/chatterbox-multilingual-v3", params={"temperature": 0.8, "lang_code": "en"},
                 take_params=[{"exaggeration": 0.5, "cfg_weight": 0.5}, {"exaggeration": 0.7, "cfg_weight": 0.35},
                              {"exaggeration": 0.9, "cfg_weight": 0.3}]),
}


def line_direction(seg, script):
    bits = []
    if seg.get("pace") or script.get("pace"):
        p = seg.get("pace") or script.get("pace")
        bits.append(PACE.get(p, str(p)))
    if seg.get("energy") or script.get("energy"):
        bits.append(ENERGY.get(seg.get("energy") or script.get("energy"), ""))
    if seg.get("melody"):
        bits.append(MEL.get(seg["melody"], str(seg["melody"])))
    if seg.get("emphasis"):
        bits.append("stress " + " and ".join(f'"{w}"' for w in seg["emphasis"]))
    if seg.get("direction"):
        bits.append(str(seg["direction"]).rstrip("."))
    return "; ".join(b for b in bits if b) + "."


def _sentences(t):
    out, cur = [], ""
    for ch in t:
        cur += ch
        if ch in ".?!" and cur.strip():
            out.append(cur.strip())
            cur = ""
    if cur.strip():
        out.append(cur.strip())
    return out


def _mark(text, words, tag):
    """the model's own emphasis tag before the first whole-word occurrence of each word the writer stressed"""
    toks = text.split(" ")
    done = set()
    for w in words or []:
        for wi in str(w).split():
            key = "".join(c for c in wi.lower() if c.isalnum() or c == "'")
            for i, t in enumerate(toks):
                tk = "".join(c for c in t.lower() if c.isalnum() or c == "'")
                if tk and tk == key and i not in done:
                    toks[i] = tag + t
                    done.add(i)
                    break
    return " ".join(toks)


def take_texts(seg, style):
    """three readings of one line in the model's own control tags, taken from the writer's delivery fields"""
    t = seg["text"]
    if style == "higgs":
        # the references speak ~190-200 words a minute while speaking, faster than the model's plain read: the third
        # reading asks for speed unless the writer marked the line slow
        speed = "" if seg.get("pace") == "slow" else "<|prosody:speed_fast|>"
        return [t, " ".join("<|prosody:expressive_high|>" + x for x in _sentences(t)),
                " ".join(speed + "<|prosody:expressive_high|>" + x for x in _sentences(t))]
    if style == "fish":
        em = _mark(t, seg.get("emphasis"), "[emphasis] ")
        return [t, em, "[excited tone] " + em]
    return [t]


def make_job(script, eng, ref_audio, ref_text, band="03", takes=TAKES):
    c = ENGINES[eng]
    persona = PERSONA.get(band, PERSONA["03"])
    lines = []
    for i, s in enumerate(script["segments"]):
        ln = {"id": f"s{i:02d}", "text": s["text"], "takes": takes}
        if c.get("instruct"):
            ln["instruct"] = f"Same speaker, telling a true story to a friend. {persona} This line: {line_direction(s, script)}"
        if c.get("tags"):
            ln["take_texts"] = take_texts(s, c["tags"])
        lines.append(ln)
    return {"lines": lines, "params": c.get("params", {}), "takes": takes, "take_params": c.get("take_params", []),
            "ref_audio": str(ref_audio) if ref_audio else None, "ref_text": ref_text}


def model_dir(eng):
    """the model's weights on disk (downloaded once into NARRATE_MODELS), else its hub id for mlx-audio to fetch"""
    d = MODELS / eng
    return str(d) if (d / "config.json").exists() else ENGINES[eng]["repo"]


# ---------------------------------------------------------------- Phonon (in process)

PHONON_PIN = json.loads((HERE / "phonon.json").read_text())
_PHONON = {}
# A line no take reads whole gets retakes in batches, each batch a little slower and clearer (Phonon's own pace
# control, duration_delta: +0.5 is about 15% longer, +1.0 about 17%). Skylab's "retrorockets struck a partially
# opened solar panel" read at the voice's own pace was heard whole in 0 of 45 takes (the s and d run into the next
# word); at +1.0 in 1 of 6 (14 Nov). The first takes are always the voice as the owner heard it.
INPROC_MORE = 6
INPROC_ROUNDS = 4
PHONON_RETAKE_DD = (0.0, 0.5, 1.0, 1.5)


def retake_dd(k, base=TAKES):
    if k < base:
        return 0.0
    return PHONON_RETAKE_DD[min((k - base) // INPROC_MORE, len(PHONON_RETAKE_DD) - 1)]


def phonon_voice(args, script):
    """The Phonon voice: one the caller or the script names, when the voices on disk (or the pinned catalog) have it;
    otherwise the owner's choice, Easton. The VoxCPM2 voice ids (atlas, sage...) are not Phonon voices."""
    want = [v for v in (getattr(args, "voice", None), script.get("phonon_voice"), script.get("voice"),
                        os.environ.get("NARRATE_PHONON_VOICE")) if v]
    have = {str(v["name"]).lower(): v["name"] for v in _phonon_catalog()}
    for v in want:
        if str(v).lower() in have:
            return have[str(v).lower()]
    return ENGINES["phonon"]["voice"]


def _phonon_catalog():
    f = MODELS / "phonon" / "voices.jsonl"
    if f.exists():
        return [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    return [{"name": v} for v in PHONON_PIN["voices"]]


def phonon_tts():
    """the loaded model, once per process: the folder on disk, else the pinned Hub revisions (downloaded once, cached)"""
    if "tts" not in _PHONON:
        import ptts
        c = ENGINES["phonon"]
        d = MODELS / "phonon"
        if (d / "config.json").exists() and (d / c.get("weights", "model.q8.gguf")).exists():
            model, cat = str(d), str(d / "voices.jsonl") if (d / "voices.jsonl").exists() else PHONON_PIN["voice_catalog"]
        else:
            model, cat = PHONON_PIN["model"], PHONON_PIN["voice_catalog"]
        _PHONON["tts"] = ptts.TTS(model, lang=c["lang"], quant=c["quant"], voice_catalog=cat)
        _PHONON["src"] = model
    return _PHONON["tts"]


def generate_phonon(job, out, log, seed0=7):
    """Takes of every line, on disk as s00_t0.wav...; a take already there is kept. Seeds as the A/B was made."""
    import soundfile as sf
    tts = phonon_tts()
    t0, n = time.time(), 0
    cp = out / "conditions.json"
    cond = json.loads(cp.read_text()) if cp.exists() else {}
    for ln in job["lines"]:
        i = int(ln["id"][1:])
        for k in range(int(ln.get("takes") or job.get("takes") or TAKES)):
            f = out / f"{ln['id']}_t{k}.wav"
            if f.exists():
                continue
            dd = retake_dd(k, int(job.get("takes") or TAKES))
            tts.set_conditions({"duration_delta": dd} if dd else {})
            x = tts.synth(ln["text"], voice=job["voice"], seed=seed0 + 101 * k + 7 * i)
            sf.write(f, np.asarray(x, np.float32), tts.sample_rate, subtype="FLOAT")
            cond[f.name] = {"duration_delta": dd}
            n += 1
    tts.set_conditions({})
    cp.write_text(json.dumps(cond))
    log(f"phonon {job['voice']}: {n} takes in {time.time() - t0:.1f}s")


def generate(job, eng, out, log, seed0=7):
    if ENGINES[eng].get("inproc"):
        return generate_phonon(job, out, log, seed0)
    jp = out / "job.json"
    json.dump(job, open(jp, "w"), indent=1)
    cmd = [VENV_TTS2, str(HERE / "gen_takes.py"), "--kind", ENGINES[eng]["kind"], "--model", model_dir(eng), "--job", str(jp),
           "--out", str(out), "--seed0", str(seed0)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{eng} generation failed: {(r.stderr or r.stdout)[-600:]}")
    log(f"{eng}: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else 'done'}")


def errs_of(N, text, heard, names):
    import measure
    w = measure.wer(text, heard)
    e = w["S"] + w["D"] + w["I"]
    if e:
        try:
            import spoken
            sp = spoken.spoken_form(heard, text, names)
            if sp.get("ok"):
                w2 = measure.wer(text, sp["text"])
                e = min(e, w2["S"] + w2["D"] + w2["I"])
        except Exception:  # noqa: BLE001 the rule's count stands
            pass
    return e


def trim_take(x, sr):
    """The take without the silence around it (openai_voice.trim), and without a lone blip before the first word:
    Phonon starts every take with one 10 ms tick about 40 dB under the voice, then 100 ms of digital silence; kept,
    it put a tick and a hole in every line join (Skylab, 14 Nov)."""
    import openai_voice as OV
    n = int(0.01 * sr)
    k = len(x) // n
    if k > 12:
        db = 10 * np.log10((x[: k * n].reshape(k, n).astype(np.float64) ** 2).mean(1) + 1e-14)
        on = db > db.max() - 45
        i = 0
        while i < k and not on[i]:
            i += 1
        j = i
        while j < k and on[j]:
            j += 1
        if j - i <= 3 and j + 5 < k and not on[j:j + 5].any():
            x = x[j * n:]
    return OV.trim(x, sr)


def _judge_files(N, files, seg, script, band, names, sr0):
    """every take of one line: what the take listener heard, its word errors, its signals"""
    import measure
    import perform as P
    takes = []
    cp = files[0].parent / "conditions.json" if files else None
    cond = json.loads(cp.read_text()) if cp and cp.exists() else {}
    for f in files:
        x, sr = measure.load_mono(f)
        x = trim_take(x.astype(np.float32), sr)
        if sr0 and sr != sr0:
            continue
        text, words = N.listen_fast(x, sr)
        e = errs_of(N, seg["text"], text, names)
        j = P.judge(x, sr, words, seg, band, N.target_wpm(seg, script))
        takes.append({"file": f.name, "errs": e, "heard": text, **j, "_x": x, "_sr": sr, **(cond.get(f.name) or {})})
    return takes


def seg_errors(segments, heard):
    """Word errors per script line in one transcript of the whole narration (the gate's alignment, measure.align).
    An inserted word counts against the lines on both sides of it. None when the script's words do not split by line."""
    import measure
    r = measure.norm_words(" ".join(s["text"] for s in segments))
    seg_of = [si for si, s in enumerate(segments) for _ in measure.norm_words(s["text"])]
    if len(seg_of) != len(r):
        return None
    best = None
    for hy in (False, True):
        h = measure.compounds(r, measure.norm_words(heard, hy))
        S, D, I, pairs = measure.align(r, h)
        if best is None or S + D + I < best[0]:
            best = (S + D + I, h, pairs)
    total, h, pairs = best
    errs = {}
    for i, tok in enumerate(r):
        if i not in pairs or h[pairs[i]] != tok:
            errs[seg_of[i]] = errs.get(seg_of[i], 0) + 1
    by_h = sorted((hj, ri) for ri, hj in pairs.items())
    matched = {hj for hj, _ in by_h}
    for j in range(len(h)):
        if j in matched:
            continue
        before = [ri for hj, ri in by_h if hj < j]
        after = [ri for hj, ri in by_h if hj > j]
        for ri in ([before[-1]] if before else []) + ([after[0]] if after else []):
            errs[seg_of[ri]] = errs.get(seg_of[ri], 0) + 1
    return {"total": total, "words": len(r), "by_line": errs}


def _floor(x, sr, win=0.6):
    """the quiet frames at a take's edge (its own floor), 10 ms each, and their level"""
    n = int(0.01 * sr)
    k = len(x) // n
    if k < 4:
        return np.zeros((0, n), np.float32)
    f = x[: k * n].reshape(k, n)
    db = 10 * np.log10((f.astype(np.float64) ** 2).mean(1) + 1e-14)
    return f[db <= np.percentile(db, 10) + 6]


TONE_MIN_DB = -70.0


def room_tone(a, b, sr, dur, seed=0):
    """`dur` seconds of the floor the two takes around a join share: noise with the spectrum and level of their
    quietest edge frames. A line join of digital silence beside a take's own floor read as a 24 dB floor step at the
    VOICE gate's join check (FedEx, 14 Nov); a pause inside a take carries that floor, and so does a join now."""
    n = max(int(dur * sr), 1)
    fr = np.concatenate([_floor(a[-int(0.6 * sr):], sr), _floor(b[: int(0.6 * sr)], sr)])
    if not len(fr):
        return np.zeros(n, np.float32)
    rms = float(np.sqrt((fr.astype(np.float64) ** 2).mean()))
    if 20 * np.log10(rms + 1e-12) < TONE_MIN_DB:
        # the takes' own pauses are digital silence (Phonon's are): so is the join. A tone at -80 dB there was
        # lifted to -51 dB by the mastering chain's leveler, a floor no pause inside a take has (Skylab, 14 Nov)
        return np.zeros(n, np.float32)
    w = np.hanning(fr.shape[1])
    mag = np.abs(np.fft.rfft(fr * w, axis=1)).mean(0)
    z = np.fft.rfft(np.random.default_rng(seed).standard_normal(n))
    z *= np.interp(np.linspace(0, 1, len(z)), np.linspace(0, 1, len(mag)), mag)
    y = np.fft.irfft(z, n)
    y *= rms / (float(np.sqrt((y ** 2).mean())) + 1e-12)
    return y.astype(np.float32)


def join_lines(kept, segments, sr):
    parts, bounds, chunks, t = [], [], [], 0.0
    for i, (x, s) in enumerate(zip(kept, segments)):
        chunks.append({"seg": i, "text": s["text"]})
        parts.append(x)
        t += len(x) / sr
        if i < len(kept) - 1:
            gap = max(float(s.get("pause_after") or 0.0), 0.25)
            parts.append(room_tone(x, kept[i + 1], sr, gap, seed=i))   # both edges are already at that floor
            bounds.append({"after_chunk": i, "t": round(t + gap / 2, 3), "pause_set": gap})
            t += gap
    return np.concatenate(parts).astype(np.float32), bounds, chunks


GATE_ROUNDS = int(os.environ.get("NARRATE_GATE_ROUNDS", "3"))


def gate_repair(N, eng, job, takes_dir, script, band, names, sr0, kept, kept_v, chosen, pool, report):
    """The gate's own listener and spoken form on the joined lines; a line it does not hear whole gets the next take
    that listener hears whole on its own (the take listener's clean takes first, by signal), else new takes."""
    import perform as P
    import spoken
    segs = script["segments"]
    full = " ".join(s["text"] for s in segs)
    hist, spent = [], set()
    for rnd in range(GATE_ROUNDS + 1):
        x, _, _ = join_lines(kept, segs, sr0)
        heard, _ = N.listen_long(x, sr0)
        sp = spoken.spoken_form(heard, full, names)
        se = seg_errors(segs, sp["text"] if sp.get("ok") else heard)
        if se is None:
            return hist
        hist.append({"round": rnd, "errors": se["total"], "words": se["words"], "lines": sorted(se["by_line"]),
                     "spoken_form": bool(sp.get("ok"))})
        N.log(f"gate listener, round {rnd}: {se['total']} word errors in {se['words']} on lines {sorted(se['by_line'])}")
        if se["total"] == 0 or rnd == GATE_ROUNDS or not set(se["by_line"]) - spent:
            return hist
        for i in sorted(set(se["by_line"]) - spent):
            failed = set(chosen[i].get("gate_failed") or []) | {chosen[i]["file"]}
            chosen[i]["gate_failed"] = sorted(failed)
            cur_e = errs_of(N, segs[i]["text"], N.listen(kept[i], sr0)[0], names)
            nearest = None            # the take the gate listener hears best, when none is heard whole
            for attempt in range(INPROC_ROUNDS if ENGINES[eng].get("inproc") else 2):
                cands = sorted((t for t in pool[i] if t["file"] not in failed and t["errs"] == 0),
                               key=lambda t: -(t["score"] - P.drift_penalty(t["voice"], kept_v[:i] + kept_v[i + 1:])))
                hit = None
                for t in cands:
                    text, _ = N.listen(t["_x"], t["_sr"])
                    e = errs_of(N, segs[i]["text"], text, names)
                    if e == 0:
                        hit = t
                        break
                    if nearest is None or e < nearest[0]:
                        nearest = (e, t)
                    failed.add(t["file"])
                if hit:
                    kept[i], kept_v[i], chosen[i] = hit["_x"], hit["voice"], dict(hit, gate_failed=sorted(failed))
                    report[i].update(kept=hit["file"], errs=hit["errs"], score=hit["score"], gate_round=rnd + 1)
                    N.log(f"line {i}: the gate listener hears {hit['file']} whole")
                    break
                if attempt < (INPROC_ROUNDS if ENGINES[eng].get("inproc") else 2) - 1:
                    # no take left that both listeners hear whole: new takes, new seeds
                    n = len(list(takes_dir.glob(f"s{i:02d}_t*.wav")))
                    more = INPROC_MORE if ENGINES[eng].get("inproc") else MORE_TAKES
                    generate(dict(job, lines=[dict(job["lines"][i], takes=n + more)]), eng, takes_dir, N.log)
                    new = [f for f in sorted(takes_dir.glob(f"s{i:02d}_t*.wav"), key=lambda p: int(p.stem.split("_t")[1]))
                           if f.name not in {t["file"] for t in pool[i]}]
                    pool[i] += _judge_files(N, new, segs[i], script, band, names, sr0)
            else:
                spent.add(i)          # every take this line will get has been heard: the gate reports what is left
                if nearest and nearest[0] < cur_e:
                    t = nearest[1]
                    kept[i], kept_v[i], chosen[i] = t["_x"], t["voice"], dict(t, gate_failed=sorted(failed))
                    report[i].update(kept=t["file"], errs=t["errs"], score=t["score"], gate_round=rnd + 1, gate_errs=nearest[0])
                    N.log(f"line {i}: no take heard whole; {t['file']} is heard best ({nearest[0]} against {cur_e})")
    return hist


def render_lines(args, script, vid, out, N):
    """narrate.render for a line-by-line engine: writes narration.wav, words.json, render.json through N.deliver."""
    import perform as P
    eng = args.engine
    out = Path(out)
    takes_dir = out / "_takes"
    takes_dir.mkdir(parents=True, exist_ok=True)
    bandname = args.band or script.get("band") or "03"
    band = json.load(open(HERE / "voice_ref.json"))["band"][bandname if bandname in ("03", "04") else "03"]
    names = list(script.get("names") or [])
    t_all = time.time()
    if ENGINES[eng].get("inproc"):
        voice = phonon_voice(args, script)
        job = make_job(script, eng, None, None, bandname, takes=max(1, int(args.takes or TAKES)))
        job["voice"] = voice
        vid = f"{eng}:{voice}"
    else:
        vmeta = json.load(open(N.VOICES / f"{vid}.json"))
        job = make_job(script, eng, N.voice_path(vid), vmeta["line"], bandname, takes=max(1, int(args.takes or TAKES)))
    generate(job, eng, takes_dir, N.log)
    gen_s = time.time() - t_all
    inproc = bool(ENGINES[eng].get("inproc"))
    n_more, n_rounds = (INPROC_MORE, INPROC_ROUNDS) if inproc else (MORE_TAKES, 1)
    kept, kept_v, chosen, pool, report, sr0 = [], [], [], [], [], None
    for i, s in enumerate(script["segments"]):
        takes = []
        for rnd in range(n_rounds + 1):
            files = sorted(takes_dir.glob(f"s{i:02d}_t*.wav"), key=lambda p: int(p.stem.split("_t")[1]))
            done = {t["file"] for t in takes}
            takes += _judge_files(N, [f for f in files if f.name not in done], s, script, band, names, sr0)
            sr0 = sr0 or (takes[0]["_sr"] if takes else None)
            if any(t["errs"] == 0 for t in takes) or rnd == n_rounds:
                break
            # no take read the line whole: more takes of this line, new seeds (Phonon: each batch a little slower)
            more = dict(job, lines=[dict(job["lines"][i], takes=len(files) + n_more)])
            N.log(f"line {i}: no take read it whole; {n_more} more")
            generate(more, eng, takes_dir, N.log)
        best = P.choose(takes, kept_v)
        x = best["_x"]
        f = 1.0
        wpm = (best.get("feat") or {}).get("wpm_artic")
        want = N.target_wpm(s, script)
        if MAX_TEMPO > 1.0 and wpm and want and wpm < want * 0.97:
            f = min(want / wpm, MAX_TEMPO)
            x = P.tempo(x, sr0, f)
        kept.append(x)
        kept_v.append(best["voice"])
        chosen.append(best)
        pool.append(takes)
        report.append({"line": i, "kept": best["file"], "errs": best["errs"], "score": best["score"], "net": best["net"], "tempo": round(f, 3)})
        N.log(f"line {i + 1}/{len(script['segments'])}: kept {best['file']} errs {best['errs']} score {best['score']} tempo x{f:.2f}")
    gate = gate_repair(N, eng, job, takes_dir, script, band, names, sr0, kept, kept_v, chosen, pool, report)
    for i, r in enumerate(report):
        r["takes"] = [{k: v for k, v in t.items() if k not in ("_x", "_sr", "voice")} for t in pool[i]]
    x, bounds, chunks = join_lines(kept, script["segments"], sr0)
    rep = {"engine": f"lines:{eng}", "model": _PHONON.get("src") if ENGINES[eng].get("inproc") else model_dir(eng),
           "voice": vid, "band": bandname, "chunks": chunks, "lines": report, "gate_repair": gate}
    N.deliver(x, sr0, script, chunks, bounds, rep, args, out, t_all, gen_s)
    if not os.environ.get("NARRATE_KEEP_TAKES"):
        subprocess.run(["rm", "-rf", str(takes_dir)])
