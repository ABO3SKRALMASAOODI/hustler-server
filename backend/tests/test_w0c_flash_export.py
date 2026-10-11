"""Flash safety at the export gate (Wave 0, track 0C; owner decision).

backend/routes/video.py loads worker/flash_safety.py by path and flags a
final past the flash-safe limit ``contains_flashing``. It must read the
program the same way the worker's verifier does — including flash
transitions at the junctions they fire on, which need the shared timeline.

    cd backend && python -m pytest tests -q
"""

import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                   # noqa: E402


def _flash_every_cut(segments):
    edl = video.wschemas.default_edl(20.0)
    edl["keep"] = [[float(i), round(i + 0.3, 2)] for i in range(segments)] \
        + [[float(segments), float(segments) + 3.0]]
    edl["effects"] = {"transition": {"style": "flash", "duration_s": 0.1,
                                     "scope": "every_cut"}}
    return video.wschemas.validate_edl(edl, 20.0).model_dump()


def test_the_export_gate_counts_flash_transitions_at_their_junctions():
    assert video.wflash._timeline() is video.wtimeline
    fast = video.wflash.assess(_flash_every_cut(5))
    assert fast["events"] == 5 and fast["peak_per_s"] == 4
    assert fast["over_limit"] is True
    calm = video.wflash.assess(_flash_every_cut(2))
    assert calm["over_limit"] is False


def test_the_limit_is_the_owner_default_without_the_worker_config():
    assert video.wflash.default_max_per_s() == 3
