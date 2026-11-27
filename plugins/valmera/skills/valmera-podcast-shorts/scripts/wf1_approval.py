"""Keep one hypothetical job visible across a conditional approval and its wait."""


def example(raw, word_count):
    if not isinstance(raw, dict) or not isinstance(raw.get("job"), str) or not raw["job"].strip():
        return None
    first, last = raw.get("first"), raw.get("last")
    if not (type(first) is int and type(last) is int and 0 <= first < last < word_count):
        return None

    def cues(key):
        by_word = {}
        rows = raw.get(key)
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, dict):
                continue
            at, label = row.get("at"), row.get("label")
            if type(at) is int and first <= at <= last and isinstance(label, str) and label.strip():
                by_word[at] = label.strip()
        return [dict(at=at, label=by_word[at]) for at in sorted(by_word)]

    stations, states = cues("stations"), cues("states")
    if len(stations) < 2:
        return None
    return dict(first=first, last=last, job=raw["job"].strip(), stations=stations, states=states)
