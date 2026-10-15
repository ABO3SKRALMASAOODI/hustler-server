"""Wave 1 preview-lane harness (INTERFACES_W1.md §7).

Runs in a CHILD process started with ``VALMERA_FX_PREVIEW=1`` (or a comma
list of track ids), where every Wave 1 member module registers its classes
before its track has switched it live. It checks, with the real worker
modules, that:

  * the registry holds exactly the pinned tags, stages, compilers and slot;
  * every EDL of the Wave 0 golden corpus keeps its signature and its dump
    (cached renders are keyed by both) with all Wave 1 members registered;
  * every stale-client example (fixtures/w1_stale_client_ops.json) writes
    and removes its device through ``edit_batch.apply_batch`` — the function
    an MCP client's apply_edit_batch runs — and every bad example is
    refused with its message;
  * flash safety reads an EDL holding every Wave 1 type.

    VALMERA_FX_PREVIEW=1 python w1_preview_probe.py

Prints one JSON report on its last line; the parent test asserts on it.
"""

import copy
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)

import edit_batch      # noqa: E402
import flash_safety    # noqa: E402
import schemas         # noqa: E402


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _without_inert(dump):
    d = copy.deepcopy(dump)
    assert d.pop("fx") == []
    for m in d.get("music") or []:
        assert m.pop("provenance") is None and m.pop("automation") is None
    for m in d.get("motion") or []:
        assert m.pop("composite") is None and m.pop("anchor") is None
    tr = (d.get("effects") or {}).get("transition")
    if tr:
        assert tr.pop("per_junction") is None
    return d


def registry():
    out = {"types": list(schemas.FX_TYPES),
           "authorable": list(schemas.FX_AUTHORABLE_TYPES),
           "slots": sorted(schemas._FX_SLOT_TYPES), "classes": {}}
    for cls in schemas.FX_MEMBERS:
        tag = cls.model_fields["type"].default
        out["classes"][tag] = {
            "stages": list(cls.STAGES), "compiler": getattr(cls, "COMPILER",
                                                            None),
            "module": cls.__module__.rsplit(".", 1)[-1],
            "authorable": bool(cls.AUTHORABLE)}
    return out


def corpus():
    with open(os.path.join(HERE, "fixtures", "w0_inert_corpus.json")) as f:
        cases = json.load(f)
    failures = []
    for name, case in sorted(cases.items()):
        try:
            new = schemas.validate_edl(copy.deepcopy(case["raw"]),
                                       case["duration"]).model_dump()
            if _sha(schemas.edl_signature(new)) != case["signature_sha256"]:
                failures.append(f"{name}: signature changed")
            elif _without_inert(new) != case["old_dump"]:
                failures.append(f"{name}: dump changed")
        except Exception as e:  # noqa: BLE001
            failures.append(f"{name}: {type(e).__name__}: {e}")
    return failures


def stale_client():
    with open(os.path.join(HERE, "fixtures",
                           "w1_stale_client_ops.json")) as f:
        fx = json.load(f)
    dur, allowed = fx["duration"], set(fx["allowed_keys"])
    base = schemas.validate_edl(copy.deepcopy(fx["base"]), dur).model_dump()
    base_sig = schemas.edl_signature(base)
    ok_failures, bad_failures = [], []
    combined_ops, combined_undo = [], []
    for case in fx["ok"]:
        try:
            out = edit_batch.apply_batch(base, case["ops"], dur, allowed)
            for op in case["ops"]:
                if op["layer"] == "fx":
                    ids = [i["id"] for i in out["fx"]]
                    assert op["id"] in ids, f"{op['id']} not written"
                    assert f"[{op['id']}]" in schemas.describe_edl(out, dur)
                else:
                    m = [i for i in out["motion"] if i["id"] == op["id"]][0]
                    want = case.get("expect") or {}
                    if want.get("behind_key"):
                        # 1A: a behind_subject graphic written with no mask
                        # renders through the program matte
                        assert m["layer"] == "behind_subject"
                        assert (m.get("behind") or {}).get("asset_key") == \
                            want["behind_key"], "program matte not named"
                    else:
                        assert m["composite"] is not None, \
                            "composite not set"
            back = edit_batch.apply_batch(out, case["undo"], dur, allowed)
            assert schemas.edl_signature(back) == base_sig, \
                "removal did not restore the edit"
            if case["ops"][0]["layer"] == "fx":
                combined_ops += case["ops"]
                combined_undo += case["undo"]
        except Exception as e:  # noqa: BLE001
            ok_failures.append(f"{case['name']}: {type(e).__name__}: "
                               f"{str(e)[:300]}")
    events = None
    try:
        allfx = edit_batch.apply_batch(base, combined_ops, dur, allowed)
        events = len(flash_safety.flash_events(allfx))
        flash_safety.assess(allfx)
        back = edit_batch.apply_batch(allfx, combined_undo, dur, allowed)
        assert schemas.edl_signature(back) == base_sig
    except Exception as e:  # noqa: BLE001
        ok_failures.append(f"all devices in one batch: {type(e).__name__}: "
                           f"{str(e)[:300]}")
    for case in fx["bad"]:
        try:
            edit_batch.apply_batch(base, case["ops"], dur, allowed)
        except Exception as e:  # noqa: BLE001
            if not re.search(case["match"], str(e)):
                bad_failures.append(f"{case['name']}: refused, but with "
                                    f"{str(e)[:300]!r}")
            continue
        bad_failures.append(f"{case['name']}: accepted")
    return ok_failures, bad_failures, events, len(combined_ops)


def main():
    ok_failures, bad_failures, events, n_fx = stale_client()
    print(json.dumps({"registry": registry(), "corpus": corpus(),
                      "ok_failures": ok_failures,
                      "bad_failures": bad_failures,
                      "flash_events": events, "fx_written": n_fx}))


if __name__ == "__main__":
    main()
