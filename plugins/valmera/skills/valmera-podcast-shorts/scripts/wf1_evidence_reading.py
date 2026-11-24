"""Seat a selected publisher result or a compact source history on its spoken cue."""
from wf1_documents import valid_detail


def history_for(references, index, page):
    for ref in references:
        if index not in (ref.get("windows") or []):
            continue
        sources = {s["url"]:s for s in ref.get("sources") or [] if isinstance(s,dict) and s.get("url")}
        rows = ref.get("history") or []
        if not (isinstance(rows,list) and 2 <= len(rows) <= 3 and all(isinstance(r,dict) for r in rows)):
            continue
        if rows[-1].get("source") != page:
            continue
        if all(r.get("source") in sources and all(isinstance(r.get(k), str) and r[k].strip()
                                                  for k in ("stamp","label")) for r in rows):
            return [dict(stamp=r["stamp"], label=r["label"], source=sources[r["source"]]) for r in rows]
    return []


def seat(rows, coverage, references, word_frame, word_count, end):
    """Move only a matched reading, bounded by the adjacent selected pictures."""
    planned = []
    for a,b,kind,name,rec in rows:
        i = rec.get("window")
        read = None
        if rec.get("provider") == "primary-document" and type(i) is int and 0 <= i < len(coverage["windows"]):
            example = coverage["windows"][i].get("example") or {}
            clock = example.get("reading") or {}
            ids = [clock.get(k) for k in ("first","at","last")] if isinstance(clock,dict) else []
            if (len(ids) == 3 and all(type(v) is int for v in ids) and 0 <= ids[0] <= ids[1] <= ids[2] < word_count
                    and example.get("source") == rec.get("page")):
                history = history_for(references, i, rec["page"])
                marked = (example.get("figure_url") == (rec.get("document") or {}).get("url")
                          and valid_detail(rec.get("action_box")) and valid_detail(rec.get("mark_box")))
                if history or marked:
                    read = dict(history=history, at=word_frame(ids[1]), first=ids[0], last=ids[2],
                                text=example.get("visible_statement") or example.get("statement") or "",
                                owner=clock.get("speaker") if isinstance(clock.get("speaker"),str) else "")
                    a, b = word_frame(ids[0]), word_frame(ids[2]+1) if ids[2]+1 < word_count else end
        planned.append([a,b,kind,name,dict(rec,spoken_reading=read,audit_until=b) if read else rec])
    for j,row in enumerate(planned):
        if "spoken_reading" in row[4]:
            if j:
                row[0] = max(row[0], planned[j-1][1])
            if j+1 < len(planned):
                row[1] = min(row[1], planned[j+1][0])
            if row[1] <= row[0]:
                # Two views of the same document can carry one direction. Keep the original cuts.
                return rows
    return planned


def compose(plan, span, key, rec, palette, size):
    from wf1_display import reading_card
    read = rec["spoken_reading"]
    plan.page(span, "#14171B", grain=0)
    history = read["history"]
    if history:
        for i,row in enumerate(history):
            centre = .29 + i * .164
            reading_card(plan, span, row["label"], row["stamp"], "", palette,
                         centre=centre, max_height=.15, group="source-history-"+str(i))
        return
    from scene.motion import marked_figure
    box, mark = marked_figure(rec["action_box"], rec["mark_box"], (rec["w"],rec["h"]), size)
    plan.card(span,key,box,crop=rec["action_box"],source_ink=[box],document_excerpt=True,
              look=None,grade=dict(gain=1.),caption_baseline=.88)
    hit = max(span[0], read["at"])
    if hit < span[1]:
        plan.block([hit,span[1]],mark,palette["accent"],z=2)
    credit = " · ".join(filter(None, (rec.get("credit"), "Example")))
    reading_card(plan,span,read["text"],credit,"",palette,centre=.63,max_height=.23,group="source-result")
    if read["owner"]:
        reading_card(plan,span,"",read["owner"]+" replies","",palette,centre=.22,max_height=.10,group="reply")
