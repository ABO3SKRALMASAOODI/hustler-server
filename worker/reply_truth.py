"""A reply never says "done" for an ask the film does not show (lane EDIT-SWITCH, 14 Nov 2026).

On the Gauntlet's replays of real requests (.valmera/week-1112/lanes/EDIT-MODEL/table.json),
gpt-5.5 told two customers their edit was done when the film said otherwise: p5518 "the watermark
is removed" with the logo still on her face and "VisionStory" still on her clothes, p5533 "the
background is dark, the dancers unchanged" over a box that dimmed the dancers. Haiku's reply on
p5533 claimed the backdrop dark like the curtain as well. Customers read the reply first and the
film second; a false "done" is the worst reply there is.

The product's own reply checks see the EDL (what was written), not the picture, and the ask
ledger's film check (score.asks) runs only where typed_asks is on. So before the reply is posted,
one call on the turn's own model and client looks at the rendered film's screening sheets (made by
the render itself) beside the customer's request and the drafted reply, and lists the reply's
claims of a change made or a thing kept that the frames clearly contradict, each with the reply's
exact words and the same point said truthfully in the reply's language. Those words are replaced
in the reply; nothing else in it changes. The model reads and judges; the code only swaps the
quoted text it was given. Fail-open: no frames, no answer or any error posts the draft as it was.
"""

import json
import time

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["claims"],
    "properties": {"claims": {"type": "array", "maxItems": 12, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["quote", "status", "seen", "honest"],
        "properties": {
            "quote": {"type": "string", "description": (
                "The reply's own words for this claim, copied exactly (one sentence or clause).")},
            "status": {"type": "string", "enum": ["shown", "contradicted", "cannot_tell"]},
            "seen": {"type": "string", "description": "What the frames show, with sheet and tile."},
            "honest": {"type": "string", "description": (
                "For contradicted: the same point said truthfully, in the reply's language and "
                "voice, plain, no apology. Otherwise empty.")},
        }}}},
}

SYSTEM = """You check a video editor's reply against the film it delivered, before the customer
reads it. You get the customer's request, the drafted reply and screening sheets of the rendered
film (tiles labelled with their output time).

List each claim in the reply that something the customer asked for was done (added, removed,
changed, fixed, kept untouched, left as it was). For each, look at the frames:
- shown: the frames show it, or are consistent with it;
- contradicted: the frames clearly show the opposite: what the reply says is removed or hidden is
  still visible; what it says is kept untouched (a person, the floor, the timing of a shot) is
  visibly dimmed, covered, cut or damaged; what it says was added appears in no frame where it
  should; what it says is clean shows a smear or a patch;
- cannot_tell: the frames cannot show it (sound, music, pacing between frames, an exact second).
Never mark contradicted from a doubt, a matter of taste or something the reply already admits.
Statements that something was NOT done, questions and offers are not claims; leave them out.
For each contradicted claim write `honest`: the same point told truthfully from what the film
shows (what is there now, what still shows, and where), in the reply's language, in the editor's
own plain voice. Use the record_reply_truth tool once."""


def _preview(ctx):
    preview = getattr(ctx, "last_preview", None) or {}
    try:
        version = ctx.latest_edl().get("version")
    except Exception:  # noqa: BLE001
        version = None
    if preview and str(preview.get("edl_version")) == str(version):
        return preview
    if version is None:
        return None
    from score import asks
    return asks.stored_preview(ctx, version)


def check(ctx, client, model, request_text, draft, limit=3):
    """[{quote, status, seen, honest}] for the draft against the film, or None when it cannot be
    checked here. Records the call (llm.record) so it is metered with the request."""
    import llm
    from score import asks
    preview = _preview(ctx)
    if not preview:
        return None
    pages = asks.frame_pages(ctx, preview, limit=limit)
    if not pages:
        return None
    content = [{"type": "text", "text": "\n".join([
        "The customer's request:\n" + json.dumps(str(request_text or "")[:3000], ensure_ascii=False),
        "The drafted reply:\n" + json.dumps(str(draft or "")[:3000], ensure_ascii=False)])}]
    for local, label in pages:
        content.append({"type": "text", "text": label})
        content.append(llm.image_part(local))
    tool = {"type": "function", "function": {
        "name": "record_reply_truth", "description": "Record the checked claims.",
        "parameters": SCHEMA, "strict": True}}
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]
    t0 = time.monotonic()
    response = llm.create_with_dialect(
        client, model, messages, max_tokens=3000, tools=[tool],
        tool_choice={"type": "function", "function": {"name": "record_reply_truth"}})
    message = response.choices[0].message
    calls = list(message.tool_calls or [])
    llm.record("reply_truth", {"model": model, "pages": [label for _p, label in pages]},
               {"tool_calls": [{"name": c.function.name, "arguments": c.function.arguments}
                               for c in calls], "s": round(time.monotonic() - t0, 2)},
               getattr(response, "usage", None))
    if len(calls) != 1 or calls[0].function.name != "record_reply_truth":
        return None
    value = json.loads(calls[0].function.arguments or "{}")
    rows = value.get("claims") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        return None
    return [r for r in rows if isinstance(r, dict) and r.get("status") in
            ("shown", "contradicted", "cannot_tell")]


def corrected(draft, rows):
    """(reply, swapped): every contradicted claim's quoted words replaced by its honest line; an
    honest line whose quote is not verbatim in the draft is added after the first paragraph."""
    text = str(draft or "")
    swapped, added = [], []
    for r in rows or []:
        if r.get("status") != "contradicted":
            continue
        quote = str(r.get("quote") or "").strip()
        honest = str(r.get("honest") or "").strip()
        if not honest:
            continue
        if quote and quote in text:
            text = text.replace(quote, honest, 1)
            swapped.append(quote)
        elif honest not in text:
            added.append(honest)
            swapped.append(quote or honest)
    if added:
        head, sep, tail = text.partition("\n\n")
        text = head.rstrip() + " " + " ".join(added) + (sep + tail if sep else "")
    return text, swapped


def enforce(ctx, client, model, request_text, draft, honesty=None):
    """The draft with its contradicted claims told truthfully. Never raises."""
    if not draft or not str(draft).strip() or client is None or not model:
        return draft
    try:
        rows = check(ctx, client, model, request_text, draft)
    except Exception as exc:  # noqa: BLE001 - an unchecked reply goes out as drafted
        print(f"[reply-truth] check skipped: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
        return draft
    if not rows:
        return draft
    text, swapped = corrected(draft, rows)
    if isinstance(honesty, dict):
        honesty["reply_truth"] = {
            "claims": len(rows),
            "contradicted": [{"quote": str(r.get("quote") or "")[:160],
                              "seen": str(r.get("seen") or "")[:200]}
                             for r in rows if r.get("status") == "contradicted"][:6]}
    if swapped:
        job = (getattr(ctx, "job", None) or {}).get("id")
        print(f"[reply-truth] job {job}: {len(swapped)} claim(s) the film contradicts, told "
              "truthfully", flush=True)
    return text
