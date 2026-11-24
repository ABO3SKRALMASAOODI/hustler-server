"""A speaker's own question can illustrate the difference between a record and a reading of it."""

PROMPT = """When a speaker acts out questions and proposes checking a record to resolve conflicting
recollections, direct a record_example. Use the numbered interview words, the speaker's exercise
and the actual turn in the argument. first/last enclose the entire anecdote including setup and close.
question is a pair of inclusive word IDs for ONE short complete question the speaker actually voices
early in the reenactment. Its exact interview wording will appear as the example record, twice.
inference is the word interval asking about a private interpretation. memory is a later short clause
about the fallibility of recollection, ideally within the speaker's extended qualification. Return to
the quoted line at the first words about establishing what was said (return_at), accent it where
the speaker lands that idea (accent), then release on the way back to the live close (release, first
word back on camera alone). Attribute the hypothesis to the speaker. Include speaker, inference_label
and memory_label: short open questions or noun phrases, never invented answers, private records or
admissions. Keep the live speaker and pauses through the setup, including any passing cultural analogy.
A separate product account can establish only the capability its primary source documents. A callback
to a different demonstration cannot document this example. Leave this whole anecdote on the interview
and its cited line. Omit record_example for other stories."""


def reading(row, words):
    if not isinstance(row, dict):
        return None
    def index(i):
        return type(i) is int and 0 <= i < len(words)
    for field in ("question", "inference", "memory"):
        span = row.get(field)
        if not isinstance(span, list) or len(span) != 2 or not all(index(i) for i in span) or span[1] < span[0]:
            return None
    if not all(index(row.get(key)) for key in ("first", "return_at", "accent", "release", "last")):
        return None
    if not (row["first"] <= row["question"][0] <= row["question"][1] < row["inference"][0]
            and row["inference"][1] < row["memory"][0] <= row["memory"][1] < row["return_at"]
            <= row["accent"] < row["release"] <= row["last"]):
        return None
    if all(isinstance(row.get(k), str) and row[k].strip() for k in ("speaker", "inference_label", "memory_label")):
        return row


def outside(rows, record):
    """Retain neighboring editorial decisions on their original word IDs."""
    if not record:
        return rows
    return [r for r in rows if r["last"] < record["first"] or r["first"] > record["last"]]


def source_claims(claims, coverage):
    """The interview is the primary source for claims inside an enacted account."""
    record = coverage.get("record_example")
    if not record:
        return claims
    origin = coverage["story"]["episode_start"]
    words = coverage["words"]
    lo = origin + words[record["first"]]["start"]
    hi = origin + words[record["last"]]["end"]
    return [dict(c, interview_only=True) if isinstance(c.get("start"), (int,float))
            and isinstance(c.get("end"), (int,float)) and lo <= (c["start"]+c["end"])/2 <= hi
            else c for c in claims]


def pages(existing, record, words, source, origin, beats=(), caption_ends=()):
    if not record:
        return existing
    result = []
    for page in existing:
        if outside([page], record):
            result.append(page)
            continue
        for a, b in ((page["first"], record["first"]-1), (record["last"]+1, page["last"])):
            if a <= b:
                part = dict(page, first=a, last=b)
                if part.get("sequence"):
                    part["sequence"] = [s for s in part["sequence"] if a <= s["at"] <= b]
                    if not part["sequence"]:
                        continue
                result.append(part)
    a, b = record["question"]
    quote = " ".join(w["w"] for w in words[a:b+1])
    fact = dict(source=source, attribution=record["speaker"], value=quote,
                time_s=round(origin + words[a]["start"], 3), word_ids=[a,b])
    for role, lo, hi in (("wording",a,record["inference"][0]-1), ("inference",*record["inference"]),
                         ("memory",*record["memory"]), ("return",record["return_at"],record["release"]-1)):
        evidence = fact if role in ("wording","return") else dict(
            source=source, attribution=record["speaker"], value=record[role+"_label"],
            time_s=round(origin + words[lo]["start"], 3), word_ids=[lo,hi])
        result.append(dict(first=lo,last=hi,record_role=role,facts=[evidence]))
    # A directed contrast immediately before the memory beat is the speaker's
    # qualification. Give the whole existing caption its own reading interval.
    last = record["memory"][0]-1
    if last in caption_ends and any(b.get("role") == "contrast" and b.get("w") == last for b in beats):
        first = 1+max((i for i in caption_ends if i < last), default=-1)
        if first > record["inference"][1]:
            phrase = " ".join(w["w"] for w in words[first:last+1])
            result.append(dict(first=first,last=last,record_role="qualification",facts=[dict(
                source=source,attribution=record["speaker"],value=phrase,
                time_s=round(origin+words[first]["start"],3),word_ids=[first,last])]))
    return sorted(result,key=lambda p:p["first"])
