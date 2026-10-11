"""Track 1F checks that need the Wave 1 fx types registered.

Runs in a CHILD process with ``VALMERA_FX_PREVIEW=1`` (tests/test_w1f_planner
starts it), where every Wave 1 member module registers its classes, so the
planner's candidates, the trimmer and the verbs are exercised through the
real ``edit_batch.apply_batch`` — the path a stale MCP client's
apply_edit_batch runs. Device cards are the stand-ins in
tests/fixtures/w1f_cards (the real cards ship with tracks 1A-1E).

    VALMERA_FX_PREVIEW=1 python w1f_preview_probe.py

Prints one JSON report on its last line.
"""

import copy
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)
CARDS = os.path.join(HERE, "fixtures", "w1f_cards")

import devices          # noqa: E402
import device_planner   # noqa: E402
import edit_batch       # noqa: E402
import flash_safety     # noqa: E402
import motion_qa        # noqa: E402
import schemas          # noqa: E402

DURATION = 40.0


def program():
    """A 30 s talking head (two keeps, one cut at program 14.5 s) with a
    setup, a contrast, a negation and a number payoff at the end."""
    text = ("We tried everything in the lab for years. Nothing worked. "
            "Everyone said the idea was dead. | But then the numbers changed. "
            "We did not quit. Today it costs 140 dollars.")
    words, t = [], 0.4
    for tok in text.split():
        if tok == "|":
            t = 20.4                       # the cut removes 14.5-20.0
            continue
        dur = 0.32 if len(tok) > 4 else 0.22
        words.append({"w": tok, "t0": round(t, 2), "t1": round(t + dur, 2),
                      "p": 0.95})
        t += dur + (0.45 if tok.endswith(".") else 0.06)
    index = {"video": {"duration": DURATION, "width": 1920, "height": 1080,
                       "fps": 30.0, "has_audio": True},
             "words": words, "shots": [{"start": 0.0, "end": 14.5},
                                       {"start": 20.0, "end": 40.0}]}
    edl = schemas.validate_edl({"keep": [[0.0, 14.5], [20.0, 35.5]]},
                               DURATION).model_dump()
    return edl, index


def cards_with(extra=None):
    d = tempfile.mkdtemp(prefix="w1f_cards_")
    for f in os.listdir(CARDS):
        shutil.copy(os.path.join(CARDS, f), d)
    for card in extra or []:
        with open(os.path.join(d, card["id"] + ".json"), "w") as fh:
            json.dump(card, fh)
    return d


def zoom_card():
    with open(os.path.join(CARDS, "accent_negative.json")) as fh:
        c = json.load(fh)
    c.update(id="accent_zoom_test", owner_flags={
        "adds_sound": False, "adds_zoom": True, "flash": False},
        description="a stand-in device that adds a zoom")
    return c


def suggest(edl, index, cards, **kw):
    kw.setdefault("matte", {"status": "ready"})
    kw.setdefault("sidecar_status", {"matte.person": "ready"})
    return device_planner.suggest(edl, index, cards=cards,
                                  duration=DURATION, **kw)


