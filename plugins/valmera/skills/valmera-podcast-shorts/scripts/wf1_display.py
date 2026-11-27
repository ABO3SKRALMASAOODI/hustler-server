"""Source-backed display copy on the interview's word clock."""
import math
import string

import wf1_principle


WRITING = """Write for a stranger following these captions, planned pictures and story beats.
Use one display only where the viewer needs an unsaid name, an unexplained term or a figure to land.
Return viewer_text rows with first and last numbered word, printed text, and picture ID for a person's name.
Give the spoken relay one moving diagram or let its real pictures carry it. Keep the punchline for its word.
Allow the existing captions or a real document to do all the reading an interval needs.
Research, source attribution, dates of publication, stance and disagreements belong in the source record.
Never paraphrase the line being heard. Each display takes the caption's place while it is visible."""


def selected_excerpt(ref, selection):
    """The selected words and URL must belong to the same verified publication."""
    if not isinstance(selection,dict) or not selection.get("words"):
        return None
    for src in ref.get("sources") or []:
        if isinstance(src,dict) and src.get("url") == selection.get("source"):
            for q in src.get("quotations") or []:
                if isinstance(q,dict) and q.get("sha256") and q.get("words") == selection["words"]:
                    return src,q
    return None


def dated_record(ref):
    """Bind the reader's selected action and date to a verified span on that exact source."""
    record = ref.get("record")
    if not isinstance(record, dict) or not all(isinstance(record.get(k), str) and record[k].strip()
                                               for k in ("source", "heading", "event_date", "words")):
        return None
    sources = [s for s in ref.get("sources") or [] if isinstance(s, dict)]
    identifier = record.get("identifier_source")
    supporting = [identifier] if identifier != record["source"] and any(s.get("url") == identifier for s in sources) else []
    selected = selected_excerpt(ref,record)
    if selected:
        src,q = selected
        return dict(source=src["url"], also_sources=supporting, excerpt=q,
                    statement=ref.get("statement"), record=record)
    return None


