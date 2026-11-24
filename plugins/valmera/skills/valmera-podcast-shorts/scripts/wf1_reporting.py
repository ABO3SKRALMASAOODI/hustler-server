"""One participant's account of a reporting chain, on the episode's word clock."""


def example(raw, word_count, interview_cues=()):
    if not isinstance(raw, dict):
        return None
    keys = ("first", "finding_at", "flat_at", "last")
    if not all(type(raw.get(k)) is int for k in keys):
        return None
    first, finding, flat, last = (raw[k] for k in keys)
    if not 0 <= first <= finding < flat <= last < word_count:
        return None
    if not all(isinstance(raw.get(k), str) and raw[k].strip() for k in ("speaker","question","finding")):
        return None

    def cues(name, field, lower):
        rows = raw.get(name)
        timed = {}
        for row in rows if isinstance(rows,list) else ():
            if not isinstance(row,dict):
                continue
            at, value = row.get("at"), row.get(field)
            if type(at) is int and lower <= at < flat and isinstance(value,(str,int)) and str(value).strip():
                timed[at] = dict(row, at=at, **{field:str(value).strip()})
        return [timed[k] for k in sorted(timed)]

    levels = cues("levels", "label", finding)
    if len(levels) < 2:
        return None
    counts = cues("counts", "value", levels[0]["at"])
    # A count with no explicit earlier referent belongs in the adjacent quotation.
    for row in counts:
        if not any(row.get("level_at") == level["at"] <= row["at"] for level in levels):
            row.pop("level_at",None)
    # A director may anchor the answer on the last word of a clause. Its attributed
    # reading can start as soon as the storyteller begins giving that answer.
    onset = next((c["first"] for c in interview_cues
                  if first < c["first"] <= finding <= c["last"] < levels[0]["at"]
                  and c.get("speaker") == raw["speaker"]), finding)
    return dict(raw, finding_at=onset, levels=levels, counts=counts)


def draw(plan, start, end, rec, frame, palette, width, height):
    from scene.palette.type import reporting_chain
    from wf1_display import reading_card

    finding = frame(rec["finding_at"])
    levels = [(frame(row["at"]),row["label"]) for row in rec["levels"]]
    counts = [(frame(row["at"]),row["value"],frame(row["level_at"]) if "level_at" in row else None)
              for row in rec["counts"]]
    flat = frame(rec["flat_at"])
    reading_card(plan,[start,end],"",rec["speaker"]+" · Recollection","",palette,
                 centre=.485,max_height=.06,group="reporting-credit")
    for a,b,text in ((start,finding,rec["question"]),(finding,levels[0][0],rec["finding"])):
        if a < b:
            reading_card(plan,[a,b],text,"","",palette,centre=.625,max_height=.22,group="reporting-summary")
    result = reporting_chain(f"reporting-{start}",end,levels,counts,flat,width=width,height=height,
                             voice=plan.voices["cap"],ink=palette["ink"],accent=palette["accent"])
    plan.items.extend(result["items"])
    plan.voices.update(result["voices"])
    plan.events.extend([start,finding,flat,*(f for f,_ in levels),*(f for f,_,_ in counts)])
