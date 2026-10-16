"""Defects the owner's Codex podcast runs (6-16 Oct 2026) hit, fixed here.

Each test names the run, short and project the defect showed on. The triage
(every distinct defect, its evidence and status) is
.valmera/engines/codex_runs_defects.md in the owner's workspace.

Run:  PYTHONPATH=.. python -m pytest tests/test_codex_runs_fixes.py -q   (from worker/)
"""
import json
import os
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402
from schemas import default_edl, validate_edl, EDLValidationError  # noqa: E402


# ── a caption correction that changes nothing is not a passing audit ──────

def _caption_ctx(tmp_path):
    edl = default_edl(200.0)
    edl["keep"] = [[100.0, 115.0]]
    edl["captions"] = {"mode": "from_transcript"}
    words = [{"w": "while", "t0": 110.0, "t1": 110.2},
             {"w": "their", "t0": 110.2, "t1": 110.4},
             {"w": "compute", "t0": 110.4, "t1": 110.8},
             {"w": "started,", "t0": 110.8, "t1": 111.2},
             {"w": "it", "t0": 111.3, "t1": 111.4},
             {"w": "forces", "t0": 111.4, "t1": 111.8}]
    row = {"version": 1, "json": edl}
    ctx = SimpleNamespace(latest_edl=lambda: row, project_id=1,
                          index={"words": words, "video": {"width": 1920, "height": 1080}},
                          db=SimpleNamespace(run=lambda *_: None), workdir=str(tmp_path),
                          job={"id": 9}, pending_images=[])

    def write(value, _description):
        row.update(version=row["version"] + 1, json=value)
        return f"EDL v{row['version']} saved"
    ctx.write_edl = write
    return ctx


def _preview(result):
    return json.loads(result.split("CAPTION PREVIEW: ", 1)[1])


def test_the_caption_audit_names_a_correction_that_changes_no_word(tmp_path):
    """Jensen s25 (project 3762 v9-v13) and klRb0 s11/s12 (3711, 3713):
    corrections scoped in SOURCE seconds matched nothing, yet audit_captions
    listed them as active_corrections with status pass, and the render still
    read the misheard words."""
    ctx = _caption_ctx(tmp_path)
    miss = agent_tools.set_caption_fixes(ctx, [
        {"from": "compute started", "to": "compute starved",
         "start": 110.4, "end": 111.2}])
    audit = _preview(miss)
    assert audit["status"] == "warnings", audit
    assert audit["corrections_not_applied"] and \
        "PROGRAM seconds" in audit["corrections_not_applied"][0]
    assert any("change no word" in w for w in audit["warnings"])
    # the same rule in program seconds applies and the audit passes again
    ok = agent_tools.set_caption_fixes(ctx, [
        {"from": "compute started", "to": "compute starved",
         "start": 10.0, "end": 11.5}])
    audit = _preview(ok)
    assert audit["corrections_not_applied"] is None
    assert not any("change no word" in w for w in audit.get("warnings") or [])


# ── the storyboard's vision pages run side by side ─────────────────────────

def test_storyboard_pages_are_described_in_parallel_with_the_recorder(tmp_path, monkeypatch):
    """Jensen vlog (index 69728, 15 Oct): 3,064 clusters -> 64 vision calls
    one after another held the index at 90% for 1.5 h."""
    import visual_index
    # the module's own references: other tests reload llm/config
    llm, config = visual_index.llm, visual_index.config
    monkeypatch.setattr(config, "VISION_CAPTION_PARALLELISM", 4)
    monkeypatch.setattr(config, "VISUAL_SEMANTIC_SHEETS_PER_CALL", 1)
    monkeypatch.setattr(llm, "vision_available", lambda plan=None: True)
    monkeypatch.setattr(visual_index.storage, "exists", lambda key: False)
    uploads = []
    monkeypatch.setattr(visual_index.storage, "upload_file",
                        lambda path, key, mime: uploads.append(key))
    calls, lock = [0], threading.Lock()
    together = threading.Barrier(2, timeout=20)   # only met if two run at once
    recorded = []

    def ask(prompt, paths, **kw):
        with lock:
            calls[0] += 1
            first_two = calls[0] <= 2
        if first_two:
            together.wait()
        llm.record("visual_index_semantics", {"model": "m"}, {"answer": "x"})
        ids = [part.strip() for part in prompt.split("in this set: ")[1].split(".")[0].split(",")]
        return json.dumps([{"evidence_id": i, "semantic_description": f"frame {i}"}
                           for i in ids])
    monkeypatch.setattr(llm, "ask_vision", ask)
    evidence = [{"evidence_id": f"ve_{n:03d}", "frame_hash": f"{n:064x}"}
                for n in range(8)]
    sheets = [{"path": str(tmp_path / f"s{n}.jpg"), "digest": f"d{n}",
               "evidence_ids": [f"ve_{n:03d}"]} for n in range(8)]
    llm.set_recorder(lambda purpose, *a: recorded.append(purpose))
    try:
        status = visual_index._caption_semantics(evidence, sheets, str(tmp_path))
    finally:
        llm.set_recorder(None)
    assert status == "complete"
    assert all(row["semantic_description"] == f"frame {row['evidence_id']}"
               for row in evidence)
    assert not together.broken, "pages ran one after another"
    assert len(recorded) == 8               # every pool thread recorded its call
    assert len(uploads) == 8                # every frame's semantics cached


