"""Cheap, version-specific finishing evidence for every editorial decision.

This does not certify unseen pixels, lock tools, or erase a review finding.
It keeps known repairs in front of the editor while a new version awaits review.
"""
import quality_verifier
import taste
from timeline import Timeline


DIRECTIVE_PREFIX = "CURRENT FINISHING EVIDENCE:"


def current_findings(ctx, row):
    edl = row.get("json") or {}
    index = getattr(ctx, "index", None) or {}
    video = index.get("video") or {}
    request = quality_verifier.request_text_for(ctx)
    findings = taste.critique(
        edl, index, Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                             edl.get("speed") or []),
        src_w=video.get("width"), src_h=video.get("height"), user_asked=request)
    findings += [r["message"] for r in quality_verifier.deterministic_findings(
        edl, index, request_text=request)]
    return list(dict.fromkeys(findings))


def directive(ctx):
    if not (getattr(ctx, "versions_written", None)
            or ((getattr(ctx, "job", None) or {}).get("payload") or {}).get("operator_repair")):
        return None
    row = ctx.latest_edl()
    version = int(row["version"])
    records = getattr(ctx, "verification_records", None) or {}
    current = records.get(version) or records.get(str(version)) or {}
    fresh = current_findings(ctx, row)
    # A justified finding remains visible in history, but must not be
    # reintroduced as an open repair for this same immutable version.
    resolved = {r.get("message") for r in current.get("findings") or []}
    resolved -= {r.get("message") for r in current.get("unresolved_findings") or []}
    fresh = [line for line in fresh if line not in resolved]
    lines = list(fresh)
    lines += [str(r.get("message") or r) for r in current.get("unresolved_findings") or []
              if r.get("code") != "complete_preview_missing"]
    lines = list(dict.fromkeys(lines))
    header = f"{DIRECTIVE_PREFIX} EDL v{version}. "
    if lines:
        return (header + "Repair these current findings before optional polish or another full encode:\n- "
                + "\n- ".join(s[:1000] for s in lines[:8])
                + "\nChoose the smallest targeted edit. A metadata finding such as transition density "
                  "does not require inspecting every source again. Preserve shots and audio that already "
                  "passed. Once repaired, render and verify the new version. If direct evidence shows "
                  "a false positive, use justify_verification_findings; never claim an unchecked pass.")
    if (current.get("status") in {"passed", "justified"}
            and current.get("complete_preview_passed") is True):
        return (header + "The complete preview and its verification passed. If the customer's requested "
                "work is fulfilled, finish now with the saved result. Do not invent extra polish or "
                "reopen passed work merely to use more tools. An actual remaining user requirement "
                "still takes precedence.")
    reviewed = [(int(v), r) for v, r in records.items()
                if int(v) < version and r.get("complete_preview_passed")]
    if reviewed:
        previous_version, previous = max(reviewed, key=lambda item: item[0])
        unresolved = previous.get("unresolved_findings") or []
        if unresolved:
            return (header + f"Not yet verified. The last complete review was v{previous_version}:\n- "
                    + "\n- ".join(str(r.get("message") or r)[:1000] for r in unresolved[:6])
                    + "\nThese are prior-version findings, not proof about current pixels. Confirm the "
                      "targeted repairs with one current preview. Do not restart broad research or "
                      "change unrelated passed work while verification is pending.")
    return None


def refresh(ctx, messages, preserve_prefix=False):
    # Grok keeps an append-only prefix for caching. Each changed status
    # explicitly supersedes prior versions; unchanged status adds no tokens.
    previous = [m["content"] for m in messages if (
        m.get("role") == "system" and isinstance(m.get("content"), str)
        and m["content"].startswith(DIRECTIVE_PREFIX))]
    if not preserve_prefix:
        messages[:] = [m for m in messages if not (
            m.get("role") == "system" and isinstance(m.get("content"), str)
            and m["content"].startswith(DIRECTIVE_PREFIX))]
    try:
        note = directive(ctx)
    except Exception as exc:
        # The real immutable render/verification gate remains authoritative.
        print(f"[finishing] preflight unavailable: {type(exc).__name__}", flush=True)
        return
    if preserve_prefix:
        if note is None and not previous:
            return
        note = (note or DIRECTIVE_PREFIX + " No current preflight findings. Current render verification still applies.")
        note += "\nThis current status supersedes every earlier finishing status in this conversation."
        if previous and previous[-1] == note:
            return
    if note:
        messages.append({"role": "system", "content": note})
