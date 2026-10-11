"""Track 1F: suggest_devices, set_intensity and audit_motion (ROADMAP 1F,
worker/devices/INTERFACES_W1.md §8).

Pure parts run here; everything that writes Wave 1 fx items runs in a child
process with every Wave 1 member registered (tests/w1f_preview_probe.py),
through the real edit_batch.apply_batch — the stale-client path — with the
stand-in device cards of tests/fixtures/w1f_cards.
"""

import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)

import device_planner   # noqa: E402
import devices          # noqa: E402
import preference_memory  # noqa: E402
from devices import schema as card_schema   # noqa: E402

CARDS = os.path.join(HERE, "fixtures", "w1f_cards")


# ── the stand-in cards honour the card contract ─────────────────────────

def test_stand_in_cards_are_valid_cards():
    cards = {}
    for f in sorted(os.listdir(CARDS)):
        with open(os.path.join(CARDS, f)) as fh:
            cards[f[:-5]] = json.load(fh)
    assert len(cards) >= 8
    for cid, card in cards.items():
        assert card_schema.validate(card, cards) == [], cid
        assert card["owner_flags"]["adds_zoom"] is False
        assert card["owner_flags"]["adds_sound"] is False


# ── rhetoric ─────────────────────────────────────────────────────────────

def _w(text, step=0.3):
    out, t = [], 0.0
    for tok in text.split():
        out.append({"w": tok, "t0": round(t, 2), "t1": round(t + 0.25, 2)})
        t += step
    return out


def test_lexical_tagger_marks_the_rhetoric():
    words = _w("We did not quit. But the price fell 90 percent, which is "
               "insane. That is the damn truth.")
    tags = device_planner.lexical_tags(words)
    by = {words[k]["w"].strip(".,"): v for k, v in tags.items()}
    assert "negation" in by["not"]
    assert "contrast" in by["But"]
    assert "number" in by["90"] and "number" in by["percent"]
    assert "emotion" in by["insane"]
    assert "profanity" in by["damn"]
    # the sentence carrying a number puts its payload on the figure
    assert any("payload" in v for k, v in tags.items()
               if words[k]["w"].startswith(("90", "percent")))


def test_llm_tags_parse_validate_and_cache_per_transcript():
    words = _w("They promised us flying cars and all we got was 140 "
               "characters.")
    calls, store = [], {}

    def ask(system, user):
        calls.append(user)
        assert "payload" in system and "0:They" in user
        return 'sure: {"payload": [10, 11], "number": [10], "bogus": [1], ' \
               '"negation": [99]}'
    device_planner._RHETORIC_CACHE.clear()
    tags = device_planner.llm_tags(words, ask, cache_get=store.get,
                                   cache_put=store.__setitem__)
    assert tags == {10: {"payload", "number"}, 11: {"payload"}}
    assert device_planner.llm_tags(words, ask) == tags   # in process
    assert len(calls) == 1
    device_planner._RHETORIC_CACHE.clear()
    assert device_planner.llm_tags(words, ask, cache_get=store.get) == tags
    assert len(calls) == 1                               # durable store
    assert device_planner.parse_llm_tags("no json", 5) == {}
    assert device_planner.llm_tags(words, lambda s, u: None) in ({}, tags)


# ── the program and its moments ──────────────────────────────────────────

def _program():
    words, t = [], 0.4
    for tok in ("We tried everything for years. Nothing worked. | But then "
                "the numbers changed. We did not quit. Today it costs 140 "
                "dollars.").split():
        if tok == "|":
            t = 20.4
            continue
        words.append({"w": tok, "t0": round(t, 2), "t1": round(t + 0.3, 2)})
        t += 0.36 + (0.4 if tok.endswith(".") else 0.0)
    index = {"video": {"duration": 40.0, "fps": 30.0, "width": 1920,
                       "height": 1080}, "words": words,
             "shots": [{"start": 0.0, "end": 14.5},
                       {"start": 20.0, "end": 40.0}]}
    edl = {"keep": [[0.0, 14.5], [20.0, 30.0]], "inserts": [], "speed": []}
    return edl, index


