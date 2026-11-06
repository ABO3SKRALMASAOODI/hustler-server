"""Read an MP4/MOV's duration from its header, over HTTP range requests.

WHY THIS EXISTS. A proxy-first upload builds the editable copy in the BROWSER
and registers the original's duration from what the browser measured. Every
timestamp in the edit is then written against that number. If it were wrong,
the mistake would not surface until export — the one moment the user is least
able to absorb it — because that is the first time anything reads the real
file.

So when the background upload lands, the original is checked against the claim.
Not by downloading it (that is the multi-GB transfer this whole path exists to
get out of the user's way) and not with ffmpeg (the API server has none):
an MP4's duration lives in the `mvhd` box inside `moov`, which is a few hundred
bytes reachable in a handful of ranged reads.

Everything here FAILS OPEN by returning None. A container we cannot parse is
not evidence that the browser lied, and refusing an upload over an unreadable
header would break exactly the long-tail formats this check cannot help with.
"""

import struct

# Boxes whose payload is a list of child boxes. `moov` is the only one we need
# to descend into, but naming them makes the walk explicit rather than magic.
_CONTAINERS = {b"moov"}

# A header is size(4) + type(4), with an optional 64-bit largesize.
_HEADER = 8
_MAX_BOXES = 64          # a sane file has a handful at the top level
_MVHD_WINDOW = 512       # mvhd is moov's first child; this covers it comfortably


def _parse_header(buf):
    """(size, type, header_len) from the start of `buf`, or None."""
    if len(buf) < _HEADER:
        return None
    size, btype = struct.unpack(">I4s", buf[:_HEADER])
    hlen = _HEADER
    if size == 1:                      # 64-bit largesize follows the type
        if len(buf) < 16:
            return None
        size = struct.unpack(">Q", buf[8:16])[0]
        hlen = 16
    return size, btype, hlen


def _mvhd_duration(payload):
    """Seconds from an mvhd box payload (everything after its header)."""
    if len(payload) < 4:
        return None
    version = payload[0]
    if version == 1:
        if len(payload) < 4 + 8 + 8 + 4 + 8:
            return None
        timescale = struct.unpack(">I", payload[20:24])[0]
        duration = struct.unpack(">Q", payload[24:32])[0]
    else:
        if len(payload) < 4 + 4 + 4 + 4 + 4:
            return None
        timescale = struct.unpack(">I", payload[12:16])[0]
        duration = struct.unpack(">I", payload[16:20])[0]
    if not timescale:
        return None
    # 0xFFFFFFFF is the "unknown duration" sentinel a fragmented or still-being-
    # written file uses. It is not a 49710-hour video.
    if duration in (0, 0xFFFFFFFF):
        return None
    return duration / float(timescale)


def duration_seconds(fetch, total_bytes):
    """Duration in seconds, or None if it cannot be read.

    `fetch(offset, length) -> bytes | None` does the ranged reads. Walks the
    top-level boxes by HEADER ONLY, so a 14 GB file whose moov sits after a
    14 GB mdat costs a few dozen bytes to skip past — the alternative, reading
    forward until moov appears, would download the whole thing.
    """
    if not total_bytes or total_bytes < _HEADER:
        return None
    offset = 0
    for _ in range(_MAX_BOXES):
        if offset >= total_bytes:
            return None
        head = fetch(offset, 16)
        if not head:
            return None
        parsed = _parse_header(head)
        if not parsed:
            return None
        size, btype, hlen = parsed
        if size == 0:                  # "extends to end of file"
            size = total_bytes - offset
        if size < hlen:
            return None                # malformed; do not loop forever

        if btype in _CONTAINERS:
            body = fetch(offset + hlen, min(_MVHD_WINDOW, size - hlen))
            if not body:
                return None
            inner = 0
            while inner + _HEADER <= len(body):
                ch = _parse_header(body[inner:])
                if not ch:
                    return None
                csize, ctype, chlen = ch
                if ctype == b"mvhd":
                    return _mvhd_duration(body[inner + chlen:])
                if csize < chlen:
                    return None
                inner += csize
            return None                # mvhd was not moov's first child
        offset += size
    return None