# ── a bare provider id resolves to the saved research hit ──────────────────

def _stock_ctx(kv, parent=None, results=None):
    return SimpleNamespace(
        project_id=3719, project={"parent_project_id": parent},
        stock_results=dict(results or {}),
        db=SimpleNamespace(run=lambda fn, key: kv.get(key)))


def test_a_bare_provider_id_finds_its_research_hit_here_or_on_the_parent(monkeypatch):
    """klRb0 run (15 Oct): research_broll listed 'broll:1:detroit:pexels:
    photo:15020901' (project 3719) and the editor passed 'pexels:photo:
    15020901'; s09/s25 (3720, 3731) passed ids researched on their parent
    3705. All six were refused 'unknown stock id for this project'."""
    hit = {"id": "broll:1:detroit:pexels:photo:15020901", "kind": "photo",
           "_provider_result_id": "pexels:photo:15020901"}
    ctx = _stock_ctx({}, results={hit["id"]: hit})
    found, note = agent_tools._stock_hit_by_provider_id(ctx, "pexels:photo:15020901")
    assert found is hit and note is None
    parent_hit = {"id": "broll:3:wood:pexels:photo:16725607", "kind": "photo",
                  "_provider_result_id": "pexels:photo:16725607"}
    kv = {"search_hits:stock:project:3705": json.dumps({parent_hit["id"]: parent_hit})}
    ctx = _stock_ctx(kv, parent=3705)
    found, note = agent_tools._stock_hit_by_provider_id(ctx, "pexels:photo:16725607")
    assert found["id"] == parent_hit["id"] and note is None
    # two different saved results ending in the same tail: say which
    a = {"id": "broll:1:a:pexels:photo:1", "_provider_result_id": "pexels:photo:1"}
    b = {"id": "broll:2:b:pixabay:photo:1", "_provider_result_id": "pixabay:photo:1"}
    ctx = _stock_ctx({}, results={a["id"]: a, b["id"]: b})
    found, note = agent_tools._stock_hit_by_provider_id(ctx, "photo:1")
    assert found is None and "several saved results" in note
    # nothing saved: the plain refusal
    monkeypatch.setattr(agent_tools.stock, "available", lambda: True)
    ctx = _stock_ctx({})
    assert "unknown stock id" in agent_tools.add_stock_media(ctx, "pexels:photo:9")
    # a resolved id passes the id gate (here it stops at the evidence gate)
    ctx = _stock_ctx(kv, parent=3705)
    out = agent_tools.add_stock_media(ctx, "pexels:photo:16725607")
    assert "unknown stock id" not in out and "no description" in out


# ── counters land on 'twice' and a program-time 'land' moves inside ────────

def test_a_counter_land_given_in_program_time_lands_inside_the_graphic():
    """Zuckerberg run (13 Oct, project 3624): land 4.16 on a counter at
    3.76-5.76 and land_first 13.96 / land 14.44 on one at 13.56-15.56 were
    clamped to the item's last instant: blank through 'twice', '0-0'."""
    import number_reveal
    item = {"template": "counter", "start": 3.76, "end": 5.76,
            "params": {"value": "2", "land": 4.16}}
    note = number_reveal.local_landing(item)
    assert item["params"]["land"] == pytest.approx(0.4) and "PROGRAM time" in note
    rng = {"template": "counter", "start": 13.56, "end": 15.56,
           "params": {"value": "1–2", "land_first": 13.96, "land": 14.44}}
    number_reveal.local_landing(rng)
    assert rng["params"]["land_first"] == pytest.approx(0.4)
    assert rng["params"]["land"] == pytest.approx(0.88)
    ok = {"template": "counter", "start": 1.0, "end": 3.0, "params": {"land": 0.5}}
    assert number_reveal.local_landing(ok) == "" and ok["params"]["land"] == 0.5


def test_once_and_twice_are_spoken_numbers():
    import number_reveal
    words = [{"w": w, "t0": t, "t1": t + 0.2} for w, t in
             (("maybe", 0.0), ("twice", 0.3), ("a", 0.6), ("year,", 0.7),
              ("once", 2.0), ("or", 2.2), ("twice", 2.4), ("a", 2.6), ("week", 2.7))]
    said = [(sorted(n["values"]), n["t0"]) for n in number_reveal.spoken_numbers(words)]
    assert said == [([2.0], 0.3), ([1.0], 2.0), ([2.0], 2.4)]


