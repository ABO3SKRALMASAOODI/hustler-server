"""Round 94 — the caption-erase loop (project 382, 2026-08-07).

A user asked for nicer captions on footage with yellow caption boxes burned
in. The agent's first turn was right (detect -> erase -> restyle, 2 minutes);
the next 23 minutes were a spiral: 36 EDL versions, 17 preview renders, 87
LLM calls, and a final give-up blur. Three defects fed it, none of them a
missing verb:

  * look_at routed `output_times=[]` to the output path and rejected —
    NINE times in one session — discarding the valid source `times` the
    model had passed alongside (this model fills every schema field).
  * find_burned_text scans the RAW source, so a band the EDL already
    repaints kept listing as if the erase had failed, and its own closing
    line invited erasing it again.
  * erase_region stacked a second repaint over the same band (a text-fill
    under a 70%x30% box-fill) instead of replacing it — the "corrupt
    screen" / "two pyramids" the user reported.

Pins:
  * look_at treats empty arrays as absent: times=[...] with
    output_times=[] reaches the frame path; all-empty gets the teaching
    rejection that names all three forms.
  * _rect_cover / _windows_overlap measure the session's real rectangles
    the way the fix assumes.
  * _superseded_patches drops a patch only when EVERY region is re-covered
    over an overlapping window.
  * find_burned_text annotates covered detections with ALREADY
    repainted/censored, keeps the erase invitation off when everything is
    covered, and appends the quality-not-detection NOTE.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools                                              # noqa: E402
import inpaint                                                  # noqa: E402

PASS = 0


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


# The session's real geometry: the detected caption band, the text-fill
# erase the agent placed on it, and the giant box-fill it stacked on top.
BAND = {"x": 0.3125, "y": 0.6907, "w": 0.3816, "h": 0.1463,
        "first_s": 2.7, "last_s": 32.44, "kind": "captions",
        "coverage": 0.53, "changes": 12}
ER_TEXT = {"id": "er1", "x": 0.3125, "y": 0.6907, "w": 0.3816, "h": 0.1463,
           "start": 0.45, "end": 34.69, "fill": "text"}
ER_SLAB = {"id": "er2", "x": 0.2, "y": 0.6, "w": 0.7, "h": 0.3,
           "start": None, "end": None, "fill": "box"}

print("== 1. look_at: empty arrays are absent, not requests ==")

r = agent_tools.look_at(None, times=[], output_times=[])
check("all-empty gets the teaching rejection, not the output one",
      "output_times=[...]" in r and "counts as not passed" in r)
check("the output-path rejection is gone from that answer",
      "must be a non-empty array of OUTPUT" not in r)


class _Ctx:
    """Just enough ctx for look_at to get PAST the routing: clamp works,
    every pixel source fails, and the failure text proves which path ran."""
    workdir = "/nonexistent"

    def clamp(self, t):
        return float(t)

    def proxy_path(self):
        raise RuntimeError("no proxy in this test")

    def latest_edl(self):
        raise RuntimeError("no edl in this test")


r = agent_tools.look_at(_Ctx(), times=[2.0, 6.0], output_times=[])
check("times + empty output_times reaches the FRAME path",
      "Could not extract frames" in r)
r = agent_tools.look_at(_Ctx(), times="junk", output_times=[])
check("a non-list times still teaches the array form",
      "times must be an array" in r)

print("== 2. cover geometry, on the session's real rectangles ==")

check("the slab covers the band (this is the stack that corrupted v14)",
      agent_tools._rect_cover(ER_SLAB, BAND) >= 0.5)
check("the band does not cover the slab",
      agent_tools._rect_cover(BAND, ER_SLAB) < 0.5)
check("a disjoint corner covers nothing",
      agent_tools._rect_cover(
          {"x": 0.74, "y": 0.27, "w": 0.19, "h": 0.03}, BAND) == 0.0)
check("None windows mean the whole video",
      agent_tools._windows_overlap(None, None, 2.7, 32.44, 39.2))
check("disjoint windows do not overlap",
      not agent_tools._windows_overlap(0.0, 2.0, 3.0, 4.0, 39.2))

print("== 3. supersede: replace, never stack ==")

edl = {"patches": [
    {"id": "pa1", "regions": [dict(ER_TEXT)]},
    {"id": "pa2", "regions": [dict(ER_TEXT, id="er3"),
                              {"id": "er4", "x": 0.05, "y": 0.05,
                               "w": 0.1, "h": 0.05,
                               "start": None, "end": None}]},
]}
new = [dict(ER_SLAB, id="er9")]
gone = agent_tools._superseded_patches(edl, new, 39.2)
check("re-erasing the band supersedes the patch that held it",
      [p["id"] for p in gone] == ["pa1"])
check("a patch with an uncovered region survives",
      all(p["id"] != "pa2" for p in gone))
check("a disjoint window supersedes nothing",
      agent_tools._superseded_patches(
          edl, [dict(ER_SLAB, id="er9", start=36.0, end=39.0)], 39.2) == [])

print("== 4. find_burned_text tells the truth about covered marks ==")


class _ScanCtx:
    has_main_video = True
    duration = 39.2

    def __init__(self, edl_json):
        self._edl = edl_json

    def proxy_path(self):
        return "/dev/null"

    def latest_edl(self):
        return {"json": self._edl}


_real_detect = inpaint.detect_text_regions
inpaint.detect_text_regions = lambda *a, **k: [dict(BAND)]
try:
    covered_edl = {"patches": [{"id": "pa1", "regions": [dict(ER_TEXT)]}]}
    r = agent_tools.find_burned_text(_ScanCtx(covered_edl), scope="captions")
    check("a repainted band is annotated", "ALREADY repainted by [er1]" in r)
    check("the raw-source NOTE explains the listing",
          "does NOT mean the erase failed" in r)
    check("all-covered keeps the erase invitation off",
          "Pass one of these rectangles" not in r)

    r = agent_tools.find_burned_text(_ScanCtx({}), scope="captions")
    check("an uncovered band still invites the erase",
          "Pass one of these rectangles" in r and "ALREADY" not in r)

    blur_edl = {"effects": {"regions": [
        {"id": "rg1", "x": 0.3, "y": 0.68, "w": 0.4, "h": 0.16}]}}
    r = agent_tools.find_burned_text(_ScanCtx(blur_edl), scope="captions")
    check("a censored band is annotated too",
          "ALREADY censored by [rg1]" in r)
finally:
    inpaint.detect_text_regions = _real_detect

print(f"\nALL {PASS} CHECKS PASSED")
