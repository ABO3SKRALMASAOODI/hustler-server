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


def draw(plan, example, frame, end, palette, width):
    from wf1_display import reading_card

    start = frame(example["first"])
    stations = example["stations"]
    group = f"approval-{start}"
    ink, accent = palette["ink"], palette["accent"]
    reading_card(plan, [start,end], "", "Illustration · "+example["job"], "", palette,
                 centre=.499, max_height=.055, group=group+"-title")
    # The work travels only between the selected recipients. Other possible
    # approvals are annotations, never stops on this route.
    stops = [.15 + .69*i/(len(stations)-1) for i in range(len(stations))]
    times = [frame(row["at"]) for row in stations] + [end]
    rail_y = .620
    motion = [stops[0]] * (times[0] - start)
    for i,(row,x,a,b) in enumerate(zip(stations,stops,times,times[1:])):
        label = row["label"]
        face = group+f"-label-{i}"
        plan.voices[face] = dict(plan.voices["cap"], px=44, track=0, shadow={"a":0})
        space = min(.90*.69/(len(stations)-1), 2*min(x,1-x)-.04)
        plan.voices[face]["px"] = plan.R.fit_px(face,label,space*width,44)
        plan.word([a,end],label,face,x,.596,ink,align="c",role="graphic",
                  text_group=group+"-stations",fact_type=True)
        previous = stops[max(0,i-1)]
        if i:
            plan.block([a,end],[previous,rail_y-.0015,x,rail_y+.0015],accent,z=4,op=.70)
        for j in range(b-a):
            u = min(1., (j+1)/min(9,b-a))
            motion.append(previous+(x-previous)*(1-(1-u)**3))
        plan.events.append(a)
    marker = plan.block([start,end],[0,rail_y-.006,.023,rail_y+.006],accent,z=5)
    marker.update(x=motion, approval_job=True)
    states = example["states"]
    for i,row in enumerate(states):
        a = frame(row["at"])
        b = frame(states[i+1]["at"]) if i+1 < len(states) else end
        if a < b:
            reading_card(plan,[a,b],row["label"],"","",palette,centre=.724,max_height=.132,
                         group=group+"-state")