# ── set_overlay_motion: a still moves with animate_still; width is capped ──

class _OverlayCtx:
    def __init__(self, edl):
        self._edl = validate_edl(edl, 5.0).model_dump()
        self.writes = []

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": self._edl}

    def write_edl(self, edl, description):
        self._edl = validate_edl(edl, 5.0).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes) + 1}: {description}"


def test_a_full_frame_still_is_pointed_to_animate_still():
    """PG s02 (3585: four overlays), klRb0 s03/s06 (3715, 3717): a slow
    1.0 -> 1.04 push on fit=picture stills was refused without a way."""
    edl = default_edl(5.0)
    edl["overlays"] = [{"id": "ov1", "asset_key": "image.png", "kind": "image",
                        "start": 0.0, "duration_s": 3.0, "fit": "picture"}]
    out = agent_tools.set_overlay_motion(_OverlayCtx(edl), "ov1", {
        "scale": [{"t": 0, "v": 1.0}, {"t": 3, "v": 1.04}]})
    assert out.startswith("REJECTED") and "animate_still(id='ov1')" in out


def test_a_pip_scale_past_full_width_is_named_not_silently_flattened():
    """Jensen editor B (16 Oct): 'free overlay scale greater than 1 silently
    clamps to 1'."""
    edl = default_edl(5.0)
    edl["overlays"] = [{"id": "ov1", "asset_key": "image.png", "kind": "image",
                        "start": 0.0, "duration_s": 3.0}]
    out = agent_tools.set_overlay_motion(_OverlayCtx(edl), "ov1", {
        "scale": [{"t": 0, "v": 1.0}, {"t": 3, "v": 1.08}]})
    assert out.startswith("EDL v") and "WIDTH" in out and "1.08" in out


# ── apply_edit_batch: near misses a batch plainly meant ────────────────────

def _batch(before, ops, notes=None):
    import edit_batch
    return edit_batch.apply_batch(before, ops, 20.0, {}, notes=notes)


def test_a_batch_cut_transition_and_card_near_misses_are_normalized():
    """PG s02 (3585), Zuckerberg s21 (3632): transition style 'cut'/'none';
    Jensen s13/s27 (3754, 3763): picture card follow false, fit 'contain',
    duration_s 0 - each a rejected batch."""
    before = default_edl(20.0)
    before["keep"] = [[0.0, 10.0]]
    before = validate_edl(before, 20.0).model_dump()
    notes = []
    effects = {"transition": {"style": "cut"}, "picture_cards": [
        {"id": "c1", "start": 0.0, "end": 4.0, "box": [0, .24, 1, .794],
         "source": [0.1, 0.0, 0.6, 1.0], "follow": False, "fit": "contain",
         "duration_s": 0, "entrance": "none", "exit": "none"}]}
    out = _batch(before, [{"action": "set", "layer": "effects", "value": effects}], notes)
    card = out["effects"]["picture_cards"][0]
    assert out["effects"].get("transition") is None
    assert card["fit"] == "pad" and card["duration_s"] == pytest.approx(0.12)
    assert any("plain cut" in n for n in notes) and any("follow false" in n for n in notes)
    effects["picture_cards"][0]["follow"] = True
    with pytest.raises(ValueError, match="not a switch"):
        _batch(before, [{"action": "set", "layer": "effects", "value": effects}])


def test_caption_style_fields_at_the_top_level_say_where_they_go():
    """klRb0 s16 (3725): 'Unknown field in captions: anchor_y, background,
    ... size_scale.' cost a batch with no hint."""
    before = default_edl(20.0)
    before["keep"] = [[0.0, 10.0]]
    before = validate_edl(before, 20.0).model_dump()
    with pytest.raises(ValueError, match="put them under captions.style"):
        _batch(before, [{"action": "set", "layer": "captions", "value": {
            "mode": "from_transcript", "font": "Inter", "size_scale": 0.9}}])


# ── pixel units read as pixels ──────────────────────────────────────────────

def test_a_pixel_box_is_stored_as_frame_fractions():
    """Zuckerberg s23 (3629 v15): html payoff box [50, 1030, 1030, 1290] was
    refused 'motion[3].box is empty'."""
    import motion_tools
    edl = default_edl(30.0)
    edl["frame"] = {"ratio": "9:16"}
    ctx = SimpleNamespace(index={"video": {"width": 1920, "height": 1080}})
    box, note = motion_tools._box_in_fractions(ctx, edl, [50, 1030, 1030, 1290])
    W, H = motion_tools._canvas_size(ctx, edl)
    assert box == [round(50 / W, 4), round(1030 / H, 4), round(1030 / W, 4), round(1290 / H, 4)]
    assert "pixels" in note
    same, none = motion_tools._box_in_fractions(ctx, edl, [0.1, 0.5, 0.9, 0.7])
    assert same == [0.1, 0.5, 0.9, 0.7] and none == ""


