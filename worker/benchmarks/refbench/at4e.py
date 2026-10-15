"""Track 4E acceptance (ROADMAP 4E): delivery.

    python -m benchmarks.refbench.at4e AT-4E-1 AT-4E-2 AT-4E-3 AT-4E-4

AT-4E-1  one EDL -> five aspect masters (16:9, 4:3, 1:1, 4:5, 9:16), each
         re-solved (aspects.solve) and rendered final with the watermark.
         Pass: 0 type-on-face, 0 brand (end card / watermark) and 0
         safe-area violations; the UI scene re-laid out (not scaled);
         wall time of the five <= 2.5x one aspect (reported; the bar).
         Interim for W2-S1 (a Wave 2 showcase not on this branch): a Wave 4
         short on V01 with captions, a hook title, a UI scene, a NASA
         cutaway and depth of field.
AT-4E-2  the same short in German from a user-supplied translation (one
         line per phrase, written here as the user would supply it) and a
         text mapping. Pass: every motion/fx window and every caption
         phrase window unchanged (choreography); the critic's type lens
         finds no legibility failure.
AT-4E-3  trend recognition on refs 08/26/29 audio (internal measurement):
         the desk-drop grammar's drop within 1 beat of where each edit's
         montage starts.
AT-4E-4  whole-piece recreations from one chat (20 agent runs each): not
         run here — it needs the in-house agent (LLM turns, billed); the
         verdict says so.
"""

import copy
import json
import os
import subprocess
import sys
import time

import numpy as np

from . import out_dir

SCRATCH = os.path.expanduser("~/Documents/Valmera/.valmera/engines/w4/at4e")
BOARDS = os.path.expanduser(
    "~/Documents/Valmera/.valmera/refbench/boards/wave4/4E")
REFS = "/Users/masaoodi/.openclaw/workspace/references"
KEEP = (7.0, 48.5)
GERMAN = [
    "Sie haben jetzt vier ziemlich kleine Kinder,",
    "inwieweit haben sie Ihr Leben verändert, oder gibt es etwas, das Sie",
    "schon von ihnen gelernt haben, als Sie sie beobachtet haben?",
    "Man denkt immer darüber nach,",
    "es verändert wirklich den Zeithorizont für die Zukunft.",
    "Und es ist so,",
    "wenn wir in dieser sich beschleunigenden technologischen Welt leben "
    "oder in dieser verrückten Welt, in der wir",
    "nicht wissen, wie die Welt es bis 2030 oder 2035 schaffen soll,",
]
MAPPING = {"TIME HORIZONS": "ZEITHORIZONTE",
           "2030 or 2035?": "2030 oder 2035?",
           "how the world makes it": "wie die Welt es schafft"}


def _project():
    from . import at4c
    words = os.path.join(at4c.SCRATCH, "thiel_3334_words.json")
    w, info, index, edl = at4c._project("V01", "thiel_3334", KEEP, words)
    return w, info, index, edl, at4c


def _short(index):
    """The W4 short every master is delivered from."""
    import ui_scene
    from schemas import validate_edl
    g_sc = [{"at": 0.0, "layout": "stack", "objects": [
        {"id": "q", "kind": "card", "title": "2030 or 2035?",
         "body": "how the world makes it"}]}]
    g = ui_scene.solve(copy.deepcopy(g_sc), 3.2, 1080, 1920)
    e = {"keep": [list(KEEP)],
         "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.55},
         "captions": {"mode": "from_transcript"},
         "motion": [
             {"id": "hook", "template": "hook_title", "start": 0.2,
              "end": 2.4, "params": {"text": "TIME HORIZONS"}},
             {"id": "ui1", "template": "ui_scene", "start": 31.6,
              "end": 34.8, "layer": "below_captions", "params": {
                  "graph": g, "shown": ui_scene.caption_shown(g),
                  "backdrop": "dim",
                  "spec": {"scenes": g_sc, "words": []}}}],
         "fx": [
             {"id": "dof1", "type": "depth", "stage": "world", "start": 15.8,
              "end": 18.8, "kind": "dof", "focus": {"on": "subject"},
              "blur": 0.6, "device": "depth_dof"},
             {"id": "br1", "type": "broll", "stage": "screen",
              "start": 25.6, "end": 28.0,
              "source": {"kind": "asset", "asset_key":
                         "media/7/nasa_earth_orbit.mp4", "offset_s": 4.0},
              "credit": {"provider": "nasa", "license":
                         "Public domain (NASA media guidelines)",
                         "author": "NASA", "title": "Earth from orbit",
                         "page_url": "https://images.nasa.gov/"},
              "device": "broll_cutaway"}]}
    return validate_edl(e, float(index["video"]["duration"])).model_dump()