def check_candidates(edl, index, cards):
    out = {"failures": []}
    c = suggest(edl, index, cards)
    out["n"] = len(c)
    out["devices"] = [(x["device"], x["at"], x["detail"]["moment"])
                      for x in c]
    keys = {"device", "family", "verb", "op", "at", "bind", "reason",
            "exemplar", "est_ms", "owner_flags", "rank"}
    for x in c:
        if not keys <= set(x):
            out["failures"].append(f"{x['device']}: keys {keys - set(x)}")
        if not x.get("bind") or not x.get("exemplar"):
            out["failures"].append(f"{x['device']}: unbound or no exemplar")
        if x["owner_flags"].get("adds_zoom") or \
                x["owner_flags"].get("adds_sound"):
            out["failures"].append(f"{x['device']}: zoom/sound proposed")
    out["ranks"] = [x["rank"] for x in c]
    # every candidate together, in ONE batch, through apply_batch
    ops = [x["op"] for x in c]
    after = edit_batch.apply_batch(edl, ops, DURATION, set())
    undo = []
    for op in ops:
        if op["layer"] == "fx":
            undo.append({"action": "remove", "layer": "fx", "id": op["id"]})
    back = edit_batch.apply_batch(after, undo, DURATION, set()) \
        if undo else after
    out["undo_restores"] = (schemas.edl_signature(back)
                            == schemas.edl_signature(edl))
    plan = device_planner.moments(edl, index)
    out["payoff"] = plan["payoff"] and {
        "word": plan["payoff"]["word"], "t": plan["payoff"]["t"],
        "hold_end": plan["payoff"]["hold_end"]}
    # restraint rules
    times = sorted(x["at"] for x in c)
    out["min_gap"] = min((b - a for a, b in zip(times, times[1:])),
                         default=None)
    p = plan["payoff"]
    out["in_hold"] = [x["device"] for x in c
                      if x["family"] in device_planner.HOLD_FREE and p
                      and p["t"] + 0.02 < x["at"] < p["hold_end"]]
    out["punct_moments"] = [(x["detail"]["moment"], x["detail"]["word"])
                            for x in c if x["family"] == "punctuation"]
    out["flash_peak"] = flash_safety.assess(after)["peak_per_s"]
    out["junctions"] = [x["op"]["value"].get("at") for x in c
                        if x["family"] == "transition"]
    out["payoff_device"] = [x["device"] for x in c
                            if x["detail"]["moment"] == "word.payoff"]
    out["alternatives"] = {x["device"]: [a["device"] for a in
                                         x["detail"]["alternatives"]]
                           for x in c}
    out["text"] = device_planner.format_candidates(c)[:3000]
    return out


def check_gates(edl, index, cards_dir):
    cards = devices.load_cards(cards_dir)
    out = {}
    c = suggest(edl, index, cards)
    out["zoom_unasked"] = [x["device"] for x in c
                           if x["device"] == "accent_zoom_test"]
    c = suggest(edl, index, cards, asked=["zoom"])
    out["zoom_asked_eligible"] = devices.proposable(
        cards["accent_zoom_test"], ["zoom"])[0]
    out["dark"] = [x["device"] for x in c if x["device"] == "accent_dark_test"]
    os.environ["W1F_TEST_DARK"] = "1"
    out["dark_flag_proposable"] = devices.proposable(
        cards["accent_dark_test"])[0]
    os.environ.pop("W1F_TEST_DARK", None)
    c = suggest(edl, index, cards, weights={"subject_fill_flash": 0.0,
                                            "matte_fx": 1.0})
    out["muted"] = [x["device"] for x in c
                    if x["device"] == "subject_fill_flash"]
    c = suggest(edl, index, cards, matte={"person": False})
    out["no_person_matte"] = [x["device"] for x in c
                              if x["family"] in ("matte_fx", "type_depth")]
    c = suggest(edl, index, cards, matte={"status": "absent",
                                          "estimate_s": 42},
                sidecar_status={})
    out["pending_notes"] = [x["reason"] for x in c
                            if x["family"] == "matte_fx"]
    c = suggest(edl, index, cards, window=(0.0, 10.0))
    out["window_max_at"] = max([x["at"] for x in c], default=None)
    c = suggest(edl, index, cards, intensity="charged")
    after = edit_batch.apply_batch(edl, [x["op"] for x in c], DURATION,
                                   set())
    out["charged_n"] = len(c)
    out["charged_flash_peak"] = flash_safety.assess(after)["peak_per_s"]
    c = suggest(edl, index, cards, intensity="calm")
    out["calm_n"] = len(c)
    return out


