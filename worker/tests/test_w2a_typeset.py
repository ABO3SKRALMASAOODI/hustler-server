"""Track 2A: the typesetter (layout solver) and its verbs.

  * geometry is measured on the page's own font files (HarfBuzz) and every
    solved layout honours the hard constraints: inside the safe area, off
    the face zone (front) / the eyes-to-mouth core (behind), clear of the
    corner mark, no two words touching, a ladder <= 30:1 with secondary
    type >= 2.2% cap height;
  * every voice solves a phrase on 9:16, 4:5 and 16:9 within the 2 s budget;
  * intent, not pixels: the item stores no geometry and the render solves
    it per canvas (the motion_templates seam hands the page ``_layout``);
  * callbacks reuse the geometry (box IoU >= 0.95), the quote window
    replaces one word with the next;
  * add_typeset / set_typeset / remove_typeset write through
    apply_edit_batch, and the same item written by a stale client renders
    the same.
"""

import copy
import json
import time

import pytest

from w2_support import Ctx, edl, index

import motion_templates                                   # noqa: E402
import schemas                                            # noqa: E402
from tools_ext import typeset_tools as tt                 # noqa: E402
from typeset import freespace, layoutdoc, layouts, roles, shaping, solver
from typeset import voices as V

ASPECTS = {"9:16": (1080, 1920), "4:5": (1080, 1350), "16:9": (1080, 608)}
FACE = {"9:16": [(0.30, 0.22, 0.70, 0.46)], "4:5": [(0.36, 0.16, 0.64, 0.5)],
        "16:9": [(0.46, 0.14, 0.62, 0.6)]}
WM = (0.0, 0.0, 0.3, 0.12)
PHRASE = "perhaps AI is *bigger* than the internet"


def _check(doc, W, H, faces, depth):
    space = freespace.Space(W, H, faces=faces, watermark=WM)
    runs = doc["runs"]
    assert runs, doc["solver_report"]
    for r in runs:
        ink = r["ink"]
        assert space.in_safe(ink, tol=1.5), (r["text"], ink, space.safe)
        hits = space.hard_hits(ink, depth=depth)
        assert not hits, (r["text"], hits)
    for a in range(len(runs)):
        for b in range(a + 1, len(runs)):
            if doc.get("family") == "quote_window":
                break
            assert not layouts._overlap(runs[a]["ink"], runs[b]["ink"]), (
                runs[a]["text"], runs[b]["text"])
    caps = [r["cap"] for r in runs]
    assert max(caps) / min(caps) <= 30.0
    assert min(caps) >= solver.CAP_MIN_FRAC * H * 0.99


def test_shaping_uses_harfbuzz_on_the_page_fonts():
    assert shaping.ENGINE == "harfbuzz"
    sp = shaping.FontSpec("Anybody", 900, "normal", 60)
    assert sp.variations == {"wght": 900.0, "wdth": 60.0}
    narrow = shaping.shape("BIGGER", sp, 200).advance
    wide = shaping.shape("BIGGER", shaping.FontSpec("Anybody", 900, "normal",
                                                     140), 200).advance
    assert wide > 1.6 * narrow             # the width axis is applied
    # kerning: 'AV' is tighter than 'A' + 'V'
    inter = shaping.FontSpec("Inter Display", 900)
    av = shaping.shape("AV", inter, 200).advance
    assert av < shaping.shape("A", inter, 200).advance + \
        shaping.shape("V", inter, 200).advance
    css = sp.css()
    assert css["font-variation-settings"] == "'wdth' 60, 'wght' 900"


def test_every_manifest_face_is_registered_for_pages_and_the_solver():
    import motion_engine
    fams = set(motion_engine.font_families())
    for f in ("League Gothic", "Six Caps", "Anybody", "UnifrakturMaguntia",
              "Fraunces", "Noto Serif Display", "Mrs Saint Delafield",
              "Permanent Marker"):
        assert f in fams
        assert shaping.match(f) is not None


def test_roles_whisper_and_shout():
    w = roles.tokens("perhaps AI is *bigger* than the internet")
    assert [x for x, _s in w][3] == "bigger" and w[3][1]
    r = roles.assign(w)
    assert r[3] == "accent" and r[4] == "connector" and r[2] == "connector"
    assert roles.assign(roles.tokens("we got 140 characters"))[2] == "number"
    assert roles.assign(roles.tokens("it is not enough"))[2] == "negation"
    # a magazine voice promotes the strong content words to the hero rung
    r = roles.assign(w, hero_share=0.75)
    assert r[1] == "payload" and r[6] == "payload"