def _locals():
    from . import vfl
    return {"media/7/nasa_earth_orbit.mp4":
            vfl.window("V13", "nasa_earth_orbit")["path"]}


def _render(edl, src, index, out, locals_, want_wm=True):
    import fxgraph
    import gradewin
    import renderer
    import qa_metrics
    from schemas import validate_edl
    dur = float(qa_metrics.probe(src)["duration"])
    e = validate_edl(copy.deepcopy(edl), dur).model_dump()
    work = out + ".work"
    os.makedirs(work, exist_ok=True)
    gradewin.ASSET_LOCALS.update(locals_)
    os.environ["VALMERA_FX_ASSET_LOCALS"] = json.dumps(locals_)
    rows = []
    tok = fxgraph.collect(rows)
    t = time.time()
    try:
        renderer.render_edl(e, index, src, out, work, False,
                            want_wm=want_wm, asset_locals=dict(locals_))
    finally:
        fxgraph.stop_collecting(tok)
    return {"wall_s": round(time.time() - t, 2), "edl": e,
            "report": fxgraph.merge_report(rows)}


def _thumb(img, h=420):
    from PIL import Image
    im = Image.fromarray(np.asarray(img, np.uint8))
    return im.resize((max(1, int(im.width * h / im.height)), h))


def _board(path, rows, title):
    from . import at4b
    return at4b._board(path, rows, title)


def _verdict(case, metrics, extra=None, passed=None):
    od = os.path.join(out_dir(), case)
    os.makedirs(od, exist_ok=True)
    v = {"case": case, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "metrics": metrics, **(extra or {})}
    v["pass"] = passed if passed is not None else all(
        m.get("pass") is True for m in metrics.values())
    with open(os.path.join(od, "verdict.json"), "w") as f:
        json.dump(v, f, indent=1, default=str)
    print(json.dumps({k: v[k] for k in ("case", "metrics", "pass")},
                     indent=1, default=str))
    return v


def _m(value, op, bar, detail=None):
    ok = None
    if value is not None:
        ok = value <= bar if op == "<=" else value >= bar
    return {"value": None if value is None else round(float(value), 4),
            "bar": f"{op}{bar}", "pass": ok, "detail": detail or {}}


def _frames(path, ts):
    import qa_metrics
    info = qa_metrics.probe(path)
    fps = float(info["fps"])
    got, fr = qa_metrics.read_frames(path, frames={int(t * fps) for t in ts})
    return [np.asarray(f) for f in fr]