def test_moments_bind_words_cuts_and_find_the_late_payoff():
    edl, index = _program()
    plan = device_planner.moments(edl, index)
    assert plan["program_s"] == pytest.approx(24.5)
    cuts = [m for m in plan["moments"] if m["kind"] == "cut"]
    assert [round(m["t"], 2) for m in cuts] == [14.5]
    assert cuts[0]["bind"] == {"on": "cut", "ref": 20.0}
    p = plan["payoff"]
    assert p["word"] == "140" and p["t"] > plan["program_s"] * 0.6
    assert p["hold_end"] - p["t"] >= device_planner.PAYOFF_HOLD_S[0]
    payoff = [m for m in plan["moments"] if m["kind"] == "word.payoff"][0]
    assert payoff["bind"]["on"] in ("word", "stress")
    assert index["words"][payoff["bind"]["ref"]]["w"] == "140"
    # every moment sits inside the program, on a shot and a section
    for m in plan["moments"]:
        assert 0.0 <= m["t"] < plan["program_s"]
        assert 0 <= m["shot"] < len(plan["shots"])
        assert 0 <= m["section"] < len(plan["sections"])


def test_format_rules_keep_talking_head_punctuation_to_cuts_and_turns():
    stress = {"kind": "stress", "tags": []}
    turn = {"kind": "word", "tags": ["negation"]}
    cut = {"kind": "cut", "tags": []}
    beat = {"kind": "onset.kick", "tags": []}
    assert not device_planner.format_allows("C", "punctuation", stress)
    assert device_planner.format_allows("C", "punctuation", turn)
    assert device_planner.format_allows("C", "punctuation", cut)
    assert device_planner.format_allows("B", "punctuation", beat)
    assert not device_planner.format_allows("C", "transition", stress)
    assert device_planner.format_allows("C", "matte_fx", stress)


def test_frame_times_name_exact_frames():
    for fps in (25.0, 29.97, 24.0, 59.94):
        for f in (0, 1, 119, 120, 1799):
            t = device_planner.time_of(f, fps)
            assert device_planner.frame_of(t, fps) == f, (fps, f)


def test_card_values_split_choices_from_placement():
    with open(os.path.join(CARDS, "subject_fill_flash.json")) as fh:
        card = json.load(fh)
    vals, place = device_planner.card_values(card)
    assert vals == {"kind": "fill", "colour": "white", "decay_f": 2}
    assert place == {"frames": 2}


# ── the preference memory (project-level, never the EDL) ────────────────

def test_intensity_lives_in_app_kv_per_project():
    kv = {}
    got = preference_memory.read_device_prefs(kv.get, 12)
    assert got["intensity"] == "designed"
    preference_memory.write_device_prefs(kv.__setitem__, 12, "calm",
                                         now="t0")
    assert json.loads(kv["device_prefs:12"]) == {"intensity": "calm",
                                                  "set_at": "t0"}
    assert preference_memory.read_device_prefs(kv.get, 12)["intensity"] \
        == "calm"
    assert preference_memory.read_device_prefs(kv.get, 13)["intensity"] \
        == "designed"
    with pytest.raises(ValueError):
        preference_memory.write_device_prefs(kv.__setitem__, 12, "loud")
    kv["device_prefs:14"] = "{broken"
    assert preference_memory.read_device_prefs(kv.get, 14)["intensity"] \
        == "designed"


def test_repeated_removals_in_a_family_lower_its_rank():
    def v(*ids):
        return {"fx": [{"id": i, "device": "accent_negative"
                        if i.startswith("n") else "subject_fill_flash"}
                       for i in ids]}
    history = [v("n1", "n2", "f1"), v("n2", "f1"), v("f1"), v("f1", "n4"),
               v("f1"), v("f1", "n6"), v("f1")]
    fam = {"accent_negative": "punctuation",
           "subject_fill_flash": "matte_fx"}.get
    counts = preference_memory.removal_counts(history, fam)
    assert counts["devices"] == {"accent_negative": 4}
    assert counts["families"] == {"punctuation": 4}
    w = preference_memory.removal_weights(counts)
    assert w == {"punctuation": 0.0, "accent_negative": 0.0}
    w2 = preference_memory.removal_weights(
        {"families": {"matte_fx": 2}, "devices": {"x": 3}})
    assert w2 == {"matte_fx": 0.5, "x": 0.25}
    # removed and later re-added (an undo) is not a removal
    undo = [v("n1"), v("n9"), v("n1")]
    assert preference_memory.removal_counts(undo)["devices"] == \
        {"accent_negative": 1}
    assert "punctuation removed 4x" in preference_memory.removal_note(counts)