def editorial(ctx, script, pack):
    args = getattr(ctx, "args", None) or {}
    moment = args.get("moment") or {}
    coverage = pack.get("coverage") or {}
    research = pack.get("referent_research") or coverage.get("research_context") or {}
    sources = {"interview": dict(url=args.get("source_url"), publisher=args.get("channel"),
                                 supports=["The speaker's own account in the numbered passage."])}
    borrowed = {r["first"]:dict(r) for r in coverage.get("borrowed_estimates") or []}
    speaker_claims = [dict(c) for c in coverage.get("speaker_claims") or []]
    definitions = {row["start"]: row.get("gloss") for row in coverage.get("contextual_definitions") or []
                   if isinstance(row, dict) and type(row.get("start")) in (int, float)}
    terms = {row["start"]:row["term"] for row in coverage.get("contextual_definitions") or []
             if isinstance(row,dict) and row.get("start") is not None and row.get("term")}
    claims = []
    import wf1_record
    for claim in wf1_record.source_claims(script.get("claims") or [], coverage):
        if claim.get("stance") == "definition" and claim.get("start") in definitions:
            gloss = definitions[claim["start"]]
            if isinstance(gloss, str) and gloss.strip():
                claims.append(dict(claim, report=gloss.strip(), term=terms.get(claim["start"]) or claim.get("term")))
        else:
            claims.append(dict(claim))
    for ref in research.get("references") or []:
        for src in ref.get("sources") or []:
            if isinstance(src, dict) and src.get("url"):
                key = src["url"]
                evidence = sources.setdefault(key, dict(url=key, publisher=src.get("publisher"), supports=[]))
                for q in src.get("quotations") or []:
                    if isinstance(q, dict) and q.get("sha256") and all(isinstance(q.get(k), str) and q[k].strip()
                                                   for k in ("words", "speaker", "event", "locator", "url")):
                        if q not in evidence.setdefault("quotations", []):
                            evidence["quotations"].append(q)
                for field in ("author", "publication_date"):
                    if src.get(field):
                        evidence[field] = src[field]
                reading = src.get("supports") or ref.get("finding")
                if reading and reading not in evidence["supports"]:
                    evidence["supports"].append(reading)
        estimate = borrowed.get(ref.get("estimate_for"))
        if estimate and all(isinstance(ref.get(k),str) and ref[k].strip() for k in ("statement","credit")):
            cited_sources = [sources[s["url"]] for s in ref.get("sources") or []
                             if isinstance(s,dict) and s.get("url") in sources
                             and not same_recording(s["url"],sources["interview"].get("url"))]
            if cited_sources:
                estimate.update(statement=ref["statement"],credit=ref["credit"],sources=cited_sources)
        # The reader ties an original to this particular claim (an author can make many).
        cited = [(src["url"], q) for src in ref.get("sources") or [] if isinstance(src, dict)
                 and src.get("url") in sources for q in src.get("quotations") or []
                 if q in sources[src["url"]].get("quotations", [])]
        selection = ref.get("selection") or {}
        for claim in claims:
            if ref.get("claim_start") is None or claim.get("start") != ref["claim_start"]:
                continue
            matches = [(url, q) for url,q in cited
                       if q["speaker"].casefold() == str(claim.get("subject")).casefold()]
            if isinstance(selection, dict) and selection:
                matches = [(url,q) for url,q in matches
                           if url == selection.get("source") and q["words"] == selection.get("words")]
            if len(matches) == 1:
                url,q = matches[0]
                original = dict(source=url, quote=q["words"])
                if selection:
                    claim["primary_excerpt"] = original
                else:
                    claim.setdefault("primary_excerpt", original)
        # The same source reader can supply the scoped line when direction chooses the live camera.
        statement = ref.get("statement")
        if isinstance(statement, str) and statement.strip() and type(ref.get("scope_for")) is int:
            urls = [s["url"] for s in ref.get("sources") or [] if isinstance(s, dict) and s.get("url") in sources]
            for claim in speaker_claims:
                if urls and claim["first"] == ref["scope_for"] and claim.get("scope_question"):
                    scoped = dict(source=urls[0], also_sources=urls[1:], statement=statement)
                    selected = dated_record(ref)
                    if selected:
                        claim["scope_fact"] = selected
                        continue
                    if claim.get("scope_kind") == "dated_record" or (claim.get("scope_fact") or {}).get("record"):
                        continue
                    entry = ref.get("program_entry")
                    if isinstance(entry, dict) and all(isinstance(entry.get(k), str) and entry[k].strip()
                                                       for k in ("title","speaker","date")):
                        scoped["program_entry"] = {k:entry[k] for k in ("title","speaker","date")}
                    # A verified span on a profile can still concern a different proposition.
                    # Keep legacy single-excerpt readings for direct policy questions.
                    chosen = [(u,q) for u,q in cited if isinstance(selection, dict) and
                              u == selection.get("source") and q["words"] == selection.get("words")]
                    if not selection and len(cited) == 1 and claim.get("stance") == "concern":
                        chosen = cited
                    if len(chosen) == 1:
                        url, excerpt = chosen[0]
                        scoped.update(source=url, also_sources=[u for u in urls if u != url], excerpt=excerpt)
                    if scoped.get("program_entry") or scoped.get("excerpt") or not claim.get("scope_fact"):
                        claim["scope_fact"] = scoped
    # The footage reader has already described these exact clips. Credit its observed action
    # to the published demo, alongside the separate policy and announcement sources.
    for line in pack.get("footage") or []:
        for clip in line.get("clips") or []:
            src = clip.get("source") or {}
            action = clip.get("shows")
            if clip.get("dull") or not isinstance(src, dict) or not (src.get("url") and src.get("channel") and action):
                continue
            source = sources.setdefault(src["url"], dict(url=src["url"], publisher=src["channel"], supports=[]))
            if action not in source["supports"]:
                source["supports"].append(action)
    from datetime import date
    from pathlib import Path
    for group in (pack.get("pictures") or {}).values():
        for rec in group:
            page = sources.get(rec.get("page"))
            asset = Path(rec.get("path") or "")
            if page and rec.get("provider") == "primary-document" and asset.is_file():
                page["checked_on"] = date.fromtimestamp(asset.stat().st_mtime).isoformat()
    return dict(live_demo=(coverage.get("source_reading") or {}).get("live_demo"),
                record_example=coverage.get("record_example"), handoffs=coverage.get("handoffs") or [], borrowed_estimates=list(borrowed.values()),
                episode_examples=coverage.get("episode_examples") or [], callback=coverage.get("callback"), speaker_claims=speaker_claims, comparisons=coverage.get("comparisons") or [], story=coverage.get("story"), claims=claims, episode_start=(script.get("window") or [0])[0],
                sources=sources, picture_windows=[{k:w[k] for k in ("first","last","name","claim","must_show","example") if k in w}
                                                  for w in coverage.get("windows", [])], speaker_inserts=[{k:r[k] for k in ('first','last')}
                    for r in (coverage.get('source_reading') or {}).get('speaker_inserts',[])], source_inserts=[{k:r[k] for k in ('first','last','shows')}
                                               for r in (coverage.get('source_reading') or {}).get('inserts',[])], coverage=[{k: row[k] for k in ("name", "must_show", "word_ids") if k in row}
                                          for row in coverage.get("entities", [])], speakers=moment.get("names"), moment=moment.get("why"),
                identity=args.get("identity") or coverage.get("context"), brief=script.get("direction"))


