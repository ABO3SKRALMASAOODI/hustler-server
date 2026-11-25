"""The named shot stays up through the phrase release on a copied Studio turn."""
from types import SimpleNamespace

import required_broll
import picture_hold


def test_existing_mining_shot_is_reused_through_release(monkeypatch):
    import broll_source
    from score import asks
    import agent_tools
    words = [dict(w=w, t0=a, t1=b) for w, a, b in [
        ("crypto", 32.53, 32.87), ("mining.", 32.87, 33.23), ("And", 33.79, 33.79)]]
    picture = dict(id="picture1", subject="mining rig", must_show="graphics cards in a rig",
                   query="open rig", source_words=words[:2])
    shots = [dict(id=k, asset_key=k, kind="image", fit="cover", start=a, duration_s=d)
             for k, a, d in [("gaming", 6.76, 1.2), ("rig", 7.96, .7), ("ai", 12.64, .78)]]
    edl = dict(keep=[[24.57, 37.99]], overlays=shots, frame=dict(ratio="9:16"))
    writes, searches = [], []
    ctx = SimpleNamespace(duration=39.367, project_id=1,
                          db=SimpleNamespace(run=lambda *args: {s["asset_key"] for s in shots}),
                          latest_edl=lambda: dict(json=edl, version=38), workdir="",
                          index=dict(words=words, video=dict(width=1920, height=1080)), stock_results={},
                          write_edl=lambda e, desc: writes.append(e) or "EDL v39",
                          user_message="When mining is mentioned show graphics cards in a mining rig")
    monkeypatch.setattr(broll_source, "search_named", lambda *a, **kw: searches.append(1) or ([], []))
    monkeypatch.setattr(agent_tools, "_remember_search_hits", lambda *a, **kw: None)
    monkeypatch.setattr(asks, "_call", lambda *a, **kw: (dict(matches=[
        dict(picture="picture1", shot="rig")], framing=[]), {}))
    # The asset reader returns the three actual shot identities; the model above selects the same rig.
    monkeypatch.setattr(picture_hold, "shot_images", lambda *a: (
        {"rig": ((1280, 720), None)}, []), raising=False)
    required_broll.supply(ctx, {"required_pictures": [picture]})
    assert searches == []
    assert writes[0]["overlays"][1]["asset_key"] == "rig"
    assert writes[0]["overlays"][1]["duration_s"] == 1.26
    assert all(all(after[k] == v for k,v in before.items())
               for before, after in zip(shots[::2], writes[0]["overlays"][::2]))
    assert shots[1]["duration_s"] == .7


def test_caption_plate_measures_the_cover_photo(tmp_path):
    from PIL import Image
    import plate
    from timeline import Timeline
    path = tmp_path / "actual.jpg"
    Image.new("L", (160, 90), 230).save(path)
    edl = dict(keep=[[0, 2]], overlays=[dict(id="still", kind="image", fit="cover",
               start=.5, duration_s=1, asset_key="asset", source_crop=[.1, .1, .9, .9])])
    probe = plate.Probe(edl, Timeline(edl["keep"]), "/speaker", (160, 90), 90, 160, 2,
                        insert_locals={"asset": str(path)})
    assert probe._plan(.49)[0] == "/speaker"
    assert probe._plan(.5)[0] == str(path)
    assert probe._plan(1.5)[0] == "/speaker"
    assert min(probe([1])[0]) >= 225


def test_focal_point_and_protected_label_survive_placement():
    from PIL import Image
    subject = [.35, .28, .53, .50]
    label = [.03, .15, .55, .52]
    frame = picture_hold.compose(Image.new("RGB", (880, 520)), subject, label, (1080, 1920))
    l, t, r, b = frame["source_crop"]
    x, y, right, bottom = frame["still_box"]

    def screen(u, v):
        return (x + (u-l)/(r-l)*(right-x), y + (v-t)/(b-t)*(bottom-y))

    mid = screen((subject[0]+subject[2])/2, (subject[1]+subject[3])/2)
    assert abs(mid[0] - .50) < .001 and abs(mid[1] - .46) < .001
    assert screen(label[0], label[1])[0] >= .05
    assert screen(label[2], label[3])[1] <= .95
    assert not screen(*subject[:2])[1] <= frame["caption_anchor_y"] <= screen(*subject[2:])[1]
