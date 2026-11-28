import hashlib
import json
from types import SimpleNamespace

from wf1_publisher_reading import local_coverage
from wf1_coverage import proposals
import wf1_short as wf


def test_publishers_read_over_exact_retained_insets_without_scouting_neighbour(tmp_path):
    camera = dict(name="Interview", first=0,last=21,t0=0.,t1=9.,say="the opening concern",must_show="policy")
    other = dict(name="Independent example",first=22,last=30,t0=9.,t1=12.,say="the example",must_show="record")
    windows = [camera, other]
    selected = dict(camera_windows=[0],windows=[{k:w[k] for k in ("first","last","say")} for w in windows])
    asset = tmp_path/"checked.png"
    asset.write_bytes(b"approved image")
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    crop = [.1,.15,.83,.68]
    prior = dict(path=str(asset),sha256=digest,source="publication", f=[70,162],clause=camera["say"],
                 clause_t=[0.,9.], approved_crop=crop,approved_media=digest)
    coverage = dict(windows=windows,passed_placements={"0":[prior]})
    for legacy in ("Andrew Lear", None, {}, dict(camera_windows=[0]),
                   dict(camera_windows=None, windows=None), dict(camera_windows=[1, -1], windows=[camera])):
        assert local_coverage(coverage, legacy) == coverage
    seated = local_coverage(coverage, selected)
    photos = {"Interview":[dict(path=str(asset),page="publication"),dict(path="/unused.png")]}
    rows = proposals(seated,{},photos)
    assert [(r[:2],r[4]["path"],r[4]["approved_crop"]) for r in rows] == [([70,162],str(asset),crop)]
    assert seated["windows"][1] is other and "publisher_camera" not in camera
    assert not local_coverage(dict(windows=[dict(camera,last=23)]),selected)["windows"][0].get("publisher_camera")
    measured = dict(holds=[[0,12]],longest_hold_s=12.,cuts_per_10s=2.)
    ctx = SimpleNamespace(supply=str(tmp_path),log=lambda message:None)
    (tmp_path/"pack.json").write_text(json.dumps(dict(pack=dict(coverage=coverage))))
    rendered = tmp_path/"programme.mp4"
    rendered.touch()
    sc=dict(beats=dict(publisher_reading=selected,pictures=[]))
    assert wf._rebuild_holds(ctx,{},sc,dict(film=str(rendered),measured_cuts=measured),defer=True)
    assert sc["beats"]["research_windows"] == [1]


def test_saved_reading_stays_on_the_exact_transcript_window():
    saved = dict(first=3, last=6, say="they changed the ranking")
    plan = dict(readings=[dict(first=3, last=6, evidence="source receipt")], windows=[saved])
    window = dict(saved, name="Ranking", t0=1, t1=3)
    assert local_coverage({"windows": [window]}, plan)["windows"][0]["publisher_camera"]
    revised = dict(window, say="they proposed a ranking")
    assert local_coverage({"windows": [revised]}, plan)["windows"] == [revised]


def test_legacy_camera_plan_keeps_the_approved_image_and_ignores_replaced_pixels(tmp_path):
    path = tmp_path / "approved.png"
    path.write_bytes(b"approved")
    snapshot = dict(first=0, last=2, say="read the record")
    window = dict(snapshot, name="Record", t0=0., t1=3., must_show="page")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    inset = dict(path=str(path), sha256=digest, approved_media=digest, approved_crop=[.1,.2,.8,.9],
                 f=[10,60], clause=window["say"], clause_t=[0.,3.], source="publication")
    coverage = local_coverage(dict(windows=[window], passed_placements={"0":[inset]}),
                              dict(camera_windows=[0], windows=[snapshot]))
    photos = {"Record":[dict(path=str(path), page="publication")]}
    rows = proposals(coverage, {}, photos)
    assert [(row[4]["path"], row[4]["approved_crop"]) for row in rows] == [(str(path), inset["approved_crop"])]
    path.write_bytes(b"replaced")
    assert proposals(coverage, {}, photos) == []