def claim_windows(editorial, words):
    from film import FPS, LEAD_F
    origin = editorial.get("episode_start", 0) - LEAD_F / FPS
    spans = []
    for claim in editorial.get("claims", []):
        if not isinstance(claim, dict) or not all(type(claim.get(k)) in (float, int) for k in ("start", "end")):
            continue
        ids = [i for i, w in enumerate(words)
               if claim["start"] <= origin + (w["t0"] + w["t1"]) / 2 < claim["end"]]
        if ids and all(isinstance(claim.get(k), str) and claim[k].strip() for k in ("speaker", "report")):
            spans.append(dict(claim, first=ids[0], last=ids[-1]))
    # The picture director has aligned these narrower judgments to the final word clock.
    local = list(editorial.get("speaker_claims") or [])
    return local + [span for span in spans if not any(
        row["speaker"] == span["speaker"] and row["first"] <= span["first"] and span["last"] <= row["last"]
        and (row.get("stance") == "observation" or row.get("scope_kind") == "dated_record")
        for row in local)]


CONTEXT_LABELS = {"framework": "Framework", "definition": "Definition"}
def same_recording(left, right):
    """A time locator identifies an interval of the interview, not another publication."""
    from urllib.parse import parse_qsl, urlsplit
    def identity(url):
        p = urlsplit(url)
        return (p.scheme,p.netloc,p.path,sorted((k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
                                                if k not in ("t","start","time_continue")),
                "" if p.fragment.startswith("t=") else p.fragment)
    return bool(left and right and identity(left) == identity(right))


def viewer_text(rows, words):
    """Bind the display writer's copy to its spoken sentence and exact interval."""
    chosen = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        start, end = row.get("first"), row.get("last")
        text = row.get("text")
        if type(start) is int and type(end) is int and 0 <= start <= end < len(words) and isinstance(text, str) and text.strip():
            seg = words[start]["seg"]
            if words[end]["seg"] == seg:
                chosen.setdefault(seg, dict(row, seg=seg, text=text.strip()))
    return list(chosen.values())


def reading_card(plan, span, body, byline, source, palette, *, centre=.64, max_height=.303, group="quote", steps=()):
    """One stable reading surface. Page the body at its reading size, with its credit on every page."""
    from film import W, H
    plan.voices["quote_body"] = dict(plan.voices["dsp"], px=65, track=0, shadow={"a": 0})
    plan.voices["quote_credit"] = dict(plan.voices["cap"], px=48, track=0, shadow={"a": 0})
    left, width = .08, (.48 if group=="figure-credit" else .76)*W
    if steps:
        # The states share a fixed rail; the active node changes on its word
        # without rewrapping or recentering the surrounding labels.
        width -= .05*W
        plan.voices["sequence_label"] = dict(plan.voices["cap"], px=55, track=0)

    def wrap(text, voice):
        rows = []
        for word in text.split():
            if rows and plan.R.width(voice, rows[-1]+" "+word) <= width:
                rows[-1] += " "+word
            elif plan.R.width(voice, word) <= width:
                rows.append(word)
            else:
                # Long identifiers and unspaced text still wrap at the same reading size.
                part = ""
                for char in word:
                    if part and plan.R.width(voice, part+char) > width:
                        rows.append(part)
                        part = ""
                    part += char
                rows.append(part)
        return rows

    header = ([line for citation in byline.splitlines() for line in wrap(citation,"quote_credit")]
              if group == "estimate-scope" else wrap(byline, "quote_credit"))
    footer = wrap(source, "quote_credit")
    body_rows = wrap(body, "quote_body")
    def measure(text, voice, colour, node=None):
        style = plan.voices[voice]
        quads = plan.R.T.Face.of(style).quads(text,style["px"],style["track"])
        top = min((q[2] for q in quads),default=0)
        bottom = max((q[4] for q in quads),default=0)
        return text,voice,colour,top,bottom,node

    credit = "quote_credit"
    head = [measure(row,credit,palette["accent"]) for row in header]
    foot = [measure(row,credit,palette["accent"]) for row in footer]
    body = [measure(row,"quote_body",palette["ink"]) for row in body_rows]
    nodes = []
    for label, active in steps:
        lines = []
        for i, line in enumerate(wrap(label, "sequence_label")):
            node = ("●" if active else "○") if i == 0 else None
            lines.append(measure(line, "sequence_label", palette["accent"] if active else palette["ink"], node))
        nodes.append((lines, active))
    # Seat the shaped ink, including descenders. Each page repeats the evidence
    # credit; its share of the interval follows the number of words on that page.
    gap = 26
    def height(rows):
        return sum(bottom-top for _,_,_,top,bottom,_ in rows) + gap*(len(rows)-1)

    if nodes:
        # Keep a wrapped label together for the whole spoken state. Fit the
        # gutters first. If the list needs more room, turn the page on its cue.
        all_rows = head + body + [row for lines, _ in nodes for row in lines] + foot
        gutters = max(1, len(all_rows) - 1)
        gap = max(16, min(gap, gap + (max_height * H - height(all_rows)) / gutters))
        active_node = next((i for i, (_, active) in enumerate(nodes) if active), 0)
        page = []
        for i, (lines, _) in enumerate(nodes):
            if page and height(head + body + page + lines + foot) > max_height * H:
                if i > active_node:
                    break
                page = []
            page.extend(lines)
        body.extend(page)
    pages = []
    for row in body:
        if not pages or height(head+pages[-1]+[row]+foot) > max_height*H:
            pages.append([])
        pages[-1].append(row)
    if not pages and (head or foot):
        pages.append([])
    weights = [max(1, sum(len(row[0].split()) for row in page)) for page in pages]
    total = sum(weights)
    start,end = span
    elapsed = 0
    for page,weight in zip(pages,weights):
        a = start+round((end-start)*elapsed/total)
        elapsed += weight
        b = start+round((end-start)*elapsed/total)
        rows = head+page+foot
        y = centre*H-height(rows)/2
        for row,voice,colour,top,bottom,node in rows:
            baseline = (y-top)/H
            attrs = dict(role="graphic", z=8, fact_type=True, text_group=f"{group}-{a}", note=False)
            if node:
                plan.word([a,b],node,credit,left,baseline,colour,**attrs)
            x = left + .05 if voice == "sequence_label" else left
            plan.word([a,b],row,voice,x,baseline,colour,**attrs)
            y += bottom-top+gap