@pytest.mark.parametrize("aspect", sorted(ASPECTS))
@pytest.mark.parametrize("voice", V.NAMES)
def test_every_voice_solves_every_aspect_within_budget(voice, aspect):
    W, H = ASPECTS[aspect]
    t = time.monotonic()
    doc = layoutdoc.build_doc({"text": PHRASE, "voice": voice,
                               "depth": "auto"}, W=W, H=H, duration=6.0,
                              onsets=[0.1, 0.5, 2.3, 4.0, 4.2, 4.5, 4.6],
                              faces=FACE[aspect], watermark=WM)
    assert time.monotonic() - t <= 2.5
    rep = doc["solver_report"]
    _check(doc, W, H, FACE[aspect], rep.get("depth", "front"))
    assert rep["payload_cap_pct"] >= 2.2
    # every word of the phrase is shown once
    assert sorted(i for r in doc["runs"] for i in r["shows_words"]) == \
        list(range(7))


def test_words_land_on_their_onsets_and_verbs_resolve_on_them():
    doc = layoutdoc.build_doc({"text": PHRASE, "voice": "editorial_serif"},
                              W=1080, H=608, duration=6.0,
                              onsets=[0.1, 0.5, 2.3, 4.0, 4.2, 4.5, 4.6],
                              faces=FACE["16:9"], watermark=WM)
    by = {r["shows_words"][0]: r for r in doc["runs"]}
    assert by[3]["onset"] == 4.0
    for r in doc["runs"]:
        lead = layoutdoc.LEAD_S.get(r["verb"], 0.0)
        assert r["t_in"] == pytest.approx(max(0.0, r["onset"] - lead), abs=1e-3)


def test_quote_window_shows_one_word_at_a_time_in_one_place():
    doc = layoutdoc.build_doc({"text": "nobody disagrees with me",
                               "voice": "quote_window",
                               "layout": "quote_window"},
                              W=1080, H=1920, duration=3.0,
                              onsets=[0.0, 0.5, 1.0, 1.4],
                              faces=FACE["9:16"], watermark=WM)
    runs = sorted(doc["runs"], key=lambda r: r["onset"])
    assert doc["family"] == "quote_window"
    for a, b in zip(runs, runs[1:]):
        assert a["t_out"] == pytest.approx(b["onset"])
    cys = {round((r["ink"][1] + r["ink"][3]) / 2) for r in runs}
    assert max(cys) - min(cys) <= 0.02 * 1920


def test_behind_on_a_close_up_falls_back_in_front_and_says_so():
    face = [(0.24, 0.19, 0.84, 0.61)]      # a 9:16 crop of an MCU
    doc = layoutdoc.build_doc({"text": PHRASE, "voice": "magazine",
                               "depth": "behind"}, W=1080, H=1920,
                              duration=6.0, faces=face, watermark=WM)
    rep = doc["solver_report"]
    assert doc["runs"]
    assert rep.get("depth") == "front"
    assert "behind -> front" in rep.get("depth_fallback", "")


def test_behind_type_never_covers_the_eyes_or_mouth():
    face = FACE["16:9"]
    doc = layoutdoc.build_doc({"text": "it was bigger than anything else",
                               "voice": "magazine", "depth": "behind"},
                              W=1080, H=608, duration=4.0, faces=face,
                              watermark=WM)
    space = freespace.Space(1080, 608, faces=face, watermark=WM)
    for r in doc["runs"]:
        for core in space.cores:
            assert freespace.inter(r["ink"], core) == 0


def test_the_item_stores_intent_and_the_render_solves_per_canvas():
    item = {"id": "ts1", "template": "typeset", "start": 1.0, "end": 6.0,
            "params": motion_templates.normalize_params(
                "typeset", {"text": PHRASE, "voice": "whisper_shout"}),
            "reading": {"v": 1, "rows": [[0.1, 0.5, 2.3, 4.0, 4.2, 4.5, 4.6]],
                        "bridges": []}}
    docs = {}
    for name, (w, h) in (("9:16", (1080, 1920)), ("16:9", (1920, 1080))):
        job = motion_templates.build_job(copy.deepcopy(item), w, h, 30.0)
        assert '"_layout"' in job.html
        docs[name] = layoutdoc.solve_item(item, w, h, 30.0)
        assert docs[name]["canvas"]["w"] == 1080
    assert docs["9:16"]["canvas"]["h"] == 1920
    assert docs["16:9"]["canvas"]["h"] == 608
    # the stored item carries no geometry
    assert "_layout" not in item["params"]
    assert not any(k in item["params"] for k in ("x", "y", "size_px"))


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua else 0.0


