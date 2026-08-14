"""Mid-session uploads stay in the tray after the seed EDL."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas import default_edl, edl_accepts_tray_autoplace  # noqa: E402


def test_no_edl_yet_is_the_initial_dump():
    assert edl_accepts_tray_autoplace(None) is True
    assert edl_accepts_tray_autoplace(0) is True


def test_seed_v1_without_authored_layers_autoplaces():
    assert edl_accepts_tray_autoplace(1, default_edl(17.7)) is True


def test_v1_with_inserts_or_later_versions_do_not_autoplace():
    seeded = default_edl(17.7)
    seeded["inserts"] = [{"id": "ins1", "asset_key": "x",
                          "at_output_s": 0, "duration_s": 3}]
    assert edl_accepts_tray_autoplace(1, seeded) is False
    assert edl_accepts_tray_autoplace(2, default_edl(17.7)) is False
    assert edl_accepts_tray_autoplace(39, default_edl(17.7)) is False
