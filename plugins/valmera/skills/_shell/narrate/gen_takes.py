"""Takes per script line from an open-weight model on the Mac's GPU through mlx-audio (runs in .valmera/venv-tts2;
lines.py writes the job and reads the takes; lane VO2, 14 Nov 2026).

  $VENV_TTS2/bin/python gen_takes.py --kind KIND --model REPO_OR_DIR --job job.json --out DIR
job.json: {"lines": [{"id": "s00", "text": "...", "instruct": "...", "takes": 3}],
           "ref_audio": "...", "ref_text": "...", "voice": "...", "params": {...}}
Writes DIR/<id>_t<k>.wav (model rate, float) and DIR/gen.json (seconds per take). Resumes: a take
already on disk is kept.
"""
import argparse
import json
import time
from pathlib import Path

import mlx.core as mx
import numpy as np
import soundfile as sf


def run_one(kind, model, ln, job, seed, k=0):
    mx.random.seed(seed)
    np.random.seed(seed)
    p = dict(job.get("params") or {})
    tp = job.get("take_params") or []
    if tp:
        p.update(tp[k % len(tp)])
    p.update(ln.get("params") or {})
    text = (ln.get("take_texts") or [ln["text"]])[k % len(ln.get("take_texts") or [ln["text"]])]
    ins = ln.get("instruct")
    if kind == "qwen_design":
        g = model.generate_voice_design(text=text, instruct=ins, language="English", **p)
    elif kind == "qwen_custom":
        g = model.generate_custom_voice(text=text, speaker=job["voice"], language="English", instruct=ins, **p)
    elif kind == "qwen_base":
        g = model.generate(text=text, ref_audio=job["ref_audio"], ref_text=job["ref_text"], lang_code="english", **p)
    elif kind in ("chatterbox", "chatterbox_turbo"):
        g = model.generate(text=text, ref_audio=job["ref_audio"], **p)
    elif kind == "higgs3":
        g = model.generate(text=text, ref_audio=job["ref_audio"], ref_text=job["ref_text"], seed=seed, **p)
    elif kind == "breeze":
        g = model.generate(text=text, voice="S0", instruct=ins, ref_audio=job["ref_audio"], ref_text=job["ref_text"],
                           seed=seed, split_pattern=None, **p)
    elif kind == "fish_s2":
        g = model.generate(text=text, ref_audio=job["ref_audio"], ref_text=job["ref_text"], verbose=False, **p)
    elif kind == "arktts":
        g = model.generate(text=text, ref_audio=job["ref_audio"], ref_text=job["ref_text"], seed=seed, **p)
    elif kind == "kokoro":
        g = model.generate(text=text, voice=job["voice"], lang_code="a", **p)
    elif kind in ("audio8", "zonos3", "omnivoice", "longcat", "ming", "generic_clone"):
        kw = dict(p)
        if job.get("ref_audio"):
            kw["ref_audio"] = job["ref_audio"]
        if job.get("ref_text") and kind not in ("zonos3",):
            kw["ref_text"] = job["ref_text"]
        if job.get("voice"):
            kw["voice"] = job["voice"]
        g = model.generate(text=text, **kw)
    else:
        raise SystemExit("unknown kind " + kind)
    parts, sr = [], None
    for r in g:
        parts.append(np.asarray(r.audio, dtype=np.float32).reshape(-1) if np.asarray(r.audio).ndim == 1
                     else np.asarray(r.audio, dtype=np.float32).mean(axis=-1 if np.asarray(r.audio).shape[-1] <= 2 else 0))
        sr = r.sample_rate
    return np.concatenate(parts), sr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--job", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed0", type=int, default=7)
    a = ap.parse_args()
    from mlx_audio.tts.utils import load_model
    job = json.load(open(a.job))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    model = load_model(a.model)
    load_s = time.time() - t0
    log = {"model": a.model, "kind": a.kind, "load_s": round(load_s, 1), "takes": []}
    gp = out / "gen.json"
    if gp.exists():
        log["takes"] = json.load(open(gp)).get("takes", [])
    for ln in job["lines"]:
        for k in range(int(ln.get("takes") or job.get("takes") or 3)):
            f = out / f"{ln['id']}_t{k}.wav"
            if f.exists():
                continue
            t = time.time()
            try:
                x, sr = run_one(a.kind, model, ln, job, a.seed0 + 101 * k + 7 * int(ln["id"][1:]), k)
            except Exception as e:  # noqa: BLE001
                print("FAIL", ln["id"], k, repr(e)[:300], flush=True)
                continue
            sf.write(f, x, sr, subtype="FLOAT")
            dt = time.time() - t
            log["takes"].append({"id": ln["id"], "take": k, "sec": round(dt, 2), "dur": round(len(x) / sr, 2), "sr": sr})
            print(ln["id"], k, f"{dt:.1f}s for {len(x) / sr:.1f}s", flush=True)
            json.dump(log, open(gp, "w"), indent=1)
    log["total_s"] = round(time.time() - t0, 1)
    json.dump(log, open(gp, "w"), indent=1)
    print("done", log["total_s"])


if __name__ == "__main__":
    main()
