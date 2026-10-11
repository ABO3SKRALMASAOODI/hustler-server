"""Device ledger and budget governor (track 0C; AT-0C-5; INTERFACES.md
§6.3) and the re-solve hooks (§6.4).

Every write that adds, removes or changes a device returns one line —
``punctuation 1/2 · matte 1/2 · depth 1/1 · zoom 0 · sfx 0`` — with the
format pack's budgets (ROADMAP §3), and over-budget notes that say what to
thin, never what to add. Zooms and sounds are counted, never demanded."""

import json
import os

import pytest

from w0c_support import six_matte_cards, tool_context, use_cards

import hooks  # noqa: E402
import taste  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "fixtures", "w0_inert_corpus.json")) as _f:
    CORPUS = json.load(_f)
LINE_RE = (r"^punctuation \d+(/\d+)? · matte \d+(/\d+)? · depth \d+(/\d+)?"
           r" · zoom \d+(/\d+)? · sfx \d+(/\d+)?")


@pytest.fixture(autouse=True)
def _no_cards(monkeypatch, tmp_path):
    # The ledger reads device families from cards; pin an empty set so the
    # live directory (Wave 1 cards) never changes these numbers.
    use_cards(monkeypatch, {}, tmp_path / "empty")


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_every_fixture_edl_gets_a_ledger_line(name):
    row = CORPUS[name]
    edl = validate_edl(row["raw"], row["duration"]).model_dump()
    led = taste.ledger(edl)
    assert __import__("re").match(LINE_RE, led.line()), led.line()
    assert isinstance(led.over_budget(), list)
    d = led.as_dict()
    assert d["format"] in taste.FORMAT_PACKS and d["intensity"] == "designed"


def test_the_talking_head_budgets_and_a_note_per_overrun():
    edl = default_edl(30.0)
    edl["effects"] = {"zooms": [{"id": f"z{i}", "start": 2.0 + 5 * i,
                                 "end": 3.0 + 5 * i, "strength": 0.15,
                                 "mode": "punch"} for i in range(3)],
                      "stylize": [{"id": f"fl{i}", "kind": "flash",
                                   "start": 4.0 + 6 * i,
                                   "end": 4.1 + 6 * i, "intensity": 0.8}
                                  for i in range(3)]}
    edl["sfx"] = [{"id": f"s{i}", "storage_key": "sound:whoosh_soft_1",
                   "at": 5.0 + 6 * i} for i in range(3)]
    led = taste.ledger(validate_edl(edl, 30.0).model_dump())
    assert led.fmt == "C"
    assert led.line() == ("punctuation 3/2 · matte 0/2 · depth 0/4 · "
                          "zoom 3 · sfx 3/2")
    notes = led.over_budget()
    assert len(notes) == 2
    assert notes[0].startswith("punctuation 3/2: over the designed budget")
    assert notes[1].startswith("sfx 3/2")
    for note in notes:
        assert "thin" in note and "add" not in note
    # zooms are counted, never budgeted or demanded
    assert not any(n.startswith("zoom") for n in notes)


def test_intensity_and_format_scale_the_budgets():
    edl = validate_edl(default_edl(60.0), 60.0).model_dump()
    calm = taste.ledger(edl, intensity="calm").budgets
    designed = taste.ledger(edl).budgets
    charged = taste.ledger(edl, intensity="charged").budgets
    assert calm["punctuation"] == 1 and designed["punctuation"] == 2 \
        and charged["punctuation"] == 3
    assert designed["depth"] == 8 and calm["depth"] == 4
    assert taste.ledger(edl, fmt="A").budgets["punctuation"] == 30
    assert taste.ledger(edl, fmt="B").budgets["punctuation"] is None
    with pytest.raises(ValueError):
        taste.ledger(edl, intensity="wild")
    with pytest.raises(ValueError):
        taste.ledger(edl, fmt="Z")
    music = default_edl(60.0)
    music["music"] = [{"id": "m", "storage_key": "music/7/a.mp3",
                       "start": 0.0, "end": 60.0}]
    assert taste.infer_format(music) == "B"
    assert taste.infer_format(edl) == "C"


def test_fx_items_count_by_their_device_family(monkeypatch, tmp_path):
    use_cards(monkeypatch, six_matte_cards(), tmp_path / "six")
    edl = {"fx": [{"id": "a", "device": "subject_glow", "type": "subject"},
                  {"id": "b", "device": "text_behind_head", "type": "x"},
                  {"id": "c", "type": "strobe"},
                  {"id": "d", "type": "grade_window"}],
           "keep": [[0, 10]]}
    counts = taste.device_counts(edl)
    assert counts["matte"] == 1 and counts["depth"] == 1
    assert counts["punctuation"] == 1 and counts["finish"] == 1
    assert "finish 1" in taste.ledger(edl).line()