def test_a_pixel_font_size_on_add_text_is_read_as_pixels():
    """PG s16 (3600: font_size 30), Zuckerberg s28 (3639: 24): refused
    'font_size must be between 0.012 and 0.3'."""
    edl = default_edl(30.0)
    edl["keep"] = [[0.0, 20.0]]
    edl["frame"] = {"ratio": "9:16"}
    row = {"version": 1, "json": validate_edl(edl, 30.0).model_dump()}
    ctx = SimpleNamespace(latest_edl=lambda: row, project_id=1,
                          index={"video": {"width": 1920, "height": 1080}})

    def write(value, description):
        row.update(version=row["version"] + 1, json=validate_edl(value, 30.0).model_dump())
        return f"EDL v{row['version']}: {description}"
    ctx.write_edl = write
    out = agent_tools.add_text(ctx, "Rhode Island School of Design", 2.0, 4.0,
                               template="subtitle", font_size=30)
    assert out.startswith("EDL v"), out
    assert "FONT SIZE: 30 read as pixels" in out
    size = row["json"]["texts"][0]["font_size"]
    assert 0.012 <= size <= 0.3


# ── overlapping picture cards are named ─────────────────────────────────────

def test_overlapping_picture_cards_name_both_cards():
    """klRb0 s07 (3710), Jensen s27 (3763): 'Picture-card windows must not
    overlap.' named neither card."""
    edl = default_edl(20.0)
    edl["keep"] = [[0.0, 10.0]]
    edl["effects"] = {"picture_cards": [
        {"id": "first", "start": 0.0, "end": 6.0, "box": [0, .24, 1, .794]},
        {"id": "second", "start": 5.0, "end": 9.0, "box": [0, .24, 1, .794]}]}
    with pytest.raises(EDLValidationError, match="'second' .* starts before 'first'"):
        validate_edl(edl, 20.0)


# ── a stock clip over the byte cap falls back to a smaller rendition ───────

def test_a_stock_clip_over_the_cap_uses_the_next_smaller_rendition(monkeypatch, tmp_path):
    """Zuckerberg s28 (3639, 14 Oct): two Pexels clips refused 'file is 234 MB,
    over the 90 MB limit' — the 4K file was the only one covering 1080x1920."""
    import net_fetch
    import stock
    files = [{"link": f"https://v/{w}", "width": w, "height": h, "file_type": "video/mp4"}
             for w, h in ((3840, 2160), (2560, 1440), (1920, 1080), (1280, 720), (640, 360))]
    item = {"kind": stock.KIND_VIDEO, "_files": files}
    tried = []

    def download(url, out_path, max_bytes, timeout_s):
        tried.append(url)
        if url.endswith("3840") or url.endswith("2560"):
            raise net_fetch.FetchError("file is 234 MB, over the 90 MB limit")
    monkeypatch.setattr(net_fetch, "download", download)
    stock.download(item, str(tmp_path / "s.mp4"), 1080, 1920)
    assert tried == ["https://v/3840", "https://v/2560", "https://v/1920"]
    assert (item["picked_width"], item["picked_height"]) == (1920, 1080)
    assert item["source_url"] == "https://v/1920"
    # another failure is not retried; nothing under 720 lines is tried
    tried.clear()
    monkeypatch.setattr(net_fetch, "download", lambda url, *a, **k: (
        tried.append(url), (_ for _ in ()).throw(net_fetch.FetchError("file is 99 MB, over the 90 MB limit")))[1])
    with pytest.raises(net_fetch.FetchError):
        stock.download({"kind": stock.KIND_VIDEO, "_files": files}, str(tmp_path / "t.mp4"), 1080, 1920)
    assert "https://v/640" not in tried


# ── the render review quotes the corrected words ────────────────────────────

def test_the_review_reads_the_corrected_words():
    """Jensen s13 (3754 v10): the render report's EARN ITS PLACE quoted
    'their compute started' over a caption corrected to 'starved'."""
    import edit_review
    edl = default_edl(200.0)
    edl["keep"] = [[100.0, 115.0]]
    edl["captions"] = {"mode": "from_transcript", "corrections": [
        {"from": "compute started", "to": "compute starved", "start": 10.0, "end": 11.5}]}
    index = {"words": [{"w": "their", "t0": 110.2, "t1": 110.4},
                       {"w": "compute", "t0": 110.4, "t1": 110.8},
                       {"w": "started,", "t0": 110.8, "t1": 111.2}]}
    words = [w["w"] for w in edit_review._Program(edl, index).kept]
    assert "starved" in words and not any(w.startswith("started") for w in words)
