"""Refbench plumbing (INTERFACES §8): case schema, vfl.json, judges, verdict,
AT-0D-2 scoring, labels store + CVAT round trip. CI-safe: no footage,
no references, no models.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import numpy as np                                            # noqa: E402
import pytest                                                 # noqa: E402

from benchmarks import refbench                               # noqa: E402
from benchmarks.refbench import at0d2, judges, spec, verdict  # noqa: E402
from benchmarks.refbench import vfl as vflmod                 # noqa: E402

yaml = pytest.importorskip("yaml")


def _good_case():
    return {
        "id": "AT-1B-1", "track": "1B", "title": "silhouette flashes",
        "local_only": True, "interim": False,
        "reference": [{"ref": "22", "t": [8.43, 12.33], "approx": True}],
        "footage": [{"vfl": "V12", "window": "walkon"}],
        "music": None, "aspect": "9:16", "mode": "engine",
        "edl": {"base": "vfl_default", "ops": []}, "chat": None,
        "render": {"variant": "preview"}, "allow": [],
        "metrics": [{"name": "land_err_f", "op": "<=", "value": 1}],
        "beyond": "all 6 shots of a montage in one call",
        "board": {"stills": [8.43], "clip": [6.0, 10.0]},
        "judges": {"lenses": ["motion", "craft", "owner_rules"],
                   "pass": "2of3_no_major"}}


# ── case specs ────────────────────────────────────────────────────────────

def test_every_checked_in_case_validates():
    data = vflmod.load()
    paths = spec.all_case_paths()
    assert paths, "no case specs"
    for p in paths:
        case = spec.load(p)
        assert spec.validate(case, vfl=data) == [], p


def test_good_case_validates():
    assert spec.validate(_good_case(), vfl=vflmod.load()) == []


@pytest.mark.parametrize("patch,needle", [
    ({"id": "AT-1Z-1"}, "id"),
    ({"beyond": "  "}, "beyond"),
    ({"beyond": None}, "beyond"),
    ({"metrics": [{"name": "made_up_metric"}]}, "made_up_metric"),
    ({"metrics": [{"name": "land_err_f", "op": "=~", "value": 1}]}, "op"),
    ({"allow": ["applause"]}, "allow"),
    ({"aspect": "2:1"}, "aspect"),
    ({"mode": "agent"}, "chat"),
    ({"music": {"fixture": "HM1"}}, "allow"),
    ({"music": {"fixture": "my_song"}}, "music"),
    ({"reference": [{"ref": "77"}]}, "reference"),
    ({"footage": [{"vfl": "V12", "window": "nope"}]}, "window"),
    ({"footage": [{"vfl": "V99", "window": "x"}]}, "V99"),
    ({"local_only": False}, "local_only"),
    ({"render": {"variant": "draft"}}, "variant"),
    ({"judges": {"lenses": ["vibes"], "pass": "2of3_no_major"}}, "lenses"),
    ({"track": "1C"}, "belongs"),
])
def test_bad_cases_are_refused(patch, needle):
    case = {**_good_case(), **patch}
    errs = spec.validate(case, vfl=vflmod.load())
    assert any(needle in e for e in errs), errs


def test_case_ids_follow_the_pattern():
    for good in ("AT-0D-1", "AT-1B-12", "AT-0D-1a", "AT-2C-perf", "W1-S3",
                 "BASE-2"):
        assert spec.ID_RE.match(good)
    for bad in ("AT-5A-1", "AT-0G-1", "W5-S1", "base-1", "AT-0D"):
        assert not spec.ID_RE.match(bad)


# ── vfl.json ──────────────────────────────────────────────────────────────

def test_vfl_json_shape():
    data = vflmod.load()
    srcs = vflmod.sources(data)
    assert set(srcs) == {f"V{i:02d}" for i in range(1, 15)}
    kinds = {"person", "syllables", "fingertips", "plane_corners",
             "subject"}
    for vid, src in srcs.items():
        assert src["source"] and src["windows"], vid
        assert len(src.get("sha256", "")) == 64, vid
        for name, w in src["windows"].items():
            a, b = w["t"]
            assert a < b, (vid, name)
            for k in ("file", "file_t0", "file_span", "sha256", "fps",
                      "width", "height"):
                assert k in w, (vid, name, k)
            assert w["aligned"] is True, (vid, name)
            assert w["file_span"][0] <= a + 1e-3 and \
                b <= w["file_span"][1] + 1e-3, (vid, name)
            assert set(w.get("labels", [])) <= kinds, (vid, name)
            assert not os.path.isabs(w["file"])


def test_vfl_roadmap_windows_present():
    data = vflmod.load()
    need = {("V01", 149.44, 187.0), ("V02", 840.47, 880.51),
            ("V03", 1691.66, 1783.84), ("V04", 115.5, 160.1),
            ("V04", 8784.76, 8844.66), ("V05", 43.69, 74.44),
            ("V06", 33.88, 89.48), ("V11", 725.515, 785.615),
            ("V02", 181.6, 262.42), ("V01", 3334.4, 3399.4)}
    for vid, a, b in need:
        assert vflmod.find(vid, a, b, data), (vid, a, b)


def test_vfl_clock_helpers():
    w = {"file_t0": 835.0128}
    assert vflmod.to_file_time(w, 840.47) == pytest.approx(5.4572)
    assert vflmod.to_source_time(w, 5.4572) == pytest.approx(840.47)


# ── judges and verdicts ───────────────────────────────────────────────────

def test_judge_prompts_exist_and_fill():
    for lens in spec.LENSES:
        p = judges.prompt(lens, case_id="AT-1B-1", title="t", reference="22",
                          devices="d", metrics={}, beyond="b", allow=[],
                          edl_summary={}, standing={})
        assert "Reply with JSON only" in p
        assert "{case_id}" not in p and "{allow}" not in p


def test_judge_parse_and_combine():
    v = judges.parse('```json\n{"lens": "motion", "verdict": "exceeds", '
                     '"major_defect": false, "notes": "ok"}\n```', "motion")
    assert v["verdict"] == "exceeds"
    with pytest.raises(ValueError):
        judges.parse({"verdict": "great", "major_defect": False}, "craft")
    with pytest.raises(ValueError):
        judges.parse({"verdict": "matches"}, "craft")
    lenses = ["motion", "craft", "owner_rules"]
    mk = lambda lens, vd, maj=False: {"lens": lens, "verdict": vd,  # noqa
                                      "major_defect": maj, "notes": ""}
    assert judges.combine([mk("motion", "matches")], lenses) is None
    three = [mk("motion", "matches"), mk("craft", "below"),
             mk("owner_rules", "exceeds")]
    assert judges.combine(three, lenses) is True
    three[1] = mk("craft", "matches", True)
    assert judges.combine(three, lenses) is False
    two_below = [mk("motion", "below"), mk("craft", "below"),
                 mk("owner_rules", "matches")]
    assert judges.combine(two_below, lenses) is False


def test_packets_and_verdict_roundtrip(tmp_path):
    case = _good_case()
    metrics = {"land_err_f": {"value": 0.0, "bar": "<=1", "pass": True}}
    paths = judges.packets(case, str(tmp_path), board={"images": ["a.png"]},
                           metrics=metrics, standing={"render_ok": True})
    assert len(paths) == 3
    pk = json.load(open(paths[0]))
    assert pk["images"] == ["a.png"] and "AT-1B-1" in pk["prompt"]
    st = {"render_ok": True, "brand_integrity": True, "no_collisions": None,
          "loudness": True, "owner_rules": True, "ledger": None}
    v = verdict.build(case, render_path=None, variant="preview",
                      metrics=metrics, standing=st, judges=[])
    assert v["pass"] is None                       # judges pending
    assert v["unmeasured"] == ["no_collisions", "ledger"]
    for lens in ("motion", "craft", "owner_rules"):
        json.dump({"lens": lens, "verdict": "matches", "major_defect": False,
                   "notes": ""},
                  open(tmp_path / f"judge_{lens}.verdict.json", "w"))
    got = judges.collect(case, str(tmp_path))
    v = verdict.build(case, render_path=None, variant="preview",
                      metrics=metrics, standing=st, judges=got)
    assert v["pass"] is True
    st["owner_rules"] = False
    v = verdict.build(case, render_path=None, variant="preview",
                      metrics=metrics, standing=st, judges=got)
    assert v["pass"] is False and v["standing_failed"] == ["owner_rules"]
    p = verdict.write(v, str(tmp_path))
    assert json.load(open(p))["case"] == "AT-1B-1"
    for k in ("case", "commit", "ts", "render", "metrics", "standing",
              "devices", "judges", "pass", "board"):
        assert k in v


# ── AT-0D-2 scoring ───────────────────────────────────────────────────────

def test_frame_pr_with_dont_care():
    r = at0d2.frame_pr([1, 2, 5, 9], [1, 2, 3], (0, 10), [[9, 9]])
    assert (r["tp"], r["fp"], r["fn"]) == (2, 1, 1)
    assert r["precision"] == pytest.approx(2 / 3)
    assert r["recall"] == pytest.approx(2 / 3)
    assert r["false_alarms"] == [5] and r["misses"] == [3]


def test_run_pr():
    r = at0d2.run_pr([3, 4, 20], [3, 4, 10, 11], (0, 30))
    assert r["labelled_runs"] == 2 and r["detected_runs"] == 2
    assert r["precision"] == 0.5 and r["recall"] == 0.5


def test_boundary_pr_tolerance_transitions_dont_care():
    r = at0d2.boundary_pr([10, 21, 40, 41, 42, 70, 90],
                          cuts=[10, 20, 60], transitions=[[39, 43]],
                          dont_care=[[69, 71]], scope=(0, 100))
    assert r["found"] == 3                 # 10, 20(±1), the transition
    assert r["misses"] == [60]
    assert r["false_alarms"] == [90]
    assert r["ignored_dont_care"] == [70]
    assert r["recall"] == pytest.approx(3 / 4)
    assert r["precision"] == pytest.approx(3 / 4)


# ── labels store and CVAT round trip ─────────────────────────────────────

def test_cvat_roundtrip(tmp_path, monkeypatch):
    pytest.importorskip("cv2")
    from benchmarks.refbench import labels
    monkeypatch.setenv("VALMERA_FIXTURES_DIR", str(tmp_path))
    m = np.zeros((60, 80), bool)
    m[10:50, 20:60] = True
    m[25:35, 35:45] = False                       # a hole
    labels.write_mask("V99", "win", 7, m)
    assert labels.mask_frames("V99", "win") == [7]
    back = labels.read_mask("V99", "win", 7)
    assert (back == m).all()
    # an exported polygon set re-imports to (almost) the same mask
    xml = tmp_path / "ann.xml"
    from xml.etree import ElementTree as ET
    root = ET.Element("annotations")
    img = ET.SubElement(root, "image", id="0", name="000007.jpg",
                        width="80", height="60")
    for pts, hole in labels._polygons(m, eps=0.5):
        ET.SubElement(img, "polygon",
                      label="person_hole" if hole else "person",
                      points=";".join(f"{x},{y}" for x, y in pts))
    ET.ElementTree(root).write(xml)
    win = {"vfl": "V99", "name": "win"}
    assert labels.import_cvat(win, "person", str(xml)) == [7]
    got = labels.read_mask("V99", "win", 7)
    inter = (got & m).sum()
    union = (got | m).sum()
    assert inter / union > 0.95
    man = labels.read_manifest("V99", "win", "person")
    assert man["frames"]["7"]["reviewed"] is True


def test_cvat_rle_decode():
    from benchmarks.refbench import labels
    m = labels._rle_decode("2,3,4,3", 1, 1, 4, 3, 6, 5)
    assert m.sum() == 6
    assert m[1, 3] and m[1, 4] and m[2, 1] and not m[1, 1]


def test_sample_frames_spread_inside_window():
    from benchmarks.refbench import labels
    win = {"fps": 25.0, "t": [149.44, 187.0], "file_t0": 146.959}
    fr = labels.sample_frames(win, 30)
    assert len(fr) == 30
    t = [f / 25.0 + 146.959 for f in fr]
    assert min(t) >= 149.44 and max(t) <= 187.0


def test_paths_default_outside_git(monkeypatch, tmp_path):
    monkeypatch.delenv("VALMERA_REFBENCH_OUT", raising=False)
    assert not refbench.out_dir().startswith(refbench.WORKER)
    monkeypatch.setenv("VALMERA_REFBENCH_OUT", str(tmp_path / "o"))
    assert refbench.out_dir() == str(tmp_path / "o")