def test_a_callback_reuses_the_geometry():
    p = {"text": "they promised us flying cars", "voice": "staircase_red",
         "seed": 11}
    a = layoutdoc.build_doc(p, W=1080, H=608, duration=3.0,
                            onsets=[0, 0.3, 0.6, 0.9, 1.2],
                            faces=FACE["16:9"], watermark=WM)
    from typeset import memory
    b = layoutdoc.build_doc(memory.callback_params(p, a), W=1080, H=608,
                            duration=3.4, onsets=[0, 0.35, 0.7, 1.0, 1.4],
                            faces=[(0.47, 0.15, 0.63, 0.6)], watermark=WM)
    assert b["solver_report"].get("callback") is True
    for ra, rb in zip(sorted(a["runs"], key=lambda r: r["id"]),
                      sorted(b["runs"], key=lambda r: r["id"])):
        assert _iou(ra["ink"], rb["ink"]) >= 0.95


def test_add_typeset_writes_one_item_through_apply_batch(monkeypatch):
    ctx = Ctx(edl())
    monkeypatch.setattr(tt, "_faces", lambda *a, **k: FACE["16:9"])
    out = tt.add_typeset(ctx, text=PHRASE)
    assert out.startswith("EDL v3 -> v4"), out
    m = [i for i in ctx.edl["motion"] if i["template"] == "typeset"][0]
    assert m["params"]["voice"] in V.VOICES          # auto resolved
    assert "voice:" in out
    assert m["footprint"]["box"]
    assert 0.4 <= m["start"] <= 0.8 and m["end"] >= 6.0
    # a second line gets a different voice (variety)
    out2 = tt.add_typeset(ctx, text="they promised us flying cars")
    assert out2.startswith("EDL"), out2
    ms = [i for i in ctx.edl["motion"] if i["template"] == "typeset"]
    assert ms[0]["params"]["voice"] != ms[1]["params"]["voice"]
    # set and remove
    out3 = tt.set_typeset(ctx, ms[1]["id"], voice="hairline_wide")
    assert out3.startswith("EDL"), out3
    out4 = tt.remove_typeset(ctx, [ms[0]["id"], ms[1]["id"]])
    assert out4.startswith("EDL"), out4
    assert not ctx.edl["motion"]


def test_a_stale_client_writes_the_same_item(monkeypatch):
    import edit_batch
    base = edl()
    item = {"id": "ts9", "template": "typeset", "start": 0.5, "end": 6.0,
            "layer": "above_captions",
            "params": {"text": PHRASE, "voice": "whisper_shout"}}
    out = edit_batch.apply_batch(base, [{"action": "upsert",
                                         "layer": "motion", "id": "ts9",
                                         "value": item}], 60.0, {})
    v = schemas.validate_edl(out, 60.0).model_dump()
    m = [i for i in v["motion"] if i["id"] == "ts9"][0]
    assert m["params"]["voice"] == "whisper_shout"
    out = edit_batch.apply_batch(v, [{"action": "remove", "layer": "motion",
                                      "id": "ts9"}], 60.0, {})
    assert schemas.edl_signature(out) == schemas.edl_signature(base)


def test_too_many_words_are_refused_with_the_reason():
    ctx = Ctx(edl())
    out = tt.add_typeset(ctx, text=" ".join(["word"] * 30), start=1, end=5)
    assert out.startswith("REJECTED") and "paragraph" in out


def test_typeset_template_has_no_sound_and_replaces_the_captions():
    sp = motion_templates.spec("typeset")
    assert sp["sfx"] == []
    assert sp["mutes_captions"] is True and sp["reads_onsets"] is True


def test_cards_are_owner_safe():
    import devices
    cards = devices.load_cards()
    for cid in ("typeset_lockup", "typeset_behind"):
        c = cards[cid]
        assert c["owner_flags"] == {"adds_sound": False, "adds_zoom": False,
                                    "flash": False}
        assert c["verb"] == "add_typeset"