def at_4e_1():
    import aspects
    case = "AT-4E-1"
    os.makedirs(SCRATCH, exist_ok=True)
    w, info, index, _e, _at4c = _project()
    base = _short(index)
    locs = _locals()
    rows, per, walls = [], {}, {}
    for r in aspects.RATIOS:
        e, notes = aspects.solve(base, r, index)
        out = os.path.join(SCRATCH, f"master_{r.replace(':', 'x')}.mp4")
        res = _render(e, w["path"], index, out, locs)
        walls[r] = res["wall_s"]
        ver = aspects.verify(out, res["edl"], index, r, final=True,
                             want_wm=True, src_path=w["path"])
        ui = next(m for m in res["edl"]["motion"] if m["id"] == "ui1")
        g = ui["params"]["graph"]
        per[r] = dict(ver, notes=notes, ui_design=[g["W"], g["H"]],
                      wall_s=res["wall_s"])
        fr = _frames(out, (1.2, 17.0, 26.5, 33.0))
        rows.append((f"{r}: " + "; ".join(notes)[:150],
                     [(f"{t}s", _thumb(f, 300))
                      for t, f in zip((1.2, 17.0, 26.5, 33.0), fr)]))
    b = _board(os.path.join(BOARDS, case, "board.jpg"), rows,
               "AT-4E-1: five masters from one edit, each re-solved")
    one = walls.get("9:16") or max(walls.values())
    ratio = sum(walls.values()) / max(1e-6, one)
    designs = {tuple(v["ui_design"]) for v in per.values()}
    return _verdict(case, {
        "face_violations": _m(sum(v["faces"] for v in per.values()), "<=",
                              0),
        "brand_violations": _m(sum(v["brand"] for v in per.values()), "<=",
                               0),
        "safe_area_violations": _m(sum(v["safe_area"]
                                       for v in per.values()), "<=", 0),
        "ui_relaid_out": _m(float(len(designs) == len(aspects.RATIOS)),
                            ">=", 1.0),
        "five_vs_one_wall": _m(ratio, "<=", 2.5, walls),
    }, {"per_aspect": per, "board": b})


def at_4e_2():
    import motion_critic
    import retypeset_lang
    case = "AT-4E-2"
    os.makedirs(SCRATCH, exist_ok=True)
    w, info, index, _e, _at4c = _project()
    base = _short(index)
    ph = retypeset_lang.phrases(base, index)
    de, notes = retypeset_lang.retypeset(base, index, "de",
                                         translation=GERMAN,
                                         mapping=MAPPING)
    locs = _locals()
    out = os.path.join(SCRATCH, "german_9x16.mp4")
    res = _render(de, w["path"], index, out, locs)
    out_en = os.path.join(SCRATCH, "master_9x16.mp4")
    if not os.path.exists(out_en):
        _render(base, w["path"], index, out_en, locs)
    # choreography: every window the same
    moved = []
    for layer in ("motion", "fx"):
        a = {x["id"]: (x.get("start"), x.get("end"))
             for x in base.get(layer) or []}
        b = {x["id"]: (x.get("start"), x.get("end"))
             for x in res["edl"].get(layer) or []}
        moved += [f"{layer}:{k}" for k in a if a[k] != b.get(k)]
    ph_de = retypeset_lang.phrases(res["edl"], index)
    moved += [f"phrase {p['i']}" for p, q in zip(ph, ph_de)
              if (p["t0"], p["t1"]) != (q["t0"], q["t1"])]
    corr = res["edl"]["captions"].get("corrections") or []
    win_ok = all(any(abs(c["start"] - p["t0"]) < 0.05 for c in corr)
                 for p in ph)
    crit = motion_critic.critique(out, res["edl"], index, final=True,
                                  want_wm=True, deep=False)
    type_fail = [f for f in crit.get("findings") or []
                 if f.get("lens") in ("type",) and f.get("severity") in
                 ("blocking", "fix")]
    fr_de = _frames(out, (1.2, 6.0, 20.0, 33.0, 36.5))
    fr_en = _frames(out_en, (1.2, 6.0, 20.0, 33.0, 36.5))
    b = _board(os.path.join(BOARDS, case, "board.jpg"), [
        ("English master (9:16)", [(f"{t}s", _thumb(f, 380)) for t, f in
                                   zip((1.2, 6.0, 20.0, 33.0, 36.5), fr_en)]),
        ("German, from the user's translation — same windows",
         [(f"{t}s", _thumb(f, 380)) for t, f in
          zip((1.2, 6.0, 20.0, 33.0, 36.5), fr_de)])],
        "AT-4E-2: re-typeset in German (user-supplied translation)")
    return _verdict(case, {
        "windows_moved": _m(len(moved), "<=", 0, {"moved": moved}),
        "phrases_on_their_windows": _m(float(win_ok), ">=", 1.0,
                                       {"phrases": len(ph),
                                        "corrections": len(corr)}),
        "legibility_failures": _m(len(type_fail), "<=", 0,
                                  {"findings": type_fail,
                                   "type_measure": (crit.get("measures")
                                                    or {}).get("type")}),
    }, {"notes": notes, "board": b, "render": out,
        "critic_score": crit.get("score")})