def check_trim(edl, index, cards):
    """Six negatives inside 1.2 s that the user once opted into; 'less
    flashy' = calm: flash safety and the budgets trim the lowest-ranked."""
    plan = device_planner.moments(edl, index)
    words = [w for w in plan["words"]]
    fx = []
    base = next(w for w in words if w["w"].lower().startswith("everyone"))
    for k in range(6):
        f = device_planner.frame_of(base["t0"], 30.0) + 6 * k
        fx.append({"id": f"neg{k}", "type": "accent",
                   "stage": "pre_captions", "kind": "negative",
                   "start": device_planner.time_of(f, 30.0),
                   "end": device_planner.time_of(f + 1, 30.0),
                   "device": "accent_negative"})
    # the strongest one is bound to a stressed word
    fx[2]["bind"] = {"on": "word", "ref": base["i"], "rhythm": "speech"}
    edl2 = edit_batch.apply_batch(edl, [{"action": "upsert", "layer": "fx",
                                         "id": i["id"], "value": i}
                                        for i in fx], DURATION, set())
    cards = dict(cards)
    before = flash_safety.assess(edl2, request_text="strobe it")
    rm = device_planner.trim_plan(edl2, index, "charged", cards=cards,
                                  opted_in=True)
    rm_calm = device_planner.trim_plan(edl2, index, "calm", cards=cards,
                                       opted_in=False)
    ops = [{"action": "remove", "layer": "fx", "id": r["id"]}
           for r in rm_calm]
    after = edit_batch.apply_batch(edl2, ops, DURATION, set())
    return {"flash_before": before["peak_per_s"],
            "charged_removed": [r["id"] for r in rm],
            "calm_removed": [r["id"] for r in rm_calm],
            "calm_reasons": sorted({r["reason"].split(":")[0]
                                    for r in rm_calm}),
            "kept": [i["id"] for i in after["fx"]],
            "flash_after": flash_safety.assess(after)["peak_per_s"],
            "audit": [f["code"] for f in motion_qa.audit(
                edl2, index, cards=cards)["findings"]]}


class _DB:
    def __init__(self, kv, ctx):
        self.kv, self.ctx = kv, ctx

    def run(self, fn, *a):
        name = getattr(fn, "__name__", "")
        if name == "kv_get":
            return self.kv.get(a[0])
        if name == "kv_put":
            self.kv[a[0]] = a[1]
            return None
        if name == "project_media_keys":
            return {}
        if name == "recent_edls":
            return [v for v in self.ctx.versions]
        if name == "asset_by_key":
            return None
        raise RuntimeError(f"unexpected db call {name}")


class FakeCtx:
    """The parts of agent_tools.ToolContext the verify verbs read."""

    def __init__(self, edl, index):
        self.versions = [edl]
        self.version = 1
        self.index = index
        self.has_main_video = True
        self.duration = DURATION
        self.project_id = 7
        self.user_message = ""
        self.kv = {}
        self.db = _DB(self.kv, self)
        self.pending_images = []
        self.workdir = tempfile.mkdtemp(prefix="w1f_ctx_")

    def latest_edl(self):
        return {"version": self.version, "json": self.versions[-1]}

    def write_edl(self, edl, desc):
        self.versions.append(edl)
        self.version += 1
        return f"EDL v{self.version - 1} -> v{self.version}: {desc}"


def check_verbs(edl, index, cards_dir):
    import tools_ext.verify_tools as vt
    out = {}
    devices.CARDS_DIR = cards_dir
    devices._cached_cards.cache_clear()
    ctx = FakeCtx(edl, index)
    out["suggest"] = vt.suggest_devices(ctx)[:4000]
    cands = ctx._last_device_candidates
    # write them as the agent would: one batch through the verb path
    from tools_ext import _write
    out["write"] = _write.commit(ctx, [c["op"] for c in cands],
                                 "wrote candidates")
    out["audit"] = vt.audit_motion(ctx)[:2000]
    out["set_calm"] = vt.set_intensity(ctx, "calm")
    out["prefs"] = ctx.kv.get("device_prefs:7")
    out["fx_after"] = [i["id"] for i in ctx.versions[-1].get("fx") or []]
    out["bad_level"] = vt.set_intensity(ctx, "loud")
    out["nothing_to_trim"] = vt.set_intensity(ctx, "charged")
    out["proof_unknown"] = vt.proof_device(ctx, ids=["nope"])
    return out


def main():
    edl, index = program()
    cards_dir = cards_with([zoom_card()])
    edit_batch._CARDS_DIR = cards_dir
    cards = devices.load_cards(cards_dir)
    report = {"registered": sorted(schemas.FX_TYPES)}
    for name, fn in (("candidates", lambda: check_candidates(edl, index,
                                                            cards)),
                     ("gates", lambda: check_gates(edl, index, cards_dir)),
                     ("trim", lambda: check_trim(edl, index, cards)),
                     ("verbs", lambda: check_verbs(edl, index, cards_dir))):
        try:
            report[name] = fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            report[name] = {"error": f"{type(e).__name__}: {e}",
                            "trace": traceback.format_exc()[-2000:]}
    print(json.dumps(report, default=str))


if __name__ == "__main__":
    main()
