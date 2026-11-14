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
                "The reply's whole sentence that makes this claim, copied exactly.")},
            "status": {"type": "string", "enum": ["shown", "contradicted", "cannot_tell"]},
            "seen": {"type": "string", "description": "What the frames show, with sheet and tile."},
            "honest": {"type": "string", "description": (
                "For contradicted: that whole sentence said truthfully (its true parts kept), in the "
                "reply's language and voice, plain, no apology. Otherwise empty.")},
        }}}},
}

SYSTEM = """You check a video editor's reply against the film it delivered, before the customer
reads it. You get the customer's request, the drafted reply, the list of what the edit holds and
when (pictures, clips, titles, effects, with their output seconds) and screening sheets of the
rendered film (tiles labelled with their output time). Tiles are samples: a short clip can fall
between them.

List each sentence of the reply that claims something the customer asked for was done (added,
removed, changed, fixed, kept untouched, left as it was). For each, look at the frames:
- shown: the frames show it or are consistent with it. Something the edit's list holds counts as
  added unless a tile INSIDE its own seconds shows it missing;
- contradicted: a tile clearly shows the opposite in the picture: what the reply says is removed or
  hidden is still visible; what it says is kept untouched (a person, the floor) is visibly dimmed,
  covered, cut or damaged; what it says is clean shows a smear or a patch; what it says was added
  is in neither the list nor any tile;
- cannot_tell: the frames cannot show it (sound, music, pacing, timing, a moment between tiles).
Never mark contradicted from a doubt, a matter of taste or something the reply already admits.
Sentences that say something was NOT done, questions and offers are not claims; leave them out.
For each contradicted claim write `honest`: that whole sentence told truthfully from what the film
shows (what is there now, what still shows, and where), its true parts kept, in the reply's
language and the editor's own plain voice. Use the record_reply_truth tool once."""


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


def _what(it, names):
    key = str(it.get("asset_key") or it.get("storage_key") or "")
    if names.get(key):
        return names[key]
    credit = it.get("credit") if isinstance(it.get("credit"), dict) else {}
    page = str(credit.get("page_url") or "").rstrip("/").rsplit("/", 1)[-1]
    words = " ".join(w for w in page.split("-") if w and not w.isdigit())
    return (words or str(credit.get("title") or it.get("query") or it.get("text") or
                         it.get("template") or it.get("effect") or ""))[:80]


def items(ctx, edl):
    """What the edit holds and when, one line each: titles, pictures and clips over the film,
    region effects. The tiles sample the film; this says what lies between them."""
    from score import asks
    edl = edl if isinstance(edl, dict) else {}
    try:
        names = asks.asset_names(ctx, edl)
    except Exception:  # noqa: BLE001
        names = {}
    out = []

    def t(v):
        try:
            return f"{float(v):.1f}"
        except (TypeError, ValueError):
            return "?"
    for it in edl.get("overlays") or []:
        if isinstance(it, dict):
            end = (float(it.get("start") or 0) + float(it.get("duration_s") or 0)
                   if it.get("duration_s") is not None else it.get("end"))
            out.append(f"[{t(it.get('start'))}-{t(end)} s] {it.get('kind') or 'overlay'} over the "
                       f"film: {_what(it, names)}")
    for it in edl.get("inserts") or []:
        if isinstance(it, dict):
            a = it.get("at_output_s")
            b = (float(a) + float(it.get("duration_s") or 0)) if a is not None else None
            out.append(f"[{t(a)}-{t(b)} s] {it.get('kind') or 'insert'} cut in: {_what(it, names)}")
    for it in edl.get("fx") or []:
        if isinstance(it, dict):
            out.append(f"[{t(it.get('start'))}-{t(it.get('end'))} s] {it.get('type') or 'effect'}: "
                       f"{_what(it, names)}")
    for it in edl.get("texts") or []:
        if isinstance(it, dict):
            out.append(f"[{t(it.get('start'))}-{t(it.get('end'))} s] title: "
                       f"{str(it.get('text') or '')[:60]}")
    return "\n".join(out[:60]) or "no added pictures, clips, titles or effects"


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
    try:
        edl = ctx.latest_edl().get("json")
    except Exception:  # noqa: BLE001
        edl = {}
    content = [{"type": "text", "text": "\n".join([
        "The customer's request:\n" + json.dumps(str(request_text or "")[:3000], ensure_ascii=False),
        "The drafted reply:\n" + json.dumps(str(draft or "")[:3000], ensure_ascii=False),
        "What the edit holds, on the film's clock:\n" + items(ctx, edl)])}]
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
    import reply_grounding
    text = str(draft or "")
    swapped, added = [], []
    for r in rows or []:
        if r.get("status") != "contradicted":
            continue
        quote = str(r.get("quote") or "").strip()
        honest = str(r.get("honest") or "").strip()
        if not honest:
            continue
        # the whole sentence that holds the quoted words, so a clause never leaves half a sentence
        whole = next((sent for sent in reply_grounding.sentences(text) if quote and quote in sent),
                     None)
        if whole:
            text = text.replace(whole, honest, 1)
            swapped.append(whole)
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
