#!/usr/bin/env python3
"""Supply for a marketing film: real Valmera output, packed with its receipts.

Marketing shows only what the product did. The evidence is a studio recording (studio.py): one clip uploaded to
the product, the sentences typed, every export the browser downloaded and every version of the project's EDL.

    python supply.py studio --style reading-wipe --records whynot-pictures --out <run>/supply

The pack names, per record, the upload, the export(s) and the EDL files, each with its sha256, and the facts a
film may show (shapes, lengths, counts), read from those files. For the film that draws the timeline it also cuts
small frames of both exports (one every half second) and one of each picture the edit added.

Nothing here uploads, publishes or touches a project. Everything written goes under --out.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import film as F  # noqa: E402

V = "/Users/masaoodi/Documents/Valmera"
BIN = f"{V}/audit/subscriber-reliability-20260908/bin-arm"
FF, FP = os.environ.get("SHELL_FFMPEG", BIN + "/ffmpeg"), os.environ.get("SHELL_FFPROBE", BIN + "/ffprobe")


def sha256(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        n = 0
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
            n += len(b)
            if limit and n >= limit:
                break
    return h.hexdigest()


def sound_facts(ids=("tick_1", "whoosh_soft_1", "riser_2", "click_1", "typing_2")):
    """The library sounds a film may place: file, where its peak is, how loud its peak is."""
    lib_dir = os.path.join(F.scene_worker(), "sound_library")
    sounds = {}
    for m in json.load(open(os.path.join(lib_dir, "manifest.json"))):
        if m["id"] in ids:
            r = subprocess.run([FF, "-hide_banner", "-i", os.path.join(lib_dir, m["file"]), "-af", "volumedetect", "-f", "null", "-"],
                               capture_output=True, text=True)
            mx = next(float(ln.split("max_volume:")[1].split()[0]) for ln in r.stderr.splitlines() if "max_volume" in ln)
            sounds[m["id"]] = dict(file=m["file"], peak_s=float(m["peak_s"]), max_db=mx)
    return sounds


def studio_record(sid):
    """One studio recording as a film reads it: the upload, the export after the last sentence, the export before it
    when there is one, and the EDL of each."""
    import studio as ST
    d = os.path.join(ST.RECORDS, sid)
    r = json.load(open(os.path.join(d, "record.json")))
    if r.get("state") != "done" or not r.get("asks"):
        raise SystemExit(f"{sid}: not a finished recording with a sentence")
    final = next((e for e in r["exports"] if e["tag"] == "final"), None)
    if not final or not final.get("edl_version"):
        raise SystemExit(f"{sid}: no final export with its EDL version on record")
    before = next((e for e in r["exports"] if e["tag"].startswith("before_ask")), None)

    def edl(v):
        p = os.path.join(d, "edl", f"v{int(v):03d}.json")
        if not os.path.exists(p):
            raise SystemExit(f"{sid}: EDL v{v} is not on record")
        return p

    def media(e):
        return dict(path=e["file"], w=e["w"], h=e["h"], fps=e["fps"], dur=e["dur"], sha256=e["sha256"], edl_version=e.get("edl_version"),
                    edl=edl(e["edl_version"]) if e.get("edl_version") else None)

    ask = r["asks"][-1]
    j = json.load(open(edl(final["edl_version"])))
    tl, body = F.program(j)
    pics = F.pictures_of(j)
    cap = j.get("captions")
    facts = dict(upload_ratio=F.ratio(r["upload"]["w"], r["upload"]["h"]), upload_length=F.clock(r["upload"]["dur"]), upload_s=round(r["upload"]["dur"], 1),
                 export_ratio=F.ratio(final["w"], final["h"]), export_length=F.clock(body), export_s=round(body, 1), cuts=max(len(tl.segs) - 1, 0),
                 pictures=len(pics), captions=bool(cap), edl_version=final["edl_version"], sentences=len(r["asks"]))
    return dict(sid=sid, sentence=ask["text"], phase=ask.get("phase"), reply=ask.get("reply"), ask_seconds=ask.get("seconds"),
                upload=dict(path=r["upload"]["file"], w=r["upload"]["w"], h=r["upload"]["h"], fps=r["upload"]["fps"], dur=r["upload"]["dur"],
                            sha256=r["upload"]["sha256"]),
                export=media(final), before=media(before) if before else None, edl=edl(final["edl_version"]), body_s=round(body, 3), facts=facts,
                source=r.get("source"), be_sha=r.get("be_sha"), fe_sha=r.get("fe_sha"), models=r.get("models"), journey=r.get("journey"),
                journey_sha256=r.get("journey_sha256"), record=os.path.join(d, "record.json"))


def frames_of(path, out_dir, tag, step, upto, w=90, h=160):
    """One small frame of a film every `step` seconds, up to `upto`: [{t, path, w, h}]."""
    os.makedirs(out_dir, exist_ok=True)
    subprocess.run([FF, "-v", "error", "-y", "-t", f"{upto:.3f}", "-i", path, "-vf", f"fps={1.0 / step:.6f}:start_time=0:round=down,scale={w}:{h}:flags=lanczos",
                    "-q:v", "3", os.path.join(out_dir, f"{tag}_%04d.jpg")], check=True)
    rows = []
    for n in sorted(x for x in os.listdir(out_dir) if x.startswith(tag + "_")):
        i = int(n[len(tag) + 1:-4]) - 1
        rows.append(dict(t=round(i * step, 3), path=os.path.join(out_dir, n), w=w, h=h))
    return rows


def studio(a):
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    recs = [studio_record(sid) for sid in a.records.split(",") if sid]
    thumbs = {}
    if a.style == "edits-itself":
        r = recs[0]
        if not r["before"]:
            raise SystemExit(f"{r['sid']}: no export from before the sentence: nothing to show changing")
        ea, eb = json.load(open(r["before"]["edl"])), json.load(open(r["edl"]))
        da, db = F.program(ea)[1], F.program(eb)[1]
        thumbs["a"] = frames_of(r["before"]["path"], os.path.join(out, "thumbs"), "a", 0.5, da)
        thumbs["b"] = frames_of(r["export"]["path"], os.path.join(out, "thumbs"), "b", 0.5, db)
        thumbs["pictures"] = {}
        for o in F.pictures_of(eb):
            p = os.path.join(out, "thumbs", f"pic_{o['id']}.jpg")
            t = float(o["start"]) + float(o["duration_s"]) / 2
            subprocess.run([FF, "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", r["export"]["path"], "-frames:v", "1", "-vf", "scale=270:480:flags=lanczos",
                            "-q:v", "3", p], check=True)
            thumbs["pictures"][o["id"]] = dict(path=p, w=270, h=480, t=round(t, 3))
    if a.style == "one-clip-three-films":
        if len(recs) != 3:
            raise SystemExit("three recordings of one clip are needed")
        if len({r["upload"]["sha256"] for r in recs}) != 1:
            raise SystemExit("the three recordings are not of the same upload")
        if len({r["sentence"] for r in recs}) != 3:
            raise SystemExit("the three sentences are not three different sentences")
    receipts = []
    for r in recs:
        receipts.append(dict(claim="this sentence was typed into the product, and this export came back", sentence=r["sentence"], record=r["record"],
                             journey=r["journey"], journey_sha256=r["journey_sha256"], product_commit=r["be_sha"], frontend_commit=r["fe_sha"]))
        receipts.append(dict(claim="the export is shown as the browser downloaded it", file=r["export"]["path"], sha256=r["export"]["sha256"],
                             edl_version=r["export"]["edl_version"], edl=r["edl"], edl_sha256=sha256(r["edl"])))
        receipts.append(dict(claim="the upload, and the seconds of the source it was cut from", file=r["upload"]["path"], sha256=r["upload"]["sha256"], source=r["source"]))
        if r["before"]:
            receipts.append(dict(claim="the same project before the sentence, as the browser downloaded it", file=r["before"]["path"], sha256=r["before"]["sha256"],
                                 edl_version=r["before"]["edl_version"], edl=r["before"]["edl"], edl_sha256=sha256(r["before"]["edl"])))
    sup = dict(sid=f"wf2-{a.style}-{recs[0]['sid']}", style=a.style, ffmpeg=FF, sounds=sound_facts(), records=recs, thumbs=thumbs, receipts=receipts)
    with open(os.path.join(out, "pack.json"), "w") as f:
        json.dump(sup, f, ensure_ascii=False, indent=1)
    print(json.dumps(dict(pack=os.path.join(out, "pack.json"), records=[r["sid"] for r in recs], thumbs={k: len(v) for k, v in thumbs.items()})))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("studio")
    st.add_argument("--style", required=True)
    st.add_argument("--records", required=True)
    st.add_argument("--out", required=True)
    a = ap.parse_args()
    studio(a)


if __name__ == "__main__":
    main()
