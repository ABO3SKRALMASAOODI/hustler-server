"""Marketing (wf2) never places or mixes a flagged library recording (owner, 15 Nov 2026; worker/sfx_filter.py): its
cut and send clicks used click_1 (an audible encoder wall at or under 16.5 kHz); they now play click_2."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import film as F  # noqa: E402
import supply  # noqa: E402


def test_the_sound_table_and_a_placed_click_hold_no_flagged_recording():
    SF = F.sfx_filter()
    lib = supply.sound_facts()
    assert lib["click_1"]["id"] == "click_2" and lib["click_1"]["file"] == "click_2.flac"
    assert not {m["id"] for m in lib.values()} & SF.flagged()
    s = F.sound("cut0", "click_1", lib, 120, -18.0)
    assert s["s"] == "click_2" and s["asked"] == "click_1" and s["file"] == "click_2.flac"


def test_the_mixer_swaps_a_flagged_file(tmp_path, monkeypatch):
    """A plan that still names click_1 (written before the filter): the mixer reads click_2."""
    calls = []
    real = F.sfx_filter()

    class Spy:
        def swap(self, s, lib_dir=None):
            out = real.swap(s, lib_dir)
            calls.append(out[0]["file"])
            return out
    monkeypatch.setattr(F, "sfx_filter", lambda worker=None: Spy())
    l1 = {"dur_f": 90, "spine": [], "assets": {},
          "sound": [dict(id="s1", s="click_1", file="click_1.flac", event_f=30, start_s=0.83, trim_s=0.0, db=-6.0, peak_s=0.168)]}
    try:
        F.audio_master(os.environ.get("FFMPEG_BIN", "ffmpeg"), l1, str(tmp_path / "a.m4a"), str(tmp_path))
    except Exception:  # noqa: BLE001 - the loudness pass is not under test here
        pass
    assert calls == ["click_2.flac"], calls