def at_4e_3():
    import format_packs
    import perception
    import rhythm_types
    case = "AT-4E-3"
    rows = {}
    sigdir = os.path.expanduser(
        "~/Documents/Valmera/.valmera/engines/w4/refsigs")
    for n in ("08", "26", "29"):
        f = next(x for x in os.listdir(REFS) if x.startswith(n + "_"))
        path = os.path.join(REFS, f)
        p = perception.analyze_audio(path)
        m = rhythm_types.from_beat_grid(p)
        env, hop = format_packs.envelope(path)
        got = format_packs.recognise(m, energy=p.get("energy"),
                                     bin_s=float(p.get("energy_bin_s")
                                                 or 0.5), env=env,
                                     env_hop_s=hop)
        sig = json.load(open(os.path.join(sigdir, f"{n}.json")))
        cuts = sorted(sig["cuts"]["t"])
        row = {"recognised": got and got["id"], "bpm": p.get("bpm")}
        if got:
            d, beat = got["drop_s"], got["beat_s"]
            near = min(cuts, key=lambda c: abs(c - d))
            span = 8 * beat
            before = sum(1 for c in cuts if d - span <= c < d) / span
            after = sum(1 for c in cuts if d <= c < d + span) / span
            row.update(drop_s=d, beat_s=beat, nearest_cut_s=near,
                       err_beats=round(abs(near - d) / beat, 2),
                       cuts_per_s_build=round(before, 2),
                       cuts_per_s_drop=round(after, 2),
                       sections=[(x["name"], x["t0"], x["t1"])
                                 for x in got["sections"]])
        rows[n] = row
    errs = [r.get("err_beats") for r in rows.values()]
    worst = None if any(e is None for e in errs) else max(errs)
    denser = all(r.get("cuts_per_s_drop", 0) > r.get("cuts_per_s_build", 9)
                 for r in rows.values())
    drops = [r["drop_s"] for r in rows.values() if r.get("drop_s")]
    return _verdict(case, {
        "drop_to_editors_cut_beats": _m(worst, "<=", 1.0, rows),
    }, {"note": "internal measurement on reference audio and the edits' "
                "own cuts; nothing about it is stored — the grammar is "
                "structural and runs on the USER's track. 29 is cut "
                "densely through its build, so the nearest-cut distance "
                "discriminates less there; the build/drop densities are "
                "reported, not judged",
        "drop_section_denser_than_build": denser,
        "drop_spread_s": round(max(drops) - min(drops), 3) if drops
        else None})


def at_4e_4():
    return _verdict("AT-4E-4", {
        "agent_recreations": {"value": None, "bar": "100% of 60 runs",
                              "pass": None, "detail": {
                                  "why": "needs 60 in-house agent runs "
                                         "(LLM turns are billed; not run "
                                         "for validation)"}}},
        passed=False)


CASES = {"AT-4E-1": at_4e_1, "AT-4E-2": at_4e_2, "AT-4E-3": at_4e_3,
         "AT-4E-4": at_4e_4}


def main(argv):
    for case in argv or list(CASES):
        CASES[case]()


if __name__ == "__main__":
    main(sys.argv[1:])
