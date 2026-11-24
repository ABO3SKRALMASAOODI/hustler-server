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


def draw(plan, ex, frame, end, return_frame, palette, width):
    fg, accent = palette["ink"], palette["accent"]
    first, pivot, switch, item = [frame(ex[k]) for k in ("first","pivot","switch","item_at")]
    centre = .625
    # The first idea stays in place as attention crosses to the second.
    for label, x, a, b, active in ((ex["before"],.255,first,pivot,True),
                                   (ex["before"],.255,pivot,item,False),
                                   (ex["alternative"],.745,first,switch,False),
                                   (ex["alternative"],.745,switch,item,True)):
        if a >= b:
            continue
        voice = "contrast-" + str(x)
        plan.voices[voice] = dict(plan.voices["cap"], px=52, track=0, shadow={"a":0})
        plan.voices[voice]["px"] = plan.R.fit_px(voice,label,.43*width,52)
        plan.word([a,b],label,voice,x,centre,accent if active else "#A1A7AE",
                  align="c",role="graphic",fact_type=True,text_group="spoken-comparison")
        plan.block([a,b],[x-.19,centre+.023,x+.19,centre+.026],accent if active else "#525860",z=4)
    plan.events.extend((pivot,switch))
    # The same labelled work record recurs at the same seat.
    for a,b,state in ((item,frame(ex.get("example_at",ex["gesture"])),ex["state"]), (return_frame,end,ex["outcome"])):
        if a >= b:
            continue
        span = [a,b]
        plan.block(span,[.065,.56,.935,.745],"#232B31",z=2)
        plan.block(span,[.065,.56,.073,.745],accent,z=3)
        plan.block(span,[.11,.634,.158,.661],accent,z=3)
        plan.block(span,[.114,.6365,.154,.6585],"#232B31",z=4)
        for text,y,size,colour in (("Hypothetical",.60,38,"#B6C0C8"),
                                   (ex["item"],.657,60,fg),(state,.709,46,accent)):
            voice = "item-"+str(size)
            plan.voices[voice] = dict(plan.voices["cap"], px=size, track=0, shadow={"a":0})
            plan.voices[voice]["px"] = plan.R.fit_px(voice,text,.69*width,size)
            plan.word(span,text,voice,.19,y,colour,role="graphic",
                      text_group="open-item",fact_type=True)
        plan.events.append(a)


def caption_clock(clock, marks, audio_path, ffmpeg, lead):
    """Use the local attack after a quiet gap for the two sides of a heard restart."""
    import copy
    import subprocess
    import numpy as np
    fork = copy.copy(clock)
    fork.words = list(clock.words)
    gaps = []
    for row in marks:
        frag, question, end = (row[k] for k in ("fragment","question","end"))
        for i in (frag,question,end):
            fork.words[i] = dict(fork.words[i])
        for i in (frag,question):
            w = fork.words[i]
            a, b = max(0., w["t0"]-lead-.06), w["t1"]-lead
            if b-a < .1:
                continue
            try:
                pcm = subprocess.run([ffmpeg,"-v","error","-ss",str(a),"-i",audio_path,"-t",str(b-a),
                                      "-vn","-ac","1","-ar","8000","-f","f32le","-"],
                                     capture_output=True, check=True).stdout
            except (OSError, subprocess.SubprocessError):
                # Keep the aligned word times if this audio stream cannot be decoded.
                continue
            samples = np.frombuffer(pcm,dtype="<f4")
            blocks = samples[:len(samples)//80*80].reshape(-1,80)
            if not len(blocks):
                continue
            rms = np.sqrt(np.mean(blocks**2,axis=1))
            quiet = rms < max(.002, float(rms.max())*.07)
            edges = np.diff(np.r_[False,quiet,False].astype(int))
            runs = [(hi-lo,lo,hi) for lo,hi in zip(np.where(edges==1)[0],np.where(edges==-1)[0])
                    if hi-lo >= 4 and hi+2 < len(rms) and max(rms[hi:hi+3]) > .01]
            if runs:
                _,lo,hi = max(runs)
                w["t0"] = min(w["t1"]-.01, a + hi*.01 + lead)
                if i == question:
                    gaps.append((frag, a + lo*.01 + lead))
        fork.words[frag]["w"] = (fork.words[frag]["w"][:1].upper() + fork.words[frag]["w"][1:]).rstrip(".,!?—") + "—"
        fork.words[question]["w"] = fork.words[question]["w"][:1].upper() + fork.words[question]["w"][1:]
        fork.words[end]["w"] = fork.words[end]["w"].rstrip(".,!?") + "?"
    return fork, gaps
