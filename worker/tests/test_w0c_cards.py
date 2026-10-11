"""Device contract (track 0C; INTERFACES.md §5, §6.3): the card schema, the
compiler, the invariants every card must keep, lane features and the
resolver that picks each device's rung — full, reduced, substitute or omit —
so a creative device degrades instead of failing a render."""

import json
import os

import pytest

from w0c_support import (card, owner_flag_cards, six_matte_cards, use_cards,
                         write_cards)

import devices  # noqa: E402
import faultinject  # noqa: E402
from devices import compiler, schema  # noqa: E402

PINNED_METRICS = ("land_err_f", "matte_iou", "matte_flicker", "edge_chatter",
                  "glyph_visible", "legibility", "reproj_rms_px",
                  "region_locality", "duration_exact", "flash_risk",
                  "determinism", "cost_ratio")


@pytest.fixture(autouse=True)
def _fresh_cache():
    devices._cached_cards.cache_clear()
    yield
    devices._cached_cards.cache_clear()


# ── the schema ──────────────────────────────────────────────────────────

def test_the_roadmap_example_card_is_valid():
    assert schema.validate(card("subject_fill_flash")) == []
    for c in list(six_matte_cards().values()) + \
            list(owner_flag_cards().values()):
        assert schema.validate(c) == [], c["id"]


@pytest.mark.parametrize("mutate, error", [
    (lambda c: c.pop("owner_flags"), "missing owner_flags"),
    (lambda c: c.update(colour="red"), "unknown field"),
    (lambda c: c.update(id="Bad-Id"), "id must match"),
    (lambda c: c.update(family="sparkle"), "family"),
    (lambda c: c.update(stage="post"), "stage"),
    (lambda c: c.update(status="maybe"), "status"),
    (lambda c: c["needs"].update(sidecars=["matte.subject"]),
     "not a pinned sidecar kind"),
    (lambda c: c["needs"].update(lane_features=["gpu"]), "lane_features"),
    (lambda c: c["needs"].update(model="rvm"), "needs: unknown key"),
    (lambda c: c.update(binds_to=["vibe"]), "unknown moment"),
    (lambda c: c.update(binds_to=[]), "binds_to"),
    (lambda c: c["budget"].pop("charged"), "budget must give"),
    (lambda c: c["budget"]["calm"].update(per_short=-1), ">= 0"),
    (lambda c: c["budget"]["calm"].update(per_short=9),
     "must not shrink"),
    (lambda c: c["owner_flags"].pop("adds_zoom"), "owner_flags"),
    (lambda c: c["owner_flags"].update(flash="yes"), "owner_flags"),
    (lambda c: c.update(rungs=["full", "reduced"]), "end with omit"),
    (lambda c: c.update(rungs=["omit", "full", "omit"]), "once"),
    (lambda c: c.update(rungs=["reduced", "full", "omit"]), "best-first"),
    (lambda c: c.update(rungs=["full", "substitute", "omit"]),
     "{substitute"),
    (lambda c: c.update(rungs=["full", {"substitute": "nowhere"}, "omit"]),
     "neither a card nor a built-in"),
    (lambda c: c.update(rungs=["full", {"substitute": c["id"]}, "omit"]),
     "substitute itself"),
    (lambda c: c["cost"]["ms_per_frame"].update(gpu=1.0), "cost.ms_per_frame"),
    (lambda c: c.update(exemplars=["the one with the jacket"]), "exemplars"),
    (lambda c: c.update(verify=["looks good"]), "verify"),
    (lambda c: c.update(flag="lowercase_flag"), "flag names"),
    (lambda c: c.update(description="x" * 201), "description"),
])
def test_every_contract_field_is_enforced(mutate, error):
    c = card("subject_fill_flash")
    mutate(c)
    errs = schema.validate(c)
    assert any(error in e for e in errs), errs


def test_a_substitute_may_name_another_card():
    cards = six_matte_cards()
    c = card("subject_fill_flash", rungs=[
        "full", {"substitute": "subject_glow"}, "omit"])
    assert schema.validate(c, cards) == []
    assert schema.validate(c) != []          # without the set it is unknown


