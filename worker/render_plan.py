"""Pure dependency planning shared by ordinary and canvas rendering.

Document versions are not media dependencies. These helpers describe the
source window and the picture that an artifact actually needs.
"""
import copy
import json


def canonical_program(edl):
    """Remove organizational cuts before compiling media dependencies.

    Only explicitly marked subdivisions are joined. Historical authored
    cuts and their transitions retain their existing meaning. A source gap,
    an intervening insert or a changed clip property prevents joining.
    """
    result = copy.deepcopy(edl)
    # Schema validation rounds source spans to centiseconds. Receipts may
    # retain the millisecond playhead (e.g. 8.318 -> 8.32); compare on the
    # same clock so that persistence cannot turn a split into a real cut.
    boundaries = {round(float(t), 2) for t in
                  (result.pop("split_keep_boundaries", None) or [])}
    keep, pre = [], 0.0
    # Imported here to keep source-window planning independent of schemas.
    try:
        from schemas import speed_pieces
    except ImportError:
        from worker_schemas import speed_pieces
    insert_at = [float(i["at_output_s"]) for i in result.get("inserts") or []]
    for start, end in result.get("keep") or []:
        occupied = any(abs(at - pre) < 1e-6 for at in insert_at)
        if (keep and round(float(start), 2) in boundaries and not occupied
                and abs(keep[-1][1] - start) < 1e-6):
            keep[-1][1] = end
        else:
            keep.append([start, end])
        pre += sum((b - a) / rate for a, b, rate in
                   speed_pieces(start, end, result.get("speed") or []))
    result["keep"] = keep
    inserts = []
    canvas = not keep and bool(result.get("canvas"))
    for item in result.get("inserts") or []:
        previous = inserts[-1] if inserts else None
        ignore = {"id", "duration_s", "source_start_s"}
        if canvas:
            ignore.add("at_output_s")
        if (previous and item.get("split_parent")
                and previous.get("split_parent") == item["split_parent"]
                and all(previous.get(k) == item.get(k)
                        for k in (set(previous) | set(item)) - ignore)
                and round(float(item.get("source_start_s") or 0), 2)
                    == round(float(previous.get("source_start_s") or 0)
                             + previous["duration_s"]
                             * (previous.get("rate") or 1), 2)):
            previous["duration_s"] = round(previous["duration_s"]
                                           + item["duration_s"], 3)
        else:
            inserts.append(item)
    cursor = 0.0
    for item in inserts:
        item.pop("split_parent", None)
        if canvas:
            item["at_output_s"] = round(cursor, 3)
            cursor += item["duration_s"]
    if "inserts" in result:
        result["inserts"] = inserts
    return result


def insert_input(item, path, fps, *, copy_timestamps=False):
    """Input options and graph item for a bounded, accurate source read.

    Keep one second of preroll for decoder/filter state. With -copyts (used
    by long main-source selections) FFmpeg retains the original clock;
    otherwise the graph must trim on the rebased input clock. Never mutate
    the saved document or its source-time evidence.
    """
    graph_item = dict(item)
    duration = float(item["duration_s"])
    if item["kind"] == "image":
        return (["-loop", "1", "-t", f"{duration:.3f}",
                 "-r", f"{fps:.3f}", "-i", path], graph_item)
    start = max(0.0, float(item.get("source_start_s") or 0))
    rate = float(item.get("rate") or 1)
    seek = max(0.0, start - 1.0)
    # A little tail allows frame-rate normalization to consume its next
    # frame. trim/atrim still enforce the exact authored interval.
    read_duration = start - seek + duration * rate + 1.0
    options = ["-ss", f"{seek:.3f}"] if seek else []
    if seek and not copy_timestamps:
        graph_item["source_start_s"] = round(start - seek, 3)
    return (options + ["-t", f"{read_duration:.3f}", "-i", path],
            graph_item)


def picture_signature(edl):
    """Conservative identity of the picture, excluding only audio controls.

    Keep durations, caption dependencies and unknown future fields in the
    key. Audio tail length is checked separately by the artifact planner.
    """
    visual = canonical_program(edl)
    for key in ("music", "sfx", "volume", "voiceover", "master", "stem_mix"):
        visual.pop(key, None)
    for item in visual.get("inserts") or []:
        item.pop("mute", None)
    return json.dumps(visual, sort_keys=True, separators=(",", ":"))


def can_reuse_picture(previous, current):
    return picture_signature(previous) == picture_signature(current)