# ── everything that writes fx items: the preview-lane child ─────────────

@pytest.fixture(scope="module")
def probe():
    env = dict(os.environ)
    env.update(VALMERA_FX_PREVIEW="1", SKIP_DB_INIT="1",
               DATABASE_URL="postgresql://stub/stub")
    r = subprocess.run([sys.executable,
                        os.path.join(HERE, "w1f_preview_probe.py")],
                       cwd=WORKER, env=env, capture_output=True, text=True,
                       timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]
    report = json.loads(r.stdout.strip().splitlines()[-1])
    for key in ("candidates", "gates", "trim", "verbs"):
        assert "error" not in report[key], report[key]
    return report


def test_candidates_are_bound_ranked_and_write_in_one_batch(probe):
    c = probe["candidates"]
    assert c["failures"] == []
    assert 1 <= c["n"] <= device_planner.MAX_CANDIDATES
    assert c["ranks"] == list(range(1, c["n"] + 1))
    assert c["undo_restores"] is True
    assert "OPS: [" in c["text"]


def test_the_payoff_takes_the_strongest_device_and_holds(probe):
    c = probe["candidates"]
    assert c["payoff"]["word"] == "140"
    assert c["payoff_device"], "nothing bound to the payoff"
    assert c["in_hold"] == []


def test_restraint_rules_hold_on_the_plan(probe):
    c = probe["candidates"]
    assert c["min_gap"] is None or c["min_gap"] >= \
        device_planner.DEVICE_SPACING_S["designed"] - 1e-6
    # a talking head punctuates only the turn (a negation or contrast) or
    # a cut
    for kind, word in c["punct_moments"]:
        assert kind in ("cut", "word", "word.payoff"), (kind, word)
    assert c["flash_peak"] <= 3
    assert c["junctions"] == [14.5]           # on the real cut, exactly


def test_owner_gates_dark_cards_memory_and_matte(probe):
    g = probe["gates"]
    assert g["zoom_unasked"] == []            # never a zoom unless asked
    assert g["zoom_asked_eligible"] is True
    assert g["dark"] == [] and g["dark_flag_proposable"] is True
    assert g["muted"] == []                   # removal memory mutes
    assert g["no_person_matte"] == []         # no person: no matte device
    assert g["pending_notes"] and all("measuring" in n or "until" in n
                                      for n in g["pending_notes"])
    assert g["window_max_at"] is not None and g["window_max_at"] < 10.0
    assert g["charged_flash_peak"] <= 3
    assert g["calm_n"] <= g["charged_n"]


def test_less_flashy_trims_the_lowest_ranked_and_passes_flash_safety(probe):
    t = probe["trim"]
    assert t["flash_before"] > 3
    assert t["flash_after"] <= 3
    assert "neg2" in t["kept"]                # the bound, strongest one
    assert set(t["calm_removed"]) | set(t["kept"]) == {
        f"neg{k}" for k in range(6)}
    assert "flash_rate_exceeded" in t["audit"]


def test_verbs_on_a_tool_context(probe):
    v = probe["verbs"]
    assert "device candidate(s)" in v["suggest"] and "OPS: [" in v["suggest"]
    assert v["write"].startswith("EDL v1 -> v2")
    assert "MOTION AUDIT" in v["audit"] and "Payoff:" in v["audit"]
    assert v["set_calm"].startswith("EDL v2 -> v3: set_intensity calm")
    assert "Diff: removed" in v["set_calm"]
    assert json.loads(v["prefs"])["intensity"] == "calm"
    assert v["bad_level"].startswith("REJECTED")
    assert "unchanged" in v["nothing_to_trim"]
    assert v["proof_unknown"].startswith("REJECTED")


def test_planner_without_cards_proposes_nothing_and_says_so():
    edl, index = _program()
    assert device_planner.suggest(edl, index, cards={}) == []
    text = device_planner.format_candidates([], empty_reason="no card")
    assert "zero devices is allowed" in text


def test_every_live_card_carries_an_owned_prefix():
    """Wave 1 cards ship with tracks 1A-1E (this branch carries none, so
    the planner proposes nothing in production until they merge)."""
    assert all(c.split("_", 1)[0] in devices.CARD_PREFIXES
               for c in devices.load_cards())