def duration_of_key(storage, key, total_bytes):
    """duration_seconds against an object in our bucket. None on any failure."""
    def fetch(off, length):
        try:
            return storage.get_range_at(key, off, length)
        except Exception:
            return None
    try:
        return duration_seconds(fetch, total_bytes)
    except Exception:
        return None


# ── Is this file finished? ───────────────────────────────────────────────────
#
# Without `moov` an MP4 cannot open, and re-reading it changes nothing. A file
# picked or sent while it is still being copied says so in its own boxes: asset
# 40278 (account 2123, 5 Nov 2026) announced an mdat of 590,450,793 bytes in a
# file of 15,728,640, and its bytes are the exact start of the finished file.
# It uploaded, failed six analyses across three jobs, and stranded the project.
# The same walk runs in the browser before a byte moves (lib/uploadSource.js)
# and in the worker (media.py), which is what stands behind every other way in.
FINISHED, UNFINISHED, UNKNOWN = "complete", "unfinished", "unknown"
_WALK_BOXES = 512
_FIRST_BOXES = {b"ftyp", b"styp", b"wide", b"free", b"skip", b"mdat", b"moov",
                b"pnot", b"uuid", b"junk", b"pict"}


def _printable(btype):
    return all(0x20 <= c <= 0x7e or c == 0xa9 for c in btype)


def file_state(fetch, total_bytes):
    """{"state", "present", "expected"} for a file of ``total_bytes``.

    Header reads only, whatever the size. UNFINISHED only when the file's own
    boxes prove there is no moov at all; UNKNOWN for everything this cannot
    read or does not recognise, because it may name a file that cannot open
    but must never refuse one that can. ``expected`` is how long the boxes say
    the file is, when they say."""
    out = {"state": UNKNOWN, "present": total_bytes, "expected": None}
    if not total_bytes or total_bytes < _HEADER:
        return out
    offset = 0
    for n in range(_WALK_BOXES):
        if offset >= total_bytes:
            return dict(out, state=UNFINISHED)
        head = fetch(offset, min(16, total_bytes - offset))
        if not head:
            return out
        parsed = _parse_header(head)
        if not parsed:
            return out
        size, btype, hlen = parsed
        if not _printable(btype) or (n == 0 and btype not in _FIRST_BOXES):
            return out
        if size == 0:                  # runs to the end: nothing can follow
            if btype == b"moov":
                return dict(out, state=FINISHED, expected=total_bytes)
            return dict(out, state=UNFINISHED)
        if size < hlen:
            return out
        end = offset + size
        if btype == b"moov":
            # A moov cut short is no proof: ffmpeg reads what is there, and a
            # file missing only the tail of its moov opens and plays. Judged by
            # the worker, which can try it (indexer._refuse_unfinished_source).
            return (dict(out, state=FINISHED, expected=total_bytes)
                    if end <= total_bytes else out)
        if end > total_bytes:
            return dict(out, state=UNFINISHED, expected=end)
        offset = end
    return out


def state_of_key(storage, key, total_bytes):
    """file_state against an object in our bucket. Never raises."""
    def fetch(off, length):
        try:
            return storage.get_range_at(key, off, length)
        except Exception:
            return None
    try:
        return file_state(fetch, total_bytes)
    except Exception:
        return {"state": UNKNOWN, "present": total_bytes, "expected": None}


def _size_words(nbytes):
    if nbytes >= 1024 ** 3:
        return f"{nbytes / 1024 ** 3:.1f} GB"
    return f"{max(1, round(nbytes / 1024 ** 2))} MB"


def unfinished_message(filename, found):
    """The customer's sentence: what is there, and what to do."""
    expected = found.get("expected")
    how = (f"only {_size_words(found['present'])} of about "
           f"{_size_words(expected)} arrived, so it was still being copied or "
           "the copy stopped") if expected else (
        "the recording or the copy never finished")
    return (f"{filename or 'This file'} isn't finished: {how}. Wait for it to "
            "finish, or copy it again from the camera or card, then add it "
            "here. This project is ready for it.")
