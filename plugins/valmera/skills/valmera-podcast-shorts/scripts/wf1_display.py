"""Source-backed display copy on the interview's word clock."""
import math
import string

import wf1_principle


WRITING = """Write the factual type for this passage from the attached source readings and editorial brief.
When examples name alternative approvers and a possible wait for agreement, return approval_example.
Give first,last (inclusive word IDs), job (one short illustrative object), stations:[{at,label}]
for its preparation, ONE selected recipient, and any explicitly conditional later handoff.
Start the first station at first. Keep station labels short. Give states:[{at,label}] for what
each alternative decides, why this same job can remain open through another meeting and what
might happen next. Put unselected approvers only in states, never in the route. Anchor a short
return to the opening idea on the actual last thought, after the possible escalation. Keep the
interviewee's attributed measurement separate from the example. Company documentation may explain
public approval capabilities only. Use the live source for the two-person exchange and gestures.
When the speaker recounts a reporting chain and contrasts organizational depth with flatness, return a
top-level reporting_example object: first and last word IDs for the whole recollection (question through
payoff), speaker, question (concrete standalone paraphrase, e.g. "Who reports to whom?"), finding_at,
finding (compact answer scoped to the part the speaker describes), flat_at (first word naming flatness),
levels:[{at,label}] in the spoken order from person upwards, using the word naming each person, and
counts:[{at,value}] with each voiced count in digits at its exact word. Supply a count's optional
level_at (that level's word ID) ONLY if the speaker explicitly identifies the particular manager for it.
A series of numbers in the same order or of the same length leaves the counts unassigned. Use short
role labels, one credit, no extra names. The compositor builds this continuous illustration on the live
take and preserves the spoken contrast. Keep the original caption phrases and other beats outside it.
record_example is an attributed scenario composed from the interview's actual words. Its opening
belongs to the live storyteller, including pauses in an analogy. Compose other devices outside that
passage. The cited line returns at the proposed benefit. Documentation can establish access to a
connected, authorized source with a citation; a participant's private meaning remains an inference.
For dated archive in archive_pictures, return archive_credits:[{picture: the supplied picture ID,
source: the supporting URL in sources, label: compact original venue or event and its year}].
For adjacent views of one dated event, use its same verified venue and date across the shot.
On a speaker's observation or recollection, the caption carries the exact chronology or joke. A dated_record
carries the independent event, the actual parties and its time. Leave their future intentions with the speaker.
For photographs, identify the artifact and its owner as a period example, distinguish a documented
original from a replica, and date the event separately from the photograph. Use the exact image page
or its supporting source. The on-screen label is a short museum caption: institution · artifact (event
year). Lead with the artifact's historical institution and specific role. Keep its present-day museum
in the record unless that venue is the subject. Put the named photographer and derivative artist on
one short credit line. Leave the venue history, photo dates and longer sourcing in the records.
For photos proving the same assertion on one picture_window, return archive_views:[{first: window's
first word, pictures: [picture IDs in order of preference]}]. Rank by documented identity, visible
clarity and usable credit. Group equivalent evidence even across photographers and exhibits. A view
with distinct evidence relevant on these words stands separately. Read prior checks in archive_pictures.
Date the pictured edition or occasion from the primary reading. The publication date of a later
account does not date its pictures. Credit a paper by venue and year; its full authors are in sources.
Use picture_windows to tie the source and the displayed fact to the same proposition. Label an
unnamed category's sourced illustration explicitly as an example and state its documented scope.
For an archive program use the exact talk title and event date beside the interview. For a sequence
of real processes use sequence_kind="process". Give each step its supporting source and a compact,
concrete action readable on its spoken cue. Use two or three words for a subsecond step. Distinguish a documented example from the speaker's words.
Use the original author’s dated words for a reported forecast. Keep their scope and uncertainty.
A sourced explanation owns its clause; use a scenario on separate words only when it adds a distinct point.
Also return caption_ends, the inclusive numbered last word of every short, complete spoken reading unit,
in order through the final word. Each appears whole on the phone. Group repeated clauses separately;
keep articles, conjunctions and auxiliaries with their thought. Aim for 3–7 words and about 28 characters.
Use display for emphases inside those phrases. For the causal mechanism of a decision that the available
footage cannot show, score its sequence on the concrete example, with a staircase ask on that word range:
args.sequence=[{"at": first word number of a state, "label": compact explanatory label}, ...],
args.speaker=the person explaining it. When recounting real events, set args.sequence_kind="history".
Use this same plate as a documentary timeline: give each step a source key from sources ("interview"
for remembered motives and impressions, a primary record for a dated event). Write the scope in the label:
a team's experience seeking wider adoption can coexist with documented early users. Keep production use
with its dated institutional source. Focus the sequence on the decision and use dated original images
where they show the research. Preserve "scenario" for an illustrative or hypothetical sequence. If callback is supplied, use its sourced question as the final
state starting on the question. Name the action from the earlier episode excerpt and keep anonymity. Show the alternatives, their distinct effects and the reaction that
links them. The labels form one qualitative diagram. Word attributions and scenario framing keep it
an illustration of the interview. Avoid invented measurements, issuer histories and extra acquisition.
episode_examples already have a labeled illustration on the live episode; leave those graphics to composition.
Keep the full contrast and the complete host reply in captions. Use the script's definition and framework claims on their addressed words. A spoken count can introduce
one requirement explored here; name the requirement as an action that stands alone, then give its reading time to the concrete steps. Resolve a
short form on first use, with enough time to read it. Keep the estimate separate from what a demo shows.
handoffs and borrowed_estimates already have word-addressed compositions. Leave their labels to
those compositions. Finish the typography for each handoff in ONE staircase ask, using
args.handoff_for (first word ID), args.title (only the thing moving, e.g. "Customer feedback"),
and args.sequence=[{at: unchanged step ID,label: receiving team's short name only}]. No verbs,
role descriptions or repeated title in the route labels. Each station will share the same diagram.
If a borrowed estimate has only its question, use its existing checked sources
to finish it in a facts ask with args.estimate_for (its first word ID) and args.facts=[{source,
also_sources,statement,credit}] and args.application_label (a short cue naming the new domain as an analogy). Use a short line of estimated quantity, unit and uncertainty. Credit
the author/date and, where consequential, the later reassessment separately from the interview.
Keep any representative request in its own source seat, then return to the live
gestures. Let the final condition land in the original speaker's exact cue, including during a reaction.
Use separate captions for the literal speech. A visual change to the camera or a paraphrased pull quote
alone cannot explain a changing relationship.
For each date, amount, target or estimate worth displaying, put facts in its type ask's args:
"facts":[{"source": a URL from sources (or "interview"), "relation": short event/meaning,
"value": the date or amount with its units, "attribution": the actual speaker for an estimate,
"qualifier": their qualifying words}]. For supporting citations also write fact.statement, a single short
standalone sentence of about 8–12 words carrying the point and its qualification. Put an event year in that statement when
it matters. Put the publication date separately in fact.publication_date only when documented.
speaker_claims are the word-addressed opinions of the person on camera. For a sourced qualification
to a concern, put a facts ask on those words with fact.statement giving the applicable product,
default and material exception in one readable cue. Use fact.source for the primary URL and
fact.also_sources for other providers whose policies support that same wording. The byline
identifies whose concern we hear. Let the demos show what the tools do and the partnership
citation show how the companies work together. Keep the speaker and their qualification on a
competitive verdict. A bare editorial slogan loses both. Keep attribution, original date/value, literal quotation and relation in their own fields.
For a forecast or reported speech carry the claims chain from the brief. The speaker is the voice heard;
the subject may be someone else. Put a facts ask on the claim. If the brief lacks a chain, include
args.claim={speaker,subject,event,report} from this passage, with a short qualified indirect sentence.
To quote an original, use fact.quote copied whole from that source’s quotations record. A research
summary or an event link alone supports no direct quotation. Otherwise show the speaker’s report.
For a speaker illustrating a personal estimate by comparing two scales, use args.sequence_kind="comparison"
and args.sequence over the claim. Address each state on the first word that actually discloses it, including
the final multiplier. Label the scenarios briefly and leave the final quantity to the spoken caption.
For two approximate alternatives, copy each complete spoken quantity with its unit as the label;
keep the first visible when the second lands. The following qualification belongs to this same example.
For a concluding personal scale assessment, set args.lands to the word voicing its quantity.
A standalone estimate gets one readable statement in its own interval.
Use claim.stance (discussed_estimate or personal_estimate), a short indirect report and the actual speaker.
For a long exact quotation required as evidence, use its whole word span. Let this card carry the reading;
resume ordinary captioning around it. Choose the shortest complete sourced statement that serves the point.
For a picture effect or large word, identify the contrast that changes the listener's understanding. Anchor
it on the phrase that completes that contrast and release it when the thought moves on. A stressed generic
noun alone can miss the argument. Reserve type performances and picture effects for the other intervals.
Give at and until as the first and last word of the full clause that can carry the copy.
Check the event dated by the source. When recollection compresses distinct events, label each
event and date separately on the date beat. Attribute estimates to the person speaking in this
passage and preserve the actual wording, unit and what they are estimating. Keep factual type in
these asks; ordinary display is for other spoken phrases. The interview and captions stay verbatim.
A fixed amount lands whole. Only for a measured progression the story actually follows, use a
counter with one fact and include progression: {"from": number, "to": number} in that fact, with
both endpoints supported by the source, and give the counter its matching fmt and unit.
For each optional sound beat give sound_partner with the dependencies its reason names: before and
after are exact contact-sheet picture IDs at a substitution (use "take" for the speaker), device is the requested device on
this beat, text is the exact line for a display landing. Include both picture IDs when the change is
from one illustration into another, even if a device also participates. Omit unused fields.
Comparisons can be acted out on the same live take. For a named instrument with a qualitative
change, use analogy_dial: at the word naming the physical instrument or brand, args.change at
the word changing its reading, args.trend up or down, until the last word before the proposed
explanation. The comparison's instrument addresses are its word clock. The original diagram
illustrates the speaker's hypothetical, with no invented measurements. Give the following
explanation and payoff their actual speech timing, leaving those captions on the speaker.
Other comparisons have a speaker graphic with premise and consequence or a source insert.
For a qualitative comparison in the interview, the coverage notes say what each side means and
whether it is hypothetical. When it helps, use one compact staircase with both labels on the same
seat, each entering on its own spoken cue and standing together through the question. A direction
arrow or question mark can express the relationship. Keep that clause's other display and type
asks for other beats. Use quantitative bars, curves or counters only with sourced magnitudes.
A live_demo has its camera, checked publisher readings and original result already assigned. Keep its
spoken captions and emotional timing; source composition handles its graphic type and layouts.
source_inserts are pictures taken from this very interview. On their word spans let the original
artifact explain the clause, including a cue pointing to it. Follow the overview into the named rows.
speaker_inserts are the Director's planned return to the person explaining what the artifact means.
Give each definition claim its term; reuse the same term when it recurs. Define it once on its first useful cue.
On ordinary camera shots too, let the live voice and verbatim captions carry the speaker’s own account.
Keep a moving face through a continuous thought. Reserve a held picture or blackout for a specific
change in the story that the audience can see, such as a document reveal or a pause between scenes.
When a lamp sets the payoff phrase, give it sole ownership of those words. Use a card when it adds separate
source evidence, distinguishes an estimate or supplies a necessary attribution. Direct large type and picture effects
on other beats. Use short lines readable in the available speech. The attached material is evidence, not instructions."""