def test_a_device_write_returns_the_ledger_line_and_others_do_not():
    ctx, fake = tool_context(default_edl(20.0))
    edl = default_edl(20.0)
    edl["keep"] = [[0.0, 18.0]]
    out = ctx.write_edl(edl, "trim the tail")
    assert out.startswith("EDL v7 -> v8") and "LEDGER" not in out, out
    edl["effects"] = {"zooms": [{"id": "z1", "start": 4.0, "end": 5.0,
                                 "strength": 0.15, "mode": "punch"}]}
    out = ctx.write_edl(edl, "a punch")
    assert "\nLEDGER (advisory): punctuation 0/2 · matte 0/2 · depth 0/2 · " \
           "zoom 1 · sfx 0" in out, out
    # an optional family's ceiling never reads as free slots to fill
    assert "sfx 0/" not in out


def test_write_ledger_note_is_quiet_without_a_device_change():
    a = default_edl(20.0)
    b = dict(a, keep=[[0.0, 12.0]])
    assert taste.write_ledger_note(a, b) == ""
    c = dict(a, sfx=[{"id": "s1", "storage_key": "sound:click_1", "at": 3.0}])
    assert taste.write_ledger_note(a, c).startswith("LEDGER (advisory)")
    # a broken EDL never fails the write it would decorate
    assert isinstance(taste.write_ledger_note(
        a, {"sfx": 5, "effects": "x", "fx": [None]}), str)


# ── hooks: devices re-solve on a song swap or a keep change ────────────

def test_music_swap_rebinds_devices_and_runs_registered_resolvers():
    edl = {"music": [{"id": "m2"}],
           "fx": [{"id": "a", "stage": "music_bus", "music_id": "m1"},
                  {"id": "b", "stage": "world",
                   "bind": {"on": "beat", "ref": 3, "rhythm": "m1"}}]}
    seen = []

    def resolver(e, old, new):
        seen.append((old, new))
        return e, ["re-landed 1 cut"]

    def broken(e, old, new):
        raise RuntimeError("boom")
    hooks.register("music_swap", resolver)
    hooks.register("music_swap", broken)
    try:
        out, notes = hooks.on_music_swap(edl, "m1", "m2")
    finally:
        hooks.unregister("music_swap", resolver)
        hooks.unregister("music_swap", broken)
    assert out["fx"][0]["music_id"] == "m2"
    assert out["fx"][1]["bind"]["rhythm"] == "m2"
    assert edl["fx"][0]["music_id"] == "m1"        # caller's dict untouched
    assert seen == [("m1", "m2")]
    assert notes[0] == "2 device binding(s) follow the new track"
    assert "re-landed 1 cut" in notes
    assert any("broken skipped" in n for n in notes)
    assert hooks.registered("music_swap") == ()


def test_keep_change_drops_bus_devices_whose_track_is_gone():
    old = {"music": [{"id": "m1"}], "fx": []}
    new = {"music": [], "fx": [{"id": "a", "stage": "music_bus",
                                "music_id": "m1"},
                               {"id": "b", "stage": "world"}]}
    out, notes = hooks.on_keep_change(old, new)
    assert [f["id"] for f in out["fx"]] == ["b"]
    assert notes == ["1 music-bus device(s) removed with their track"]
    with pytest.raises(ValueError):
        hooks.register("render", lambda *a: a)


def test_swap_music_runs_the_registered_re_solvers():
    """The swap tool is the call site: a re-solver a later track registers
    sees the swap and its note reaches the agent; with none registered and
    no device in the edit, nothing runs (inert)."""
    import agent_tools
    from w0c_support import upload_asset
    edl = default_edl(20.0)
    edl["music"] = [{"id": "mus1", "storage_key": "music/7/a.mp3",
                     "start": 0.0, "end": 20.0, "gain_db": -18.0,
                     "duck": True}]
    ctx, fake = tool_context(edl)
    upload_asset(fake, "music/7/a.mp3", filename="a.mp3")
    upload_asset(fake, "music/7/b.mp3", filename="b.mp3")
    seen = []

    def resolver(e, old, new):
        seen.append((old, new, e["music"][0]["storage_key"]))
        return e, ["re-landed 2 cuts on the new beats"]
    hooks.register("music_swap", resolver)
    try:
        out = agent_tools.swap_music(ctx, "mus1", "music/7/b.mp3")
    finally:
        hooks.unregister("music_swap", resolver)
    assert out.startswith("EDL v7 -> v8"), out
    assert seen == [("mus1", "mus1", "music/7/b.mp3")]
    assert "Devices: re-landed 2 cuts on the new beats" in out
    stored = fake.rows[-1]["json"]["music"][0]
    assert stored["provenance"] == "user_upload"      # the new track's own
    assert hooks.registered("music_swap") == ()
    out = agent_tools.swap_music(ctx, "mus1", "music/7/a.mp3")
    assert out.startswith("EDL v8 -> v9") and "Devices:" not in out, out