# ── the compiler: YAML is authored, JSON is what lanes read ─────────────

def test_compiler_round_trips_and_detects_stale_cards(tmp_path):
    pytest.importorskip("yaml")
    cards = {"subject_fill_flash": card("subject_fill_flash")}
    write_cards(str(tmp_path), cards, compiled=False, yaml_too=True)
    stale = compiler.compile_dir(str(tmp_path), check=True)
    assert stale["subject_fill_flash"][1] is False      # no JSON yet
    compiler.compile_dir(str(tmp_path))
    with open(tmp_path / "subject_fill_flash.json", encoding="utf-8") as f:
        assert json.load(f) == cards["subject_fill_flash"]
    assert compiler.compile_dir(str(tmp_path), check=True)[
        "subject_fill_flash"][1] is True
    assert devices.load_cards(str(tmp_path)) == cards
    # A YAML edit without recompiling is stale; an invalid card fails.
    import yaml
    edited = dict(cards["subject_fill_flash"], status="beta")
    (tmp_path / "subject_fill_flash.yaml").write_text(
        yaml.safe_dump(edited, sort_keys=False, allow_unicode=True),
        encoding="utf-8")
    assert compiler.compile_dir(str(tmp_path), check=True)[
        "subject_fill_flash"][1] is False
    (tmp_path / "subject_fill_flash.yaml").write_text(
        yaml.safe_dump(dict(edited, rungs=["full"])), encoding="utf-8")
    with pytest.raises(ValueError, match="end with omit"):
        compiler.compile_dir(str(tmp_path))


def test_a_card_whose_id_is_not_its_file_name_never_loads(tmp_path):
    (tmp_path / "other_name.json").write_text(
        compiler.dumps(card("subject_fill_flash")), encoding="utf-8")
    with pytest.raises(ValueError, match="id must equal the file name"):
        devices.load_cards(str(tmp_path))