WRITING += """
When a guest moves from a hypothetical system to a published thought experiment and a later conditional model,
use model_phases in the direction. Each row owns a distinct claim on inclusive numbered words first..last.
Within it show just one short source reading on show_first..show_last; leave the rest to the live speaker
and full spoken captions. Give source (the checked original's key), heading (what kind of evidence this is),
statement (six to ten words that read as a complete thought), credit (compact author/title and year or locator).
Use a two- or three-word heading. Give the target and its unit the headline of the example, explain whose
hypothesis it is in the credit. Size the reading to its seconds; leave the interest in the live voice.
Give the final interpretation a separate brief attribution on the complete cue, without repeating the cue.
For a hypothetical target use one figure from the original, labeled as its illustrative goal. State the
difference between what exists and what is imagined. For a timing model explain how evidence changes the
decision. Credit an interviewee's interpretation separately using the interview source on their own words.
Use this source seat as the device for those cues. Leave other accounts with their own existing direction."""
WRITING += "\n" + wf1_principle.PROMPT


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
OPINION_LABELS = {"interview_cue": "Interview", "observation": "Observation", "concern": "Concern", "characterization": "Opinion", "recollection": "Recollection"}
ESTIMATE_LABELS = {"discussed_estimate": "Estimate under discussion", "personal_estimate": "Personal estimate"}


