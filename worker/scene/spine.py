"""Source audio keeps its sample clock; pictures use whole programme frames."""


def start_s(span, fps):
    return float(span.get("at_s", span["f"][0] / fps))


def source_at(span, frame, fps):
    t = span["src"][0] + frame / fps - start_s(span, fps)
    return min(span["src"][1], max(span["src"][0], t))


def samples(span, fps, rate, length):
    at = round(start_s(span, fps) * rate)
    source = round(span["src"][0] * rate)
    if "at_s" in span:
        count = round(span["src"][1] * rate) - source
    else:
        count = round(span["f"][1] / fps * rate) - at
    return at, source, max(0, min(count, length - at))
