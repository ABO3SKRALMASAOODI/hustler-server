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


# ── round 68: the dedup quickhash is a two-party contract ────────────────────

def test_dedup_quickhash_matches_the_client_construction():
    """The server verifies a candidate by hashing the same bytes the browser
    hashed: first chunk, plus the last chunk when the file is bigger than one,
    concatenated. If either side changes its recipe the other stops
    recognizing every re-upload — silently, as {dedup:false} — so the exact
    construction is pinned here against a fake object store, including the
    small-file (single-slice) and the between-one-and-two-chunks shapes the
    slicing arithmetic can get wrong."""
    import hashlib

    from routes import video

    CH = video._DEDUP_CHUNK

    def fake_blob(n):
        # deterministic, position-dependent bytes so a wrong offset changes
        # the hash
        return bytes((i * 31 + 7) % 256 for i in range(n))

    for size in (100, CH - 1, CH, CH + 1, CH + CH // 2, 2 * CH, 3 * CH + 5):
        blob = fake_blob(size)

        def fake_range(key, offset, length, _blob=blob):
            return _blob[int(offset):int(offset) + int(length)]

        orig = video.storage.get_range_at
        video.storage.get_range_at = fake_range
        try:
            server = video._dedup_quickhash_of_key("k", size)
        finally:
            video.storage.get_range_at = orig

        # the client's construction (src/lib/upload.js quickhashFile)
        first = blob[0:min(size, CH)]
        last = b""
        if size > CH:
            off = max(CH, size - CH)
            last = blob[off:size]
        client = hashlib.sha256(first + last).hexdigest()
        assert server == client, f"quickhash drift at size {size}"


# ── round 70: the transfer's geometry is why one user waited 15 minutes ─────
#
# A 45 MB file went up as ONE presigned PUT — one TCP stream — and the user's
# link delivered 48 KB/s, so they stared at a bar for 15.5 minutes and left
# without ever sending a message. Meanwhile 4 GB multipart uploads on the same
# day ran at 5-9 MB/s over six sockets. Parallelism has to arrive where the
# files actually are (tens of MB), not only past a third of a gigabyte.

def test_single_put_range_is_small():
    """Above 16 MB a file gets parts and sockets. 16 MB is where every
    S3-compatible transfer manager draws the line, and it is small enough
    that the 45 MB upload this round is about would have had six streams."""
    assert storage.SINGLE_PUT_LIMIT == 16 * 1024 * 1024


def test_part_size_gives_real_parallelism_to_mid_sized_files():
    """The old fixed 64 MB part meant a 100 MB 'multipart' upload was two
    sockets in practice. Six-way parallelism (the client's pool width) must
    light up right at the multipart threshold."""
    for nbytes in (17 * 1024 * 1024, 45 * 1024 * 1024, 100 * 1024 * 1024,
                   500 * 1024 * 1024):
        ps = storage.part_size_for(nbytes)
        n_parts = (nbytes + ps - 1) // ps
        assert n_parts >= min(6, (nbytes + storage.MIN_PART_SIZE - 1)
                              // storage.MIN_PART_SIZE), (
            f"{nbytes} bytes -> {n_parts} parts of {ps}: the pool starves")


def test_part_size_respects_the_protocol_and_the_url_budget():
    """Every part but the last must be >= 5 MB (R2/S3 refuse smaller), equal
    sized (R2 requires it — part_size_for returns ONE size per file), and a
    14 GB file must not mint thousands of presigned URLs."""
    for nbytes in (storage.SINGLE_PUT_LIMIT + 1, 64 * 1024 * 1024,
                   1024 ** 3, 4 * 1024 ** 3, 14 * 1024 ** 3):
        ps = storage.part_size_for(nbytes)
        assert ps >= 5 * 1024 * 1024
        assert ps % (1024 * 1024) == 0, "whole MiB keeps ranges page-aligned"
        n_parts = (nbytes + ps - 1) // ps
        assert n_parts <= 10_000, "S3's hard cap on parts"
        assert n_parts <= 300, f"{n_parts} URLs for {nbytes} bytes is a bloated presign response"
        assert n_parts * ps >= nbytes, "parts must cover the file"


def test_upload_presigns_outlive_a_slow_upload():
    """A presign is validated when a request STARTS. At the old 15 minutes,
    any part first attempted after minute 15 — or any retry of a slow single
    PUT — got a non-retryable 403 and killed the whole transfer. The window
    must cover the biggest allowed file on a modest 4 Mbps uplink."""
    slowest_plausible_s = storage.max_upload_bytes() * 8 / 4e6
    assert storage.PRESIGN_UPLOAD_EXPIRY >= slowest_plausible_s, (
        f"{storage.PRESIGN_UPLOAD_EXPIRY}s cannot cover a "
        f"{storage.MAX_UPLOAD_GB} GB upload at 4 Mbps "
        f"({slowest_plausible_s:.0f}s)")


def test_the_slow_rescue_and_transfer_beacons_are_accepted():
    """The studio reports the mid-upload switch to a proxy and the finished
    transfer's measured speed; a kind missing from the allowlist is silently
    dropped and the next investigation is blind again."""
    from routes import video
    assert "upload_slow_rescue" in video.CLIENT_EVENT_KINDS
    assert "upload_transfer" in video.CLIENT_EVENT_KINDS