def test_an_invalid_compiled_card_fails_at_load(tmp_path):
    bad = card("subject_fill_flash", rungs=["full"])
    (tmp_path / "subject_fill_flash.json").write_text(
        compiler.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid device cards"):
        devices.load_cards(str(tmp_path))


def test_cards_are_loaded_without_yaml_at_runtime():
    src = open(devices.__file__, encoding="utf-8").read()
    assert "import yaml" not in src and "yaml." not in src
    assert "import yaml" not in open(schema.__file__, encoding="utf-8").read()


# ── invariants over every LIVE card (every later wave inherits these) ───

def _live_tool_names():
    import skill_validator
    import pathlib
    return skill_validator.live_tool_names(pathlib.Path(
        os.path.dirname(devices.CARDS_DIR)).parent / "agent_tools.py")


def _metric_names():
    try:
        import qa_metrics
        return set(qa_metrics.METRICS)
    except Exception:  # noqa: BLE001 — 0D's registry not merged yet
        return set(PINNED_METRICS)


def test_every_live_card_is_compiled_valid_and_owner_safe():
    """The gate later waves inherit: whatever cards ship, each one compiles
    from its YAML, honours the schema, names a live verb, a registered fx
    type, real metrics and an existing skill, and keeps the owner rules."""
    yaml = pytest.importorskip("yaml")  # noqa: F841
    stale = [os.path.basename(p) for p, ok in
             compiler.compile_dir(check=True).values() if not ok]
    assert not stale, ("recompile: cd worker && python -m devices.compiler",
                       stale)
    cards = devices.load_cards()
    tools = _live_tool_names()
    import schemas
    metrics = _metric_names()
    skills_dir = os.path.join(os.path.dirname(devices.CARDS_DIR), os.pardir,
                              "skills")
    for cid, c in cards.items():
        assert c["verb"] in tools, (cid, c["verb"])
        if c.get("fx_type"):
            assert c["fx_type"] in schemas.FX_TYPES, (cid, c["fx_type"])
        for v in c["verify"]:
            assert schema.verify_metric(v) in metrics, (cid, v)
        if c.get("skill"):
            assert os.path.exists(os.path.join(skills_dir,
                                               c["skill"] + ".md")), cid
        flags = c["owner_flags"]
        if flags["adds_zoom"] or flags["adds_sound"]:
            assert not devices.proposable(c, asked=())[0], cid
        if c["status"] == "dark":
            assert devices.resolve(cid, lane="batch",
                                   sidecar_status={})[0] == "omit"


def test_no_look_or_default_reaches_a_zoom_or_sound_card():
    """Owner rule 3: a card that adds a zoom or a sound is never part of a
    look or format-pack default."""
    import agent_tools
    cards = devices.load_cards()
    loud = {cid for cid, c in cards.items()
            if c["owner_flags"]["adds_zoom"] or c["owner_flags"]["adds_sound"]}
    text = json.dumps(agent_tools.LOOKS, default=str)
    for cid in loud:
        assert cid not in text, cid


# ── proposability: zoom and sound only when asked ───────────────────────

def test_zoom_and_sound_cards_are_never_proposed_unless_asked(monkeypatch):
    cards = owner_flag_cards()
    zoom, sound = cards["punch_zoom_hit"], cards["whoosh_on_turn"]
    assert devices.proposable(zoom) == (False,
                                        "adds a zoom the user did not ask for")
    assert devices.proposable(sound)[0] is False
    assert devices.proposable(zoom, asked={"zoom"})[0] is True
    assert devices.proposable(sound, asked={"sfx"})[0] is True
    assert devices.proposable(sound, asked={"zoom"})[0] is False
    # dark stays dark until its flag is on
    strobe = cards["strobe_section"]
    monkeypatch.delenv("W0C_TEST_STROBE_FLAG", raising=False)
    assert devices.proposable(strobe) == (False, "dark")
    monkeypatch.setenv("W0C_TEST_STROBE_FLAG", "1")
    assert devices.proposable(strobe)[0] is True
    assert devices.proposable(card("subject_fill_flash"))[0] is True


# ── lanes ───────────────────────────────────────────────────────────────

def test_named_lanes_state_the_production_facts():
    batch = devices.lane_features("batch")
    inter = devices.lane_features("interactive")
    assert {"chromium", "onnx.rvm", "onnx.u2net", "whisper",
            "demucs"} <= batch
    assert "chromium" in inter and not {"onnx.rvm", "onnx.u2net"} & inter
    for lane in ("agent", "mcp", "shorts"):
        feats = devices.lane_features(lane)
        assert "chromium" not in feats, lane
        assert not any(f.startswith("onnx.") and f != "onnx.yunet"
                       for f in feats), lane
        assert "ffmpeg:maskedmerge" in feats
    assert "chromium" not in devices.lane_features("vision")
    with pytest.raises(ValueError):
        devices.lane_features("gpu")


def test_faults_remove_what_they_simulate_missing():
    with faultinject.inject("model_missing:matte.person"):
        feats = devices.lane_features("batch")
        assert not {"onnx.rvm", "onnx.u2net"} & feats
        assert "chromium" in feats
    with faultinject.inject("model_missing"):
        assert not any(f.startswith("onnx.")
                       for f in devices.lane_features("batch"))
    with faultinject.inject("ffmpeg_filter_missing:maskedmerge"):
        feats = devices.lane_features("interactive")
        assert "ffmpeg:maskedmerge" not in feats
        assert "ffmpeg:xfade" in feats
    with faultinject.inject("ffmpeg_filter_missing"):
        assert not any(f.startswith("ffmpeg:")
                       for f in devices.lane_features("batch"))
    assert "onnx.rvm" in devices.lane_features("batch")


def test_the_local_lane_measures_this_process():
    feats = devices.lane_features(None)
    import shutil
    if shutil.which("ffmpeg"):
        assert "ffmpeg:overlay" in feats


# ── the resolver ────────────────────────────────────────────────────────

def test_full_when_everything_is_ready(monkeypatch, tmp_path):
    use_cards(monkeypatch, six_matte_cards(), tmp_path)
    rung, why = devices.resolve("subject_fill_flash", lane="batch",
                                sidecar_status={"matte.person": "ready"})
    assert (rung, why) == ("full", "ok")


def test_a_pending_sidecar_takes_the_substitute(monkeypatch, tmp_path):
    use_cards(monkeypatch, six_matte_cards(), tmp_path)
    d = devices.resolve_detail("subject_fill_flash", lane="interactive",
                               sidecar_status={"matte.person": "pending"})
    assert d["rung"] == "substitute" and d["substitute"] == "full_frame_flash"
    assert d["params"] == {"intensity": 0.6}
    assert "matte.person pending" in d["reason"]
    # status may be a callable (sidecars.status adapter)
    d = devices.resolve_detail("subject_fill_flash", lane="batch",
                               sidecar_status=lambda kind: "failed")
    assert d["rung"] == "substitute"


def test_budget_picks_reduced_before_substitute(monkeypatch, tmp_path):
    use_cards(monkeypatch, six_matte_cards(), tmp_path)
    ready = {"matte.person": "ready"}
    full = devices.estimate_ms(six_matte_cards()["poster_silhouette"],
                               "batch", 300)
    d = devices.resolve_detail("poster_silhouette", lane="batch",
                               sidecar_status=ready, frames=300,
                               budget_ms=full * 0.6)
    assert d["rung"] == "reduced" and "over the" in d["reason"]
    d = devices.resolve_detail("poster_silhouette", lane="batch",
                               sidecar_status=ready, frames=300,
                               budget_ms=full * 0.25)
    assert d["rung"] == "substitute"            # reduced is still too dear
    assert devices.resolve("poster_silhouette", lane="batch",
                           sidecar_status=ready, budget_ms=0.0,
                           frames=300)[0] == "omit"


def test_a_lane_without_the_feature_never_runs_full(monkeypatch, tmp_path):
    use_cards(monkeypatch, six_matte_cards(), tmp_path)
    ready = {"matte.person": "ready"}
    # the agent lanes have no Chromium: text behind the head substitutes
    d = devices.resolve_detail("text_behind_head", lane="agent",
                               sidecar_status=ready)
    assert d["rung"] == "substitute" and "lane lacks chromium" in d["reason"]
    with faultinject.inject("ffmpeg_filter_missing:maskedmerge"):
        assert devices.resolve("subject_fill_flash", lane="batch",
                               sidecar_status=ready)[0] == "substitute"


def test_a_substitute_card_resolves_on_its_own_needs(monkeypatch, tmp_path):
    cards = six_matte_cards()
    cards["subject_fill_flash"] = card("subject_fill_flash", rungs=[
        "full", {"substitute": "subject_glow"},
        {"substitute": "full_frame_flash"}, "omit"])
    use_cards(monkeypatch, cards, tmp_path)
    # the glow needs the matte too, so the chain continues to the flash
    d = devices.resolve_detail("subject_fill_flash", lane="batch",
                               sidecar_status={"matte.person": "absent"})
    assert d["substitute"] == "full_frame_flash"
    assert "subject_glow" in d["reason"]


def test_dark_unknown_and_broken_never_raise(monkeypatch, tmp_path):
    use_cards(monkeypatch, owner_flag_cards(), tmp_path)
    monkeypatch.delenv("W0C_TEST_STROBE_FLAG", raising=False)
    assert devices.resolve("strobe_section", lane="batch",
                           sidecar_status={}) == ("omit", "dark")
    monkeypatch.setenv("W0C_TEST_STROBE_FLAG", "1")
    assert devices.resolve("strobe_section", lane="batch",
                           sidecar_status={})[0] == "full"
    assert devices.resolve("no_such_device", lane="batch",
                           sidecar_status={}) == ("omit", "unknown device")
    assert devices.resolve("strobe_section", lane="gpu",
                           sidecar_status={})[0] == "omit"
    # a broken card set degrades every device, it never raises
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "x_card.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(devices, "CARDS_DIR", str(broken))
    devices._cached_cards.cache_clear()
    rung, why = devices.resolve("x_card", lane="batch", sidecar_status={})
    assert rung == "omit" and why.startswith("cards unavailable")


def test_an_unreadable_cost_model_or_bad_items_never_raise(monkeypatch,
                                                           tmp_path):
    """The resolver sits on the render path (fxgraph.prepare): whatever
    breaks under it — the cost model file, a malformed fx item, a broken
    card set — the device is omitted with the reason, the render goes on."""
    use_cards(monkeypatch, six_matte_cards(), tmp_path)

    def gone():
        raise FileNotFoundError("cost_model.json")
    monkeypatch.setattr(devices, "cost_model", gone)
    rung, why = devices.resolve("subject_glow", lane="batch",
                                sidecar_status={"matte.person": "ready"},
                                frames=12)
    assert rung == "omit" and "resolver error" in why
    rows = devices.plan_rows(
        [None, {"id": "a", "device": ["not", "an", "id"]},
         {"id": "b", "device": "subject_glow", "start": "x", "end": None}],
        lane="batch", sidecar_status={"matte.person": "ready"},
        budget_ms="lots")
    assert [r["rung"] for r in rows] == ["omit", "omit", "omit"]
    monkeypatch.setattr(devices, "load_cards",
                        lambda *_a: (_ for _ in ()).throw(ValueError("bad")))
    rows = devices.plan_rows([{"id": "c", "device": "subject_glow"}],
                             lane="batch", sidecar_status={})
    assert rows[0]["rung"] == "omit"
    assert rows[0]["reason"].startswith("cards unavailable")


def test_plan_rows_spend_one_budget_in_program_order(monkeypatch, tmp_path):
    use_cards(monkeypatch, six_matte_cards(), tmp_path)
    items = [{"id": f"fx{i}", "device": "poster_silhouette",
              "stage": "world", "start": i * 2.0, "end": i * 2.0 + 1.0}
             for i in range(4)]
    one = devices.estimate_ms(six_matte_cards()["poster_silhouette"],
                              "batch", 30)
    rows = devices.plan_rows(items, lane="batch",
                             sidecar_status={"matte.person": "ready"},
                             budget_ms=one * 2.5, fps=30.0)
    assert [r["rung"] for r in rows][:2] == ["full", "full"]
    assert rows[2]["rung"] in ("reduced", "substitute")
    assert set(rows[0]) == {"id", "device", "family", "stage", "rung",
                            "reason", "est_ms"}
    assert rows[0]["family"] == "matte_fx"
    assert devices.degraded(rows) == rows[2:]


# ── cost model ──────────────────────────────────────────────────────────

def test_cost_model_shape_and_budgets():
    m = devices.cost_model()
    assert {"local", "interactive", "batch"} <= set(m["lanes"])
    assert m["budgets"]["preview_overhead"] == 0.30
    assert m["budgets"]["final_p95_s"] == 240.0
    base = devices.baseline_ms(30.0, 1080, 1920, preview=True)
    assert devices.render_budget_ms(base, preview=True) == \
        pytest.approx(base * 0.30, abs=0.2)
    final = devices.baseline_ms(60.0, 1080, 1920, preview=False,
                                lane="batch")
    par = m["lanes"]["batch"]["parallel"]
    assert devices.render_budget_ms(final, preview=False, lane="batch") == \
        pytest.approx(240000.0 * par - final, abs=0.2)
    assert devices.wall_ms(par * 1000.0, lane="batch") == 1000.0
    assert devices.baseline_ms(60, 1080, 1920) > \
        devices.baseline_ms(30, 1080, 1920)
    assert devices.baseline_ms(30, 1080, 1920, lane="interactive") > \
        devices.baseline_ms(30, 1080, 1920, lane="local")


def test_device_cost_scales_by_lane_rung_and_length():
    c = card("subject_fill_flash")
    assert devices.estimate_ms(c, "interactive", 100) == 300 + 400
    assert devices.estimate_ms(c, "batch", 100) == 300 + 250
    assert devices.estimate_ms(c, "batch", 100, "reduced") == 275
    assert devices.estimate_ms(c, "batch", 100, "omit") == 0.0
    no_cost = dict(c, cost={})
    assert devices.estimate_ms(no_cost, "batch", 10) > 0


def test_a_card_costed_on_one_lane_estimates_on_every_lane():
    c = card("subject_fill_flash", cost={"ms_per_frame": {"vision": 2.0},
                                         "fixed_ms": 0})
    for lane in devices.LANES:
        assert devices.estimate_ms(c, lane, 10) > 0, lane
