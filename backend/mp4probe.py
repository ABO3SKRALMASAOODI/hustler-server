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
