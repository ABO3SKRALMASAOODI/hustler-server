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


def compose_notes(plan, argument, word_frame, end, palette, size, publication):
    from scene.palette.type import inference_gap
    from wf1_display import reading_card

    speaker = argument["speaker"]
    premise_credit = publication["author"] + "’s hypothesis" if publication else speaker + " on the premise"
    first, observation, gap, close = (word_frame(argument[k]) for k in ("first","observation","gap","close"))
    # The live take has its own reserved seat above the note. The final question
    # belongs to the voice even when the show's picture has already cut away.
    premise = (publication or {}).get("hypothesis") or argument["premise"]
    notes = ((first,observation,premise_credit,premise),
             (observation,gap,speaker + " on current progress",argument["present"]),
             (gap,close,speaker,argument["question"]),
             (close,end,speaker,argument["question"]))
    for a,b,by,body in notes:
        if b <= a:
            continue
        closing = a == close
        reading_card(plan,[a,b],body,by,"",palette,centre=.50 if closing else .65,
                     max_height=.24,group="publication-argument")
        if a >= gap:
            dots = [a] * 3 if closing else [word_frame(i) for i in range(argument["gap"], min(
                argument["gap"]+3, argument["close"]))]
            diagram = inference_gap(f"inference-{a}",[a,b],dots,width=size[0],height=size[1],
                voice=plan.voices["cap"],ink=palette["ink"],accent=palette["accent"],y=.63 if closing else .525)
            plan.items.extend(diagram["items"])
            plan.voices.update(diagram["voices"])