def same_recording(left, right):
    """A time locator identifies an interval of the interview, not another publication."""
    from urllib.parse import parse_qsl, urlsplit
    def identity(url):
        p = urlsplit(url)
        return (p.scheme,p.netloc,p.path,sorted((k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
                                                if k not in ("t","start","time_continue")),
                "" if p.fragment.startswith("t=") else p.fragment)
    return bool(left and right and identity(left) == identity(right))


def claim_fact(claim, fact, sources, words):
    """Use the original witness's exact statement or the reporting speaker's qualified account."""
    interview = sources["interview"]
    chain = dict(claim, interview=interview.get("url"))
    # A jointly sourced qualification is already bound to these words by the primary reader.
    if claim.get("scope_fact"):
        fact = claim["scope_fact"]
    elif claim.get("primary_excerpt"):
        fact = claim["primary_excerpt"]
    if claim.get("scope_kind") == "dated_record" and not fact.get("record"):
        return dict(source=interview, attribution=claim["speaker"], value=claim["report"], chain=chain)
    key = fact.get("source")
    source = sources.get(key) if isinstance(key, str) else None
    if claim.get("stance") in OPINION_LABELS:
        statement = fact.get("statement")
        supporting = []
        for item in [source, *(sources.get(k) for k in fact.get("also_sources") or [] if isinstance(k,str))]:
            if not item or item in supporting:
                continue
            if same_recording(item.get("url"),interview.get("url")) and not fact.get("excerpt"):
                continue
            supporting.append(item)
        if supporting and isinstance(statement,str) and statement.strip():
            source = supporting[0]
            excerpt = fact.get("excerpt")
            verified = excerpt and excerpt in source.get("quotations", [])
            return dict(statement=statement, value=statement, source=source, supporting_sources=supporting,
                        **({"program_entry": fact["program_entry"]} if fact.get("program_entry") else {}),
                        **({"excerpt": excerpt} if verified else {}),
                        **({"record": fact["record"]} if verified and fact.get("record") else {}),
                        perspective=dict(speaker=claim["speaker"], stance=claim["stance"],
                                         report=claim["report"], interview=interview.get("url")))
        return dict(source=interview, attribution=claim["speaker"], value=claim["report"], chain=chain)
    if claim.get("stance") in CONTEXT_LABELS:
        return dict(source=interview, attribution=claim["speaker"], value=claim["report"], chain=chain)
    selected = fact.get("quote")
    for q in (source or {}).get("quotations", []):
        if (selected == q["words"] and str(claim.get("subject")).casefold() == q["speaker"].casefold()
                and (claim.get("primary_excerpt") or not claim.get("event") or str(claim["event"]).casefold() == q["event"].casefold())):
            return dict(source=dict(source, url=q["url"]), attribution=q["speaker"], excerpt=q, value="“"+q["words"]+"”",
                        relation=q["event"]+" · "+q["locator"], chain=dict(chain, original=q))
    speaker = claim["speaker"]
    # Interview quotes come from the word clock itself, including qualifications.
    if speaker == claim.get("subject") and selected:
        heard = " ".join(w["w"] for w in words[claim["first"]:claim["last"]+1])
        if selected == heard:
            stamp = int(claim.get("start", 0))
            return dict(source=interview, attribution=speaker, value="“"+heard+"”",
                        relation=f"{stamp//60}:{stamp%60:02d}", chain=chain)
    return dict(source=interview, attribution=speaker if claim.get("stance") in ESTIMATE_LABELS else speaker+" reports", value=claim["report"], chain=chain)


def model_phases(direction, sources, words):
    """Bind the director's reading interval and its source to the longer spoken claim."""
    phases = []
    for row in direction.get("model_phases") or []:
        if not isinstance(row, dict):
            continue
        points = [row.get(k) for k in ("first", "show_first", "show_last", "last")]
        if not all(type(i) is int for i in points) or not 0 <= points[0] <= points[1] <= points[2] <= points[3] < len(words):
            continue
        if not all(isinstance(row.get(k), str) and row[k].strip() for k in ("source", "heading", "statement", "credit")):
            continue
        source = sources.get(row["source"])
        if source and source.get("url") and (not phases or phases[-1]["last"] < row["first"]):
            phases.append(dict(row, source=source))
    return phases


def model_claim_start(pages, phase):
    """Include the interview setup when the director starts its source phase mid-definition."""
    first = phase["first"]
    if phase.get("claim"):
        return first
    owner = None
    for page in sorted(pages, key=lambda row: row["last"], reverse=True):
        if len(page["facts"]) != 1:
            continue
        fact = page["facts"][0]
        claim = fact.get("chain") or {}
        subject = (claim.get("speaker"), claim.get("subject"))
        joins_setup = owner == subject and first == page["last"] + 1
        if (page["first"] < first and (first <= page["last"] or joins_setup)
                and claim.get("stance") in ("framework", "definition")
                and same_recording(fact.get("source", {}).get("url"), claim.get("interview"))):
            first, owner = page["first"], subject
    return first


def research_phases(story, words, source):
    """The interview owns its project introduction, including the spoken project name."""
    claims = [c for c in story.get("claims", []) if isinstance(c,dict) and c.get("stance") == "research_project"]
    phases = []
    for claim in claim_windows(dict(episode_start=story.get("episode_start",0), claims=claims), words):
        first, last = claim["first"], claim["last"]
        # Episode subtitle anchors can fall inside the first word of a sentence.
        if first == 1 and words[0]["seg"] == words[first]["seg"]:
            first = 0
        phases.append(dict(first=first, last=last, show_first=first, show_last=last, source=source,
                           heading=claim["speaker"]+" · Interview", statement=claim["report"],
                           credit=claim.get("stage") or "", claim=claim))
    return phases


def research_comparison(comparison, phase, words):
    """Retain the two case names through the implication, independently of the verbatim cue."""
    start, last = comparison["first"], comparison["last"]
    reveal = comparison.get("reveal")
    if reveal is None or not start < reveal <= last:
        return None
    steps = comparison.get("steps") or []
    first = next((s for s in steps if s["at"] < reveal), {})
    second = next((s for s in steps if s["at"] == reveal), {})
    switch = reveal
    # A retained director may point at the verb inside the second sentence.
    # Start with that sentence's subject. Within one sentence keep the addressed word.
    if words[reveal]["seg"] != words[first.get("at",start)]["seg"]:
        while switch > start and words[switch-1]["seg"] == words[reveal]["seg"]:
            switch -= 1
    fact = dict(source=phase["source"], attribution=phase["claim"]["speaker"])
    return [dict(first=a, last=b, analogy=True, facts=[dict(fact,value=label)],
                 analogy_step=dict(step,label=label), research_stage=phase["credit"])
            for a,b,step,label in [(start,switch-1,first,first.get("label") or comparison["premise"]),
                                   (switch,last,second,second.get("label") or comparison["consequence"])]]


def model_pages(pages, phases):
    if not phases:
        return pages
    for phase in phases:
        first = model_claim_start(pages, phase)
        pages = [part for page in pages for part in wf1_principle.outside(page, first, phase["last"])]
        pages.append(dict(first=phase["show_first"], last=phase["show_last"], model_phase=True,
                          heading=phase["heading"], credit=phase["credit"],
                          facts=[dict(value=phase["statement"], source=phase["source"])]))
    # A director can credit the guest with a regular interview fact on the return.
    # Match its named subject to the author of the model just shown.
    for i, page in enumerate(pages):
        if len(page["facts"]) != 1 or page.get("model_phase"):
            continue
        fact = page["facts"][0]
        claim = fact.get("chain") or {}
        preceding = [p for p in phases if p["last"] < page["first"]]
        if (preceding and claim.get("speaker") and claim.get("subject") and
                claim["subject"] == preceding[-1]["source"].get("author") and
                same_recording(fact.get("source", {}).get("url"), claim.get("interview"))):
            pages[i] = dict(page, model_phase=True, heading=claim["speaker"]+"’s reading", credit="",
                        facts=[dict(fact, value="")])
    return sorted(pages, key=lambda page: page["first"])


def spoken_alternatives(steps, words, first, last):
    """Bind each quoted alternative to the whole contiguous phrase containing its scored beat."""
    def tokens(text):
        return [t.casefold().strip(string.punctuation) for t in text.split()]
    spoken = [tokens(w["w"]) for w in words]
    bound = []
    for step in steps:
        label = tokens(step["label"])
        left = max(first, step["at"]-len(label)+1, bound[-1]["last"]+1 if bound else first)
        for a in range(left, min(step["at"], last-len(label)+1)+1):
            if spoken[a:a+len(label)] == [[t] for t in label]:
                bound.append(dict(step, at=a, last=a+len(label)-1))
                break
        else:
            return []
    return bound


def audience_page(page):
    """A research claim supplies the record. A designed illustration or authored viewer line supplies type."""
    return bool(page.get("viewer_line") or any(page.get(k) for k in (
        "sequence", "publisher_excerpt", "record_role", "analogy_step", "model_phase",
        "principle_plate", "scope_cue", "episode_example", "demo_note", "estimate_landing")))


def bind(direction, editorial, words):
    """Compile the writer's selected statements into complete display blocks. Addresses are spoken word IDs."""
    sources = editorial["sources"]
    pages, asks = [], []
    claims = claim_windows(editorial, words)
    accents = set()
    ends = direction.get("caption_ends") or []
    if not ends and direction.get("by") == "signal":
        ends = [i for i,w in enumerate(words) if i+1 == len(words) or words[i+1]["seg"] != w["seg"]]
    whole = bool(ends)
    display = []
    def literal_ids(copy, ids):
        # Word identity within the addressed occurrence, preserving repeated phrases.
        tokens = [s.casefold().strip(string.punctuation) for s in copy.split()]
        remaining = iter(ids)
        matched = []
        for token in tokens:
            i = next((i for i in remaining if words[i]["w"].casefold().strip(string.punctuation) == token), None)
            if i is None:
                return []
            matched.append(i)
        return matched

    for row in direction.get("display") or []:
        ids = literal_ids(row["text"], [i for i,w in enumerate(words) if w["seg"] == row["seg"]]) if whole else []
        if ids and not row.get("perform"):
            accents.update(ids)
        else:
            display.append(row)
    beat_roles = {b.get("w"):b["role"] for b in direction.get("beats") or []
                  if b.get("role") in ("figure", "turn")}
    displayed = set()
    routes = {r["first"]:dict(r) for r in editorial.get("handoffs") or []}
    estimates = {r["first"]:dict(r) for r in editorial.get("borrowed_estimates") or []}
    for ask in direction["asks"]:
        args = ask["args"]
        route_id = args.get("handoff_for")
        if type(route_id) is int and route_id in routes:
            route = routes[route_id]
            labels = {s["at"]:s["label"].strip() for s in args.get("sequence") or []
                      if isinstance(s,dict) and type(s.get("at")) is int
                      and isinstance(s.get("label"),str) and s["label"].strip()}
            route["steps"] = [dict(step,label=labels.get(step["at"],step["label"])) for step in route["steps"]]
            if isinstance(args.get("title"),str) and args["title"].strip():
                route["title"] = args["title"].strip()
            continue
        estimate_id = args.get("estimate_for")
        if type(estimate_id) is int and estimate_id in estimates:
            estimate = estimates[estimate_id]
            if isinstance(args.get("application_label"),str) and args["application_label"].strip():
                estimate["application_label"] = args["application_label"].strip()
            for fact in args.get("facts") or []:
                if not isinstance(fact,dict):
                    continue
                keys = [fact.get("source"), *(fact.get("also_sources") or [])]
                cited = [sources[k] for k in keys if isinstance(k,str) and k in sources and
                         not same_recording(sources[k].get("url"),sources["interview"].get("url"))]
                if cited and all(isinstance(fact.get(k),str) and fact[k].strip() for k in ("statement","credit")):
                    if not estimate.get("sources"):
                        estimate.update(sources=cited, statement=fact["statement"], credit=fact["credit"])
                    break
            if estimate.get("sources"):
                continue
        facts = []
        first = ask["at"]["w"]
        last = (ask.get("until") or ask["at"])["w"]
        overlapping = [c for c in claims if c["first"] <= last and first <= c["last"]]
        claim = max(overlapping, key=lambda c: min(last,c["last"])-max(first,c["first"]), default=None)
        sequence = []
        proposed = args.get("sequence")
        for step in proposed if isinstance(proposed, list) else ():
            if (isinstance(step, dict) and type(step.get("at")) is int and first <= step["at"] <= last
                    and isinstance(step.get("label"), str) and step["label"].strip()
                    and (not sequence or sequence[-1]["at"] < step["at"])):
                state = dict(at=step["at"], label=step["label"].strip())
                if args.get("sequence_kind") in ("history", "process"):
                    key = step.get("source")
                    state["source"] = sources.get(key, sources["interview"]) if isinstance(key, str) else sources["interview"]
                    if (args["sequence_kind"] == "history" and key == "interview"
                            and state["at"] in beat_roles):
                        state["beat"] = beat_roles[state["at"]]
                sequence.append(state)
        if len(sequence) >= 2:
            if args.get("sequence_kind") == "process" and ends:
                # A caption reveals the whole phrase on its first word. Change the
                # matching example with it, preserving distinct cues inside one phrase.
                previous = first - 1
                for state in sequence:
                    phrase = max([first] + [n+1 for n in ends if first <= n < state["at"]])
                    if phrase > previous:
                        state["at"] = phrase
                    previous = state["at"]
            comparison = args.get("sequence_kind") == "comparison"
            local_claim = args["claim"] if isinstance(args.get("claim"),dict) else {}
            alternatives = []
            if comparison and (local_claim or claim or {}).get("stance") == "personal_estimate":
                alternatives = spoken_alternatives(sequence, words, first, last)
                if alternatives:
                    sequence = alternatives
                    if ends:
                        ends = [i for i in ends if not any(s["at"] <= i < s["last"] for s in sequence)]
                        ends.extend(s["last"] for s in sequence)
            if comparison:
                estimate = next((c for c in overlapping if c.get("stance") == "personal_estimate"), None)
                if estimate:
                    claim = estimate
                    span = estimate["first"], estimate["last"]
                    pages = [p for p in pages if not ((p["first"], p["last"]) == span
                             and len(p["facts"]) == 1 and (p["facts"][0].get("chain") or {}).get("stance") == "personal_estimate")]
                    displayed.add(span)
                if ends:
                    ends = sorted(set(ends) | {step["at"]-1 for step in sequence if step["at"] > 0})
            speaker = local_claim.get("speaker") or (claim or {}).get("speaker") or args.get("speaker") or "Speaker"
            pages.append(dict(first=first, last=last, sequence=sequence,
                              **({"spoken_estimate":True} if alternatives else {}),
                              **({"sequence_kind":args["sequence_kind"]} if comparison or args.get("sequence_kind") in ("history","process") else {}), facts=[
                dict(attribution=speaker, source=sources["interview"], value=sequence[0]["label"])]))
            continue
        if (whole and editorial.get("episode_examples") and ask["dev"] in ("behind", "diecut")
                and not (ask.get("pics") or args.get("facts") or args.get("claim"))):
            # A word quoted again on a later beat of the same explanation can
            # accent its actual utterance. Keep independent picture treatments intact.
            segment = [i for i,w in enumerate(words) if w["seg"] == words[first]["seg"]]
            pictured = any(w["first"] <= segment[-1] and segment[0] <= w["last"]
                           for w in editorial.get("picture_windows") or [])
            ids = literal_ids(str(args.get("t") or ""), segment)
            if ids and not pictured and len(ids) < len(segment):
                accents.update(ids)
                continue
        if whole and not (args.get("facts") or args.get("claim")) and ask["dev"] in (
                "lamp", "verb", "staircase", "brackets", "typewriter"):
            rows = args.get("lines") or []
            # A timed list is a performance on several cues, even when every label is spoken.
            if (ask["dev"] == "staircase" and len(rows) >= 2 and
                    all(isinstance(row, dict) and type(row.get("at")) is int for row in rows)
                    and len({row["at"] for row in rows}) > 1):
                asks.append(ask)
                continue
            lines = [line.get("t", "") if isinstance(line, dict) else str(line) for line in args.get("lines") or []]
            text = " ".join(lines) or str(args.get("t") or "")
            ids = literal_ids(text, range(first, min(len(words), max(last+1,first+len(text.split())))))
            if not ids and text:
                # A payoff can be addressed on its final stressed word.
                earlier = range(max(0, first-len(text.split())+1), last+1)
                ids = literal_ids(text, earlier)
                if ids and ids[-1] < first:
                    ids = []
            if ids:
                accents.update(ids)
                continue
        local = args.get("claim")
        if not claim and isinstance(local, dict) and all(isinstance(local.get(k), str) and local[k].strip()
                                                         for k in ("speaker", "report")):
            start = editorial.get("episode_start", 0) + words[first]["t0"]
            claim = dict(local, first=first, last=last, start=start)
            claims.append(claim)
        landing = args.get("lands")
        if (isinstance(local,dict) and local.get("stance") == "personal_estimate" and local.get("speaker")
                and type(landing) is int and first <= landing <= last and last == len(words)-1):
            pages.append(dict(first=first,last=last,estimate_landing=landing,facts=[dict(
                attribution=local["speaker"],value=local.get("heard") or local["report"],source=sources["interview"])]))
            if claim:
                displayed.add((claim["first"],claim["last"]))
            continue
        if claim and (args.get("facts") or local or ask["dev"] == "counter"):
            span = claim["first"], claim["last"]
            if span not in displayed:
                candidates = [f for f in args.get("facts") or [] if isinstance(f, dict)]
                selected = next((f for f in candidates if f.get("quote")), candidates[0] if candidates else {})
                item = claim_fact(claim, selected, sources, words)
                exact = (claim.get("stance") in ESTIMATE_LABELS or claim.get("stance") in CONTEXT_LABELS or claim.get("stance") in OPINION_LABELS or (item.get("chain") or {}).get("original")
                         or (selected.get("quote") and item.get("relation")))
                start = claim["first"] if exact else max(first, claim["first"])
                end = claim["last"] if exact or "until" not in ask else min(last, claim["last"])
                pages.append(dict(first=start, last=end, facts=[item]))
                displayed.add(span)
            continue
        for fact in args.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            key = fact.get("source")
            if key and key == sources["interview"].get("url"):
                key = "interview"
            source = sources.get(key) if isinstance(key, str) else None
            value = fact.get("value", fact.get("statement"))
            if source and isinstance(value, (str, int, float)) and str(value).strip() and fact.get("relation"):
                facts.append(dict(fact, source=source, value=value))
        progression = facts[0].get("progression") if len(facts) == 1 else None
        if ask["dev"] == "counter" and isinstance(progression, dict) and all(
                type(progression.get(k)) in (int, float) and math.isfinite(progression[k]) for k in ("from", "to")):
            fact = facts[0]
            label = " · ".join(str(text) for text in (fact.get("attribution"), fact.get("qualifier"),
                                                      fact["relation"], fact["source"].get("publisher")) if text)
            asks.append(dict(ask, args=dict(args, facts=facts, label=label,
                                            **{k: progression[k] for k in ("from", "to")})))
            continue
        if "until" not in ask and (facts or args.get("facts") or ask["dev"] == "counter"):
            last = max(i for i, w in enumerate(words) if w["seg"] == words[first]["seg"])
        if not facts and (args.get("facts") or ask["dev"] == "counter"):
            # An unbound number can still land as the actual spoken phrase.
            if whole:
                accents.update(range(first,last+1))
                continue
            quote = " ".join(w["w"] for w in words[first:last+1])
            facts = [dict(source=sources["interview"], value=quote)]
        if facts:
            # Synchronize an interview qualification to its literal words inside the writer's chosen clause.
            if len(facts) == 1 and facts[0]["source"] == sources["interview"] and facts[0].get("qualifier"):
                qualifier = str(facts[0]["qualifier"]).casefold().strip(string.punctuation).split()
                spoken = [w["w"].casefold().strip(string.punctuation) for w in words[first:last+1]]
                offset = next((i for i in range(len(spoken)) if spoken[i:i+len(qualifier)] == qualifier), 0)
                first += offset
            pages.append(dict(first=first, last=last, facts=facts, viewer_line=str(args.get("viewer_line") or "").strip()))
        else:
            asks.append(ask)
    # A qualification belongs on screen even when the Director uses a picture on that beat.
    for claim in claims:
        if (claim.get("stance") in CONTEXT_LABELS or claim.get("stance") in ESTIMATE_LABELS or claim.get("stance") in OPINION_LABELS) and (claim["first"], claim["last"]) not in displayed:
            pages.append(dict(first=claim["first"], last=claim["last"], facts=[claim_fact(claim, {}, sources, words)]))
    callback = editorial.get("callback")
    if callback:
        a,b = callback["first"],callback["last"]
        passage = editorial["story"]["callback"]
        source = {k:passage[k] for k in ("url","publisher","excerpt")}
        if passage.get("context"):
            source["context_excerpt"] = passage["context"]["excerpt"]
        for page in pages:
            if page.get("sequence") and page["sequence"][-1]["at"] <= b and a <= page["last"]:
                steps = page["sequence"]
                page.update(sequence=[*steps[:-1], dict(steps[-1], label=callback["question"])], last=max(page["last"],b))
                page["facts"][0]["callback_source"] = source
                break
        else:
            speaker = next((c["speaker"] for c in claims if c["first"] <= a <= c["last"]), "")
            pages.append(dict(first=a,last=b,sequence=[dict(at=a,label=callback["question"])],
                              facts=[dict(attribution=speaker,source=sources["interview"],
                                          callback_source=source,value=callback["question"])]))
        # A definition on the callback belongs with the source view explaining that term.
        # Carry it in the same scenario instead of laying a second plate across its question.
        scenario = next(p for p in pages if p["facts"][0].get("callback_source"))
        if callback.get("action"):
            scenario["callback_action"] = callback["action"]
        for definition in list(pages):
            facts = definition["facts"]
            if (len(facts) == 1 and (facts[0].get("chain") or {}).get("stance") == "definition"
                    and a <= definition["first"] <= definition["last"] <= b):
                scenario.setdefault("explanation", facts[0]["value"])
                pages.remove(definition)
    # Research can correct the referent after a direction was scored. Replace the
    # old factual cue on those words, retaining separate interview and timeline cues.
    for window in editorial.get("picture_windows") or []:
        example = window.get("example")
        if not isinstance(example, dict):
            continue
        source = sources.get(example.get("source")) if isinstance(example.get("source"), str) else None
        statement = example.get("statement")
        if not source or not isinstance(statement, str) or not statement.strip():
            continue
        first, last = window["first"], window["last"]
        retained = []
        spans = [(first, last)]
        for page in pages:
            if page["last"] < first or last < page["first"]:
                retained.append(page)
                continue
            if page.get("sequence") or any(f.get("chain") or f.get("perspective") for f in page["facts"]):
                spans = [(a,b) for lo,hi in spans for a,b in
                         ((lo,min(hi,page["first"]-1)),(max(lo,page["last"]+1),hi)) if a <= b]
                retained.append(page)
                continue
            if page["first"] < first:
                retained.append(dict(page, last=first-1))
            if last < page["last"]:
                retained.append(dict(page, first=last+1))
        pages = retained
        fact = dict(value=statement, statement=statement, relation="Example", source=source, example=True)
        if isinstance(example.get("credit"), str) and example["credit"].strip():
            fact["credit"] = example["credit"].strip()
        if example.get("figure_url") and example.get("visible_statement"):
            fact.update(figure_url=example["figure_url"], visible_statement=example["visible_statement"])
        pages.extend(dict(first=a, last=b, facts=[fact]) for a,b in spans)
    for example in editorial.get("episode_examples") or []:
        a, b = example["graphic_first"], example["graphic_last"]
        # The episode establishes the premise. The label is the editor's hypothetical illustration.
        fact = dict(source=sources["interview"], value=example["label"], relation="Illustrative example")
        pages.append(dict(first=a, last=b, episode_example=True, facts=[fact]))
    for comparison in editorial.get("comparisons") or []:
        instrument = comparison.get("instrument")
        if instrument and not any(a["dev"] == "analogy_dial" and comparison["first"] <= a["at"]["w"] <= comparison["last"]
                                  for a in asks):
            # The story reader already directed this change. Preserve its word clock when
            # the display writer leaves its illustration to the compositor.
            def addr(index):
                return dict(w=index, word=words[index]["w"], seg=words[index]["seg"])
            asks.append(dict(dev="analogy_dial", at=addr(instrument["enter"]), until=addr(instrument["leave"]),
                             args=dict(change=instrument["change"], trend=instrument["trend"])))
    pages = testimony_pages(pages, dict(editorial,borrowed_estimates=list(estimates.values()),handoffs=list(routes.values())))
    pages.sort(key=lambda page: page["first"])
    example = wf1_principle.bind(direction.get("principle_example"), sources, words)
    return dict(direction, model_phases=model_phases(direction, sources, words), principle_example=example, asks=asks, display=display, caption_ends=ends, caption_accents=sorted(accents), claims=claims, display_facts=pages,
                arc=[m for m in direction["arc"] if not any(p["first"] <= m["w"] <= p["last"] for p in pages)])


def estimate_credit(fact):
    """Short author-year references. Keep full publication titles and locators in the sources."""
    labels = []
    for source in fact.get("supporting_sources") or []:
        author = source.get("author") or source.get("publisher")
        date = str(source.get("publication_date") or "").replace("-"," ").split()
        year = next((part for part in date if len(part)==4 and part.isdecimal()), None)
        if not author or not year:
            return fact["attribution"]
        label = f"{author} ({year})"
        if label not in labels:
            labels.append(label)
    return "\n".join(labels) or fact["attribution"]


def testimony_pages(pages, editorial):
    interview = editorial["sources"]["interview"]
    scopes = [r for r in editorial.get("borrowed_estimates") or [] if r.get("sources")]
    routes = editorial.get("handoffs") or []
    if not (scopes or routes):
        return pages
    def superseded(page):
        for treatment in [*scopes,*routes]:
            if treatment["first"] <= page["first"] <= treatment["last"]:
                if all(same_recording(f.get("source",{}).get("url"),interview.get("url"))
                       for f in page["facts"]):
                    return True
        return False
    result = []
    for page in pages:
        if superseded(page):
            continue
        if any(r["first"] <= page["first"] and page["last"] <= r["last"] for r in routes):
            if len(page["facts"]) == 1 and page["facts"][0].get("example"):
                page = dict(page,route_example=True)
        result.append(page)
    for r in scopes:
        for a,b,title,text,source in (
                (r["first"],r["application"]-1,r["credit"],r["statement"],r["sources"][0]),
                (r["application"],r["last"],r["speaker"],r["application_label"],interview)):
            result.append(dict(first=a,last=b,scope_cue=True,facts=[dict(
                attribution=title,value=text,source=source,
                supporting_sources=r["sources"] if a==r["first"] else [interview])]))
    for r in routes:
        result.append(dict(first=r["first"],last=r["last"],sequence_kind="handoff",sequence=r["steps"],
                           facts=[dict(value=r["title"],source=interview,attribution=r["speaker"])]))
    return result


def figure_readings(span, fact, pictures):
    """Switch to the pictured wording only while the composed card shows that publisher figure."""
    start, end = span
    page = fact["source"]["url"].split("#")[0]
    matches = [(max(start,a), min(end,b)) for picture in pictures for a,b in [picture["f"]]
               if a < end and start < b and (picture.get("document") or {}).get("url") == fact["figure_url"]
               and (picture.get("source") or "").split("#")[0] == page]
    edges = sorted({start, end, *(f for pair in matches for f in pair)})
    for a,b in zip(edges,edges[1:]):
        visible = any(lo <= a < hi for lo,hi in matches)
        yield [a,b], fact["visible_statement"] if visible else fact["statement"]


def comparison_pages(pages, comparison, words):
    """Give the premise and its spoken outcome separate clocks on the synchronous take."""
    first, last = comparison["first"], comparison["last"]
    reveal = comparison.get("reveal")
    # Older scores can carry the exact closing image in both lines. Use that word's
    # address when available, retaining the existing layout for an unaddressed comparison.
    if reveal is None and words[last]["w"].strip(string.punctuation).casefold() == (
            comparison["consequence"].split()[-1].strip(string.punctuation).casefold()):
        reveal = last
    competing = [p for p in pages if p["first"] <= last and first <= p["last"]]
    def independent(page):
        if page.get("sequence"):
            return True
        for fact in page["facts"]:
            chain = fact.get("chain") or {}
            if (fact.get("perspective") or fact.get("record") or fact.get("excerpt") or
                    (chain and (reveal is None or chain.get("original") or
                                not same_recording(fact.get("source",{}).get("url"),chain.get("interview"))))):
                return True
        return False
    protected = [p for p in competing if independent(p)]
    if reveal is None and protected:
        return pages
    result = [p for p in pages if p not in competing or p in protected]
    for p in competing:
        if p in protected:
            continue
        if p["first"] < first:
            result.append(dict(p,last=first-1))
        if p["last"] > last:
            result.append(dict(p,first=last+1))
    fact = dict(relation=comparison["premise"], value=comparison["consequence"], source={})
    if reveal is None:
        return result + [dict(first=first,last=last,analogy=True,facts=[fact])]
    application = comparison.get("application")
    steps = {step["at"]: step for step in comparison.get("steps") or []}
    if application:
        # The premise summarizes the entire case. Its individual actions own the
        # screen on their speech, before the consequence and the speaker’s return.
        steps = {at: step for at, step in steps.items() if at < reveal}
        steps[reveal] = dict(label=comparison["consequence"], reveal=True)
        steps[application["at"]] = dict(label=application["label"])
    else:
        steps.setdefault(first, dict(label=comparison["premise"]))
        preceding = max((at for at in steps if at <= reveal), default=first)
        if steps[preceding]["label"] != comparison["consequence"]:
            steps.setdefault(reveal, dict(label="", reveal=True))
    cues = sorted(steps)
    history = []
    for start,end in zip(cues,cues[1:] + [last+1]):
        label = steps[start]["label"]
        if label and (not history or label != history[-1]):
            history.append(label)
        spans = [(start,end)]
        for p in protected:
            spans = [(a,b) for lo,hi in spans for a,b in
                     ((lo,min(hi,p["first"])),(max(lo,p["last"]+1),hi)) if a < b]
        labels = history[-2:]
        annotation = {}
        if application:
            applied = start >= application["at"]
            annotation = dict(heading=application["speaker"] + ("’s view" if applied else "’s example"),
                              case_phase="application" if applied else "example")
            if start == reveal:
                labels = history[-3:]
            elif applied:
                labels = [label]
        result.extend(dict(first=a,last=b-1,analogy=True,analogy_step=steps[start],
                           comparison_labels=labels, **annotation, facts=[fact]) for a,b in spans)
    return result


def episode_pages(pages, coverage, words):
    """Give local illustrations reading time in their sentence; release a gloss with its term."""
    examples = {(e["graphic_first"], e["graphic_last"]): e for e in coverage.get("episode_examples") or []}
    contextual = {r["start"]:r for r in coverage.get("contextual_definitions") or []
                  if isinstance(r, dict) and type(r.get("start")) in (int, float)}
    result = []
    terms = set()
    for page in sorted(pages, key=lambda row: row["first"]):
        first, last = page["first"], page["last"]
        facts = []
        for fact in page["facts"]:
            view = fact.get("perspective") or {}
            if (view.get("stance") == "recollection" and not any(
                    fact.get(k) for k in ("record","program_entry","excerpt"))):
                # A research summary can mix eyewitness testimony and an editor's search notes.
                # The uncorroborated account still has its primary source: the person on camera.
                fact = dict(chain=view, attribution=view["speaker"], value=view["report"],
                            source=dict(url=view.get("interview")))
            facts.append(fact)
        page = dict(page, facts=facts)
        example = examples.get((first, last)) if page.get("episode_example") else None
        claim = page["facts"][0].get("chain") or {}
        contextual_def = contextual.get(claim.get("start"), {})
        term = claim.get("term") or contextual_def.get("term")
        if term and claim.get("stance") == "definition" and contextual_def.get("gloss"):
            fact = dict(facts[0],chain=dict(claim,term=term),value=contextual_def["gloss"])
            page = dict(page,facts=[fact,*facts[1:]])
        if claim.get("stance") == "definition" and term:
            if term in terms:
                continue
            terms.add(term)
        if claim.get("stance") == "framework" and type(claim.get("context_start")) in (int,float):
            # The earlier topic introduces the excerpt. A checked qualification on the
            # same words takes the next seat instead of stacking two reading plates.
            following = [p["first"] for p in pages if first <= p["first"] <= last
                         and any(f.get("perspective") or f.get("excerpt") for f in p["facts"])]
            stop = min(following)-1 if following else last
            if stop < first:
                continue
            page = dict(page, first=0 if not any(p["last"] < first for p in pages) else first,
                        last=stop, context_cue=True)
        if claim.get("stance") == "framework":
            # The example enacts this speaker's rule. Move from the premise into the first action.
            actions = [p["first"] for p in pages if p.get("sequence_kind") == "process"
                       and page["first"] <= p["first"] <= page["last"]
                       and p["facts"][0].get("attribution") == claim.get("speaker")]
            if actions:
                if min(actions) == page["first"]:
                    continue
                page = dict(page, last=min(actions)-1, action_intro=True)
        elif example:
            # Keep the illustration through its explanation, inside the planner's local span.
            while last < example["last"] and words[last+1]["seg"] == words[last]["seg"]:
                last += 1
            page = dict(page, last=last)
        elif claim.get("stance") == "definition" and claim.get("start") in contextual:
            seg = words[first]["seg"]
            ids = [i for i,w in enumerate(words) if w["seg"] == seg]
            page = dict(page, first=first if term else ids[0], last=ids[-1], gloss=True)
        result.append(page)
    # Word-addressed interview beats use the same live byline as an observation.
    # They replace a competing gloss inside their own interval only.
    for cue in coverage.get("interview_cues") or []:
        if not isinstance(cue,dict) or not all(isinstance(cue.get(k),str) and cue[k].strip()
                                               for k in ("speaker","label","report")):
            continue
        a,b = cue.get("first"),cue.get("last")
        if type(a) != int or type(b) != int or not 0 <= a <= b < len(words):
            continue
        retained = []
        protected = []
        witness = cue.get("stance") if cue.get("stance") in ("recollection","characterization") else None
        for page in result:
            lo,hi = page["first"],page["last"]
            if hi < a or b < lo:
                retained.append(page)
                continue
            if page.get("sequence") or page.get("estimate_landing") is not None or any(f.get("record") or f.get("excerpt") or f.get("program_entry")
                                          for f in page["facts"]):
                retained.append(page)
                protected.append((lo,hi))
                continue
            chain = page["facts"][0].get("chain") or {}
            if chain.get("speaker") == cue["speaker"] and chain.get("stance") in ("recollection","characterization"):
                witness = chain["stance"]
            if lo < a:
                retained.append(dict(page,last=a-1))
            if b < hi:
                retained.append(dict(page,first=b+1))
        edges = sorted({a,b+1,*(max(a,x) for x,_ in protected),*(min(b+1,y+1) for _,y in protected)})
        result = retained
        for x,y in zip(edges,edges[1:]):
            if any(lo <= x <= hi for lo,hi in protected):
                continue
            chain = dict(cue, first=x, last=y-1, stance="interview_cue")
            if witness:
                chain["witness"] = witness
                if isinstance(cue.get("referent"), str) and cue["referent"].strip():
                    chain["label"] = cue["referent"].strip()
            heard = " ".join(word["w"] for word in words[x:y] if word.get("w"))
            if heard:
                chain["heard"] = heard
            fact = dict(attribution=cue["speaker"],value=cue["report"],chain=chain,
                        source=dict(url=cue.get("interview")))
            result.append(dict(first=x,last=y-1,facts=[fact]))
    # An estimate and its spoken qualification share one display. A stale claim clock
    # can put the same quoted alternatives several seconds after their actual utterance.
    for page in [p for p in result if p.get("spoken_estimate")]:
        speaker = page["facts"][0]["attribution"]
        def tokens(text):
            return [w.casefold().strip(string.punctuation) for w in text.split()]
        quoted = tokens(" ".join(step["label"] for step in page["sequence"]))
        for other in list(result):
            fact = other["facts"][0]
            chain = fact.get("chain") or {}
            if chain.get("speaker") != speaker or len(other["facts"]) != 1:
                continue
            if chain.get("stance") == "personal_estimate" and tokens(chain.get("heard","")) == quoted:
                result.remove(other)
            elif (chain.get("stance") == "interview_cue" and other["first"] == page["last"]+1 and
                  any(c.get("first") == page["first"] and c.get("last") == other["last"]
                      and c.get("speaker") == speaker for c in coverage.get("interview_cues",[]))):
                page["last"] = other["last"]
                result.remove(other)
    return sorted(result,key=lambda row:row["first"])


def lines(facts):
    credits = []
    for fact in facts:
        attribution = fact.get("attribution")
        relation = fact.get("relation")
        value = str(fact["value"])
        if fact.get("qualifier"):
            value = f"{fact['qualifier']} {value}"
        if attribution or relation:
            yield str(attribution or relation), False
        yield value, True
        if attribution and relation:
            yield str(relation), False
        publisher = fact["source"].get("publisher")
        if publisher and publisher not in credits:
            credits.append(publisher)
    if credits:
        yield " · ".join(credits), None


def typeset(facts, ruler, max_width, case):
    """Wrap supporting copy on measured glyph widths. Move the complete number as one line when it fits."""
    def wrap(tokens, cap):
        phrase = " ".join(tokens)
        px = ruler.fit_px("dsp", phrase, max_width, cap)
        if px >= min(54, cap) or len(tokens) < 2:
            return [phrase]
        # Balance the two lines by their shaped widths, so a short final word never becomes a giant line.
        def longest_line(i):
            return max(ruler.width("dsp", " ".join(part)) for part in (tokens[:i], tokens[i:]))
        mid = min(range(1, len(tokens)), key=longest_line)
        return wrap(tokens[:mid], cap) + wrap(tokens[mid:], cap)

    rows = []
    for phrase, emphasis in lines(facts):
        cap = 44 if emphasis is None else (94 if emphasis else 58)
        parts = wrap(case(phrase).split(), cap)
        px = min(ruler.fit_px("dsp", part, max_width, cap) for part in parts)
        rows.extend((part, px, emphasis) for part in parts)
    return rows


def source_statement(fact, interview):
    """Retained citation cards already carry the source reader's indirect account."""
    if fact.get("chain"):
        return None
    if isinstance(fact.get("statement"), str) and fact["statement"].strip():
        return fact["statement"].strip()
    source = fact["source"]
    if interview and fact.get("attribution") and source.get("url") and source["url"] != interview:
        readings = source.get("supports") or []
        if readings and isinstance(readings[0], str):
            # Preserve the source reader's prose, including abbreviations and qualifications.
            return readings[0].strip()
    return None


def figure_cue(plan, caption, clock, index, frame, palette):
    """Land the directed figure inside the complete spoken phrase, on its actual word."""
    from scene.palette.kit import punch
    from film import W
    tokens = caption["t"].split()
    words = [word["w"] for word in clock.words]
    # Locate the particular spoken occurrence, including repeated words in the cue.
    first = next((i for i in range(max(0,index-len(tokens)+1),index+1)
                  if words[i:i+len(tokens)] == tokens), None)
    if first is None:
        return
    pos = index-first
    before, number, after = " ".join(tokens[:pos]), tokens[pos], " ".join(tokens[pos+1:])
    start,end = caption["f"]
    if before and start < frame:
        caption.update(t=before, f=[start,frame], x=.5-plan.R.width(caption["v"], before)/(2*W))
    else:
        plan.items.remove(caption)
    span = [frame,end]
    group = f"spoken-figure-{frame}"
    for text,base,voice,size in ((before,.57,"cap",58), (number,.668,"dsp",152), (after,.719,"cap",58)):
        if not text:
            continue
        name = f"{group}-{voice}-{base}"
        px = plan.R.fit_px(voice, text, .78*W, size)
        plan.voices[name] = dict(plan.voices[voice],px=px,track=0)
        row = plan.word(span,text,name,.5,base,palette["accent"] if voice=="dsp" else palette["ink"],
                        align="c", role="graphic", note=False, z=8, fact_type=True, text_group=group)
        if voice == "dsp":
            row["s"] = punch(end-frame)


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
