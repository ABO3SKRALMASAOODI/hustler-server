"""Small, stable error excerpts for job rows and transport envelopes."""


def excerpt(error, limit=2000):
    """Keep both the exception identity and the actionable diagnostic tail.

    ffmpeg writes its banner first and the actual filter/codec failure last.
    Prefix-only truncation therefore preserved ~2 KB of version information
    while deleting the one line needed to diagnose the render.
    """
    text = str(error or "")
    limit = max(32, int(limit))
    if len(text) <= limit:
        return text
    marker = "\n...[diagnostic middle omitted]...\n"
    room = max(2, limit - len(marker))
    head = max(1, room // 3)
    tail = max(1, room - head)
    return text[:head] + marker + text[-tail:]
