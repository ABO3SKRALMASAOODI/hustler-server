"""Direct an acted comparison on the interview clock, with one recurring example."""
import math


def region(box):
    return (isinstance(box, list) and len(box) == 4 and
            all(type(v) in (int, float) and math.isfinite(v) for v in box) and
            0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)


def reading(raw, count):
    if not isinstance(raw, dict):
        return None
    keys = ("first", "pivot", "switch", "item_at", "gesture", "after", "return_at", "last")
    ids = [raw.get(k) for k in keys]
    if not all(type(i) is int and 0 <= i < count for i in ids) or ids != sorted(ids):
        return None
    labels = ("before", "alternative", "item", "state", "outcome")
    if not all(isinstance(raw.get(k), str) and raw[k].strip() for k in labels):
        return None
    row = {k: raw[k] for k in (*keys, *labels)}
    for key in ("subject", "action"):
        row[key] = raw[key] if region(raw.get(key)) else [0, 0, 1, 1]
    example = raw.get("example_at")
    if type(example) is int and row["item_at"] < example < row["gesture"]:
        row["example_at"] = example
    detail = raw.get("detail")
    if (isinstance(detail, dict) and type(detail.get("first")) is int and type(detail.get("last")) is int
            and row["gesture"] <= detail["first"] <= detail["last"] < row["after"]
            and region(detail.get("left")) and region(detail.get("right"))):
        row["detail"] = detail
    return row


def spans(ex):
    return [(ex["first"],ex.get("example_at",ex["gesture"])-1),
            (ex["gesture"],ex["after"]-1), (ex["return_at"],ex["last"])]


def restarts(rows, count):
    return [r for r in rows if isinstance(r, dict) and all(type(r.get(k)) is int for k in ("fragment","question","end"))
            and 0 <= r["fragment"] < r["question"] == r["fragment"]+1 <= r["end"] < count
            ] if isinstance(rows, list) else []


def picture(plan, span, key, src, crop, take, canvas, seat, align_y=.5, **attrs):
    from scene.motion import fit_panel
    box = fit_panel(crop, (take["w"],take["h"]), canvas, seat, align_y=align_y)
    return plan.card(span,key,box,src=src,crop=crop,source_frame=True,source_ink=[box],
                     caption_baseline=.89, **attrs)


def live_picture(plan, ex, frame, span, src, key, take, canvas, faces, returning):
    from scene.motion import gesture_region
    at = span[0]
    live_action = frame(ex["gesture"]) <= at < frame(ex["after"])
    crop = gesture_region([ex["action" if live_action else "subject"]], faces)
    seat = [.09,.09,.91,.56] if frame(ex["item_at"]) <= at < frame(ex["gesture"]) else [0,.075,1,.59]
    align_y = .5
    if returning and faces:
        x,y,r,b = gesture_region(faces, [])
        crop = [max(0,x-.08),max(0,y-.14),min(1,r+.08),min(1,b+.18)]
    detail = ex.get("detail")
    paired = live_action and detail and frame(detail["first"]) <= at < frame(detail["last"]+1)
    if live_action:
        seat, align_y = [0,.07,1,.54], 1.
    if paired:
        # The outer setup needs more width than the inward motion. Frame the
        # latter with the actual two hands, hair and glasses still in one shot.
        crop = gesture_region([detail["left"],detail["right"]], faces)
        crop[1] = min(crop[1], ex["action"][1])
    plan.page(span,"#121619",grain=0)
    picture(plan,span,key,src,crop,take,canvas,seat,align_y=align_y,fit_motion=True)
    if paired:
        for side,box in (("left",[0,.544,.498,.827]),("right",[.502,.544,1,.827])):
            picture(plan,span,key,src,detail[side],take,canvas,box,align_y=0.,gesture_detail=side)
