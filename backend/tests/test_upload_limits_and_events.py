"""The upload caps, and the forensics rows that record who they turned away.

Two things are pinned here.

FIRST, the caps have to be mutually consistent. Shipping a 2 GiB byte cap
beside a 3-hour duration cap advertised a product that could not exist: 2 GiB
over 3 hours is 1.6 Mbps, and even ONE hour only fit under 4.7 Mbps, which is
below what any phone or camera produces. Both numbers were on the marketing
pages at once, and the one users actually hit was the one nobody had checked
against the other.

SECOND, the event detail is user-controlled input that lands in a table an
admin reads during an incident, so its sanitiser is a security boundary, not a
formatting nicety.

    cd backend && python -m pytest tests -q
"""

import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage                                          # noqa: E402


# ── the caps agree with each other ──────────────────────────────────────────

def test_the_byte_cap_can_hold_the_advertised_duration_at_a_real_bitrate():
    """The cap that broke the product.

    A byte cap and a duration cap shipped side by side imply a THIRD number
    nobody set: the bitrate at which both can be true. At 2 GiB / 3 h that
    number was 1.6 Mbps — below anything a real camera records — so the
    3-hour promise was unreachable and the byte cap was what users actually hit.

    The bar is the bitrate an ordinary long recording is made at, not the
    bitrate a short one is: nobody uploads 3 hours of 50 Mbps 4K through a
    browser (that alone is a 7-hour upload). 8-10 Mbps is a normal 1080p
    screen capture, Zoom recording or podcast camera, which is what actually
    arrives at three hours.
    """
    implied_mbps = (storage.max_upload_bytes() * 8) / storage.MAX_DURATION_S / 1e6
    assert implied_mbps >= 10, (
        f"a {storage.MAX_UPLOAD_GB} GB cap over "
        f"{storage.MAX_DURATION_S / 3600:.0f}h implies {implied_mbps:.1f} Mbps — "
        "below ordinary 1080p, so the duration limit is unreachable and one of "
        "the two numbers is a lie")


def test_an_hour_of_4k_fits():
    """~30 Mbps is a phone or mirrorless camera shooting 4K30. This is the case
    that has to work for 'upload an hour of what I shot' to be true.

    The ceiling above it is not a product choice: the executor's workdir is
    sized by its Cloud Run --memory (32 GiB) and a job needs WORKDIR_HEADROOM
    (2.2x) for what it writes beside the source, so 14.5 GB is the largest
    source that instance can stage — measured, via /health's max_source_gb.
    An hour of 4K60 at 50 Mbps (22 GB) genuinely does not fit and is refused
    honestly rather than accepted and then killed.
    """
    assert storage.max_upload_bytes() >= 30e6 / 8 * 3600


def test_the_cap_stays_within_what_the_executor_can_stage():
    """The cap and the machine are a PAIR, exactly like the executor timeouts.

    32 GiB of workdir / 2.2x headroom = 14.5 GB. A cap above that accepts an
    upload the render service cannot process — the refusal just moves to after
    the user has spent 40 minutes sending it, which is the worst place for it.
    """
    executor_workdir_gb = 32.0          # Cloud Run --memory on valmera-executor
    headroom = 2.2                      # worker/config.WORKDIR_HEADROOM
    assert storage.MAX_UPLOAD_GB <= executor_workdir_gb / headroom


def test_an_hour_of_ordinary_1080p_fits():
    """~20 Mbps is a normal phone/camera 1080p60 bitrate."""
    assert storage.max_upload_bytes() >= 20e6 / 8 * 3600


def test_ten_minutes_of_4k_fits():
    """~100 Mbps covers 4K from a consumer camera or a high-bitrate screen
    recording — the exact case that was being refused."""
    assert storage.max_upload_bytes() >= 100e6 / 8 * 600


def test_limits_are_reported_for_the_client_to_read():
    """The studio must never carry its own copy of these numbers: it did, and
    that literal is why raising the server's cap would have changed nothing a
    user could see."""
    lim = storage.upload_limits()
    for key in ("max_bytes", "max_bytes_label", "max_duration_s",
                "clip_max_bytes", "music_max_bytes", "image_max_bytes",
                "video_ext"):
        assert key in lim, f"/video/limits must report {key}"
    assert lim["max_bytes"] == storage.max_upload_bytes()
    assert lim["max_duration_s"] == storage.MAX_DURATION_S
    assert ".mp4" in lim["video_ext"]


def test_attachment_caps_stay_below_the_main_video_cap():
    lim = storage.upload_limits()
    assert lim["image_max_bytes"] < lim["clip_max_bytes"] < lim["max_bytes"]


def test_validate_upload_labels_the_cap_it_enforced():
    """The refusal has to name the number it applied, or the user is guessing."""
    try:
        storage.validate_upload("huge.mp4", storage.max_upload_bytes() + 1,
                                "original")
        assert False, "an over-cap file must be refused"
    except ValueError as e:
        assert "GB" in str(e)


def test_validate_upload_still_refuses_the_things_it_always_did():
    for filename, nbytes, kind in [("x.exe", 10, "original"),
                                   ("x.mp4", 0, "original"),
                                   ("x.mp4", None, "original")]:
        try:
            storage.validate_upload(filename, nbytes, kind)
            assert False, f"{filename}/{nbytes} should be refused"
        except ValueError:
            pass


# ── the forensics rows ──────────────────────────────────────────────────────

def _clean(detail):
    from routes import video
    return video._clean_event_detail(detail)


def test_detail_scalars_survive():
    out = _clean({"reason": "over size cap", "bytes": 5, "offline": True,
                  "status": None})
    assert out["reason"] == "over size cap"
    assert out["bytes"] == 5
    assert out["offline"] is True
    assert out["status"] is None


def test_detail_is_bounded_in_every_direction():
    """A beacon is called from a page the user controls; this row is read by an
    admin during an incident and must not be a place to write a novel."""
    out = _clean({f"k{i}": "v" for i in range(200)})
    assert len(out) <= 20
    assert all(len(k) <= 40 for k in out)

    out = _clean({"reason": "x" * 5000})
    assert len(out["reason"]) <= 300

    # JSON has no integer bound. A bare 4000-digit number passed isinstance(int)
    # and landed at full length, sailing past the cap that exists to stop it.
    out = _clean({"n": 10 ** 400})
    assert len(str(out["n"])) <= 300

    assert _clean(None) == {}
    assert _clean("not a dict") == {}


def test_nested_structures_are_flattened_to_strings():
    out = _clean({"nested": {"a": [1, 2, 3]}})
    assert isinstance(out["nested"], str)


def test_upload_kinds_are_accepted_and_unknown_kinds_are_not():
    from routes import video
    for kind in ("upload_started", "upload_rejected", "upload_failed"):
        assert kind in video.CLIENT_EVENT_KINDS
    # The player family must survive alongside them.
    assert "player_error" in video.CLIENT_EVENT_KINDS
    assert "definitely_not_a_kind" not in video.CLIENT_EVENT_KINDS


def test_only_the_two_failure_kinds_count_as_failures():
    """`upload_started` is deliberately NOT a failure — it is the denominator
    that makes 'died in transit' countable at all."""
    from routes import video
    assert set(video.UPLOAD_FAILURE_KINDS) == {"upload_rejected",
                                               "upload_failed"}
