"""Read selected examples alongside the interviewer's recorded population and question."""
import hashlib
import json
import subprocess
from pathlib import Path


PROMPT = """Read only the supplied spoken passage and selected source frames. If the later
question does not ask about routine operational work in the same population as the reported estimate, return {}.
Distinguish independent illustrative queries from a before/after of one query. For each
checked picture that explains a different use, return {id,kind:"example"|"illustration",
title,body,crop:[left,top,right,bottom] in fractional 0..1 coordinates}. For an example, title names the topic in 2–4
words, body gives the actual useful result in up to 8 words; on a sourced answer name
the actual answer and carry its supporting publisher in source_label. Keep the result
and citation together in the document crop. Use tight readable detail on real moving
search results. Label a new query as its own example; successive frames of ONE query
share a title and a very short stable result. In search listings identify a useful link and its
actual publisher. Do not present incidental commercial prices as independent facts. Keep text
short enough for its frame duration at 30 fps. Use sentence case. If a coding action is incidental or tiny, use an illustration instead:
a familiar everyday task independent of the incidental demo (e.g. sort names, sum prices),
its title in two words and at most 23 characters of legible illustrative code
as its body. Do not attribute this invented example to the archive.
For the later hypothetical routine operation give an illustrative everyday workflow in
three compact steps on one line joined by arrows (e.g. Receive order → Check stock → Ship). Use the word index where the
speaker starts describing the operational task as workflow_at, and the first word of
the scoped verdict as outcome_at. That verdict will be spoken over the witness; never
author a deployment claim. Return JSON {pictures:[...],workflow,workflow_at,outcome_at}.
"""


def account(direction):
    claims = direction.get("claims") or []
    for estimate in claims:
        if (estimate.get("stance") != "discussed_estimate" or not estimate.get("interview_only")
                or not all(estimate.get(k) for k in ("subject", "speaker", "event"))):
            continue
        for question in claims:
            if (question is estimate or not question.get("interview_only")
                    or question.get("speaker") != estimate["speaker"] or question.get("event") != estimate["event"]
                    or question.get("first", -1) <= estimate.get("last", -1)):
                continue
            for recall in claims:
                if (recall.get("stance") == "recollection" and recall.get("speaker") == estimate["speaker"]
                        and recall["first"] <= question["last"] and question["first"] <= recall["last"]):
                    return dict(estimate=estimate, question=question, recall=recall)
    return {}


def sample(rec, work, ffmpeg):
    """The exact frame read for a named result, kept for its magnified source excerpt."""
    from placement_check import file_digest
    from wf1_source_frame import write_cache
    lo, hi = rec["src"]
    time = (lo + hi) / 2
    key = hashlib.sha256(f"{file_digest(rec['path'])}@{time}".encode()).hexdigest()[:24]
    path = Path(work, "result-" + key + ".jpg")
    if not path.exists():
        data = subprocess.check_output([ffmpeg, "-v", "error", "-ss", str(time), "-i", rec["path"],
            "-frames:v", "1", "-vf", "scale=1000:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"], timeout=30)
        write_cache(path, data)
    return str(path)


def read(context, words, rows, work, ffmpeg, llm):
    from PIL import Image
    import httpx
    import openai_budget
    from placement_check import file_digest
    from wf1_documents import image_part, valid_detail
    from wf1_source_frame import write_cache

    records = []
    for i, (a, b, kind, name, rec) in enumerate(rows):
        records.append(dict(id=i, name=name, f=[a,b], kind=kind, src=rec.get("src"),
                            digest=file_digest(rec["path"]), action=rec.get("action_caption"),
                            literal=rec.get("attribution"), source=rec.get("source") or rec.get("page")))
    data = dict(account=context, words=[w["w"] for w in words], pictures=records)
    key = hashlib.sha256(json.dumps([PROMPT,data], sort_keys=True).encode()).hexdigest()
    path = Path(work, "field-report-" + key[:24] + ".json")
    spend = []
    try:
        plan = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        plan = None
    fresh = plan is None
    if fresh:
        parts = [dict(type="text", text=json.dumps(data))]
        try:
            for i, (_, _, kind, _, rec) in enumerate(rows):
                source = sample(rec, work, ffmpeg) if kind == "clip" else rec["path"]
                with Image.open(source) as im:
                    parts.extend([dict(type="text",text=f"Selected picture {i}"),image_part(im)])
            raw, usage = llm.ask(PROMPT, parts, effort="low", max_tokens=1500)
            spend.append(usage)
            plan = llm.json_block(raw)
        except openai_budget.OverBudget:
            raise
        except (ValueError, OSError, RuntimeError, httpx.HTTPError, subprocess.SubprocessError):
            return {}, spend
        if plan == {}:
            write_cache(path, b"{}")
    question, recall = context["question"], context["recall"]
    if not isinstance(plan, dict):
        return {}, spend
    at, end = plan.get("workflow_at"), plan.get("outcome_at")
    if not (type(at) is int and type(end) is int and question["first"] <= at < end <= recall["last"] < len(words)
            and isinstance(plan.get("workflow"), str) and plan["workflow"].strip()):
        return {}, spend
    pictures = {}
    for p in plan["pictures"] if isinstance(plan.get("pictures"),list) else ():
        if not (isinstance(p, dict) and type(p.get("id")) is int and 0 <= p["id"] < len(rows)
                and p.get("kind") in ("example","illustration")
                and all(isinstance(p.get(k),str) and p[k].strip() for k in ("title","body"))):
            continue
        pictures[p["id"]] = {k:p[k] for k in ("kind","title","body")}
        if valid_detail(p.get("crop")):
            pictures[p["id"]]["crop"] = p["crop"]
        if isinstance(p.get("source_label"), str):
            pictures[p["id"]]["source_label"] = p["source_label"]
    if fresh:
        write_cache(path, json.dumps(plan).encode())
    return dict(pictures=pictures, workflow=plan["workflow"], workflow_at=at, outcome_at=end,
                scope=context, decision=key), spend
