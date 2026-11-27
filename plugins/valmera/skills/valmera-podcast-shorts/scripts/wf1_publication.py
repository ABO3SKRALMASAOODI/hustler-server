"""Word-timed readings of a published hypothesis and the speaker's inference."""


def argument_of(row, count):
    if not isinstance(row, dict):
        return None
    indices = [row.get(k) for k in ("reaction","first","observation","gap","close","last")]
    if not all(type(i) is int and 0 <= i < count for i in indices):
        return None
    if indices != sorted(indices) or not all(isinstance(row.get(k),str) and row[k].strip()
            for k in ("speaker","author","premise","present","question")):
        return None
    return row


def retained_covers(coverage, sources):
    refs = []
    for i, window in enumerate(coverage["windows"]):
        if window.get("medium") != "publisher_cover":
            continue
        match = next((src for src in sources if src.get("figure_title") == window["name"] and
                      all(src.get(k) for k in ("figure_url","url","author","publisher","publication_date"))), None)
        if match:
            refs.append(dict(windows=[i],referent=window["name"],sources=[match]))
    return refs
