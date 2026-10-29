"""Scope automated review findings to what the customer actually asked for.

Owner, 25 Oct 2026: requests must be carried through to a good result, not
held open by reviewers. 21 Studio requests (8 users, 1,281 credits, 1,304
preview renders in 60 days) were kept from delivery by findings the request
itself contradicted or a reviewer kept re-raising:

- 1794 asked "Strip all original audio. The final export must have NO audio
  track" and was blocked on "the whole mix is quiet: -28.9 LUFS";
- 1912's photo-and-clip ad with no music supplied was blocked on "the mix is
  essentially SILENT";
- 1799 lowered the volume to 20% and the -14 LUFS check called it "quiet";
- 1769 and 1924 watched the same reviewer category come back after every
  repair until the run stopped.

Pure functions only: no I/O, no model calls. The agent tools, the
verification record and the agent loop share them so the render note, the
durable record and the handoff can never disagree.
"""

import re


# ── What the customer asked for ─────────────────────────────────────────────

_NO_AUDIO = re.compile(
    r"\b(?:no|without|zero|remove|removed|strip|stripped|drop|delete|kill|"
    r"cut|mute|muted|silence|silenced)\s+(?:(?:all|any|the|its|of the|every)\s+)*"
    r"(?:original\s+|source\s+|camera\s+)?(?:audio|sound|sounds|soundtrack|"
    r"audio track)\b(?!\s*(?:effects?|fx|design|quality|levels?|issues?|"
    r"problems?|glitch))"
    r"|\b(?:silent|mute|muted|soundless)\s+(?:video|edit|film|reel|export|"
    r"version|cut|montage|clip)\b"
    r"|\bmake\s+(?:it|the video|the edit|this)\s+(?:completely\s+)?silent\b"
    r"|\bno\s+audio\s+track\b"
    r"|\bbez\s+zvuku\b|\bsin\s+(?:audio|sonido)\b|\bsans\s+son\b"
    r"|\bohne\s+ton\b|\bsenza\s+audio\b|\bsem\s+(?:som|áudio)\b"
    r"|소리\s*없|무음|без\s+звука|音声なし|无声|ztlum",
    re.I)
_KEEP_AUDIO = re.compile(
    r"\b(?:keep|leave|don't remove|do not remove|preserve)\s+(?:the\s+)?"
    r"(?:original\s+)?(?:audio|sound)\b", re.I)

_NO_CAPTIONS = re.compile(
    r"\b(?:no|without|remove|removed|drop|delete|disable|turn off|"
    r"don't (?:add|want|use|put)|do not (?:add|want|use|put))\s+"
    r"(?:(?:any|the|all)\s+)?(?:subtitles?|captions?|subs)\b"
    r"|\b(?:subtitles?|captions?)\s+off\b"
    r"|\bbez\s+titulk|\bsin\s+subt[ií]tulos|\bsans\s+sous-titres"
    r"|\bohne\s+untertitel|\bsenza\s+sottotitoli|\bsem\s+legendas"
    r"|자막\s*(?:없|빼)|без\s+субтитр|ne\s+legyen\s+felirat|felirat\s+nélkül",
    re.I)
_WANT_CAPTIONS = re.compile(
    r"\b(?:add|with|put|include|burn|want|need|turn on)\s+(?:(?:some|the|"
    r"english|animated|bold)\s+)*(?:subtitles?|captions?)\b", re.I)


def wants_no_audio(text):
    """The customer asked for a silent programme ("strip all original audio",
    "no sound", "mute it")."""
    text = str(text or "")
    return bool(_NO_AUDIO.search(text)) and not _KEEP_AUDIO.search(text)


def wants_no_captions(text):
    """The latest explicit caption instruction is OFF ("no subtitles")."""
    text = str(text or "")
    last_off = max((m.end() for m in _NO_CAPTIONS.finditer(text)), default=-1)
    if last_off < 0:
        return False
    last_on = max((m.end() for m in _WANT_CAPTIONS.finditer(text)
                   if not re.search(r"(?:n't|\bnot|\bno|\bnever)\s*$",
                                    text[max(0, m.start() - 12):m.start()],
                                    re.I)), default=-1)
    return last_off > last_on


# ── Audio QC scoped to the request ──────────────────────────────────────────

_SILENCE_KINDS = ("silent", "quiet", "no_track", "mastered_off")


def audio_kind(line):
    """Stable category of one audio QC line."""
    text = str(line or "").lower()
    if "no audio track" in text:
        return "no_track"
    if "essentially silent" in text:
        return "silent"
    if "natural level by request" in text:
        return "level"
    if "even though it is mastered" in text:
        # Mastered and still far off target: most of it is silence or a
        # layer is muted. A real loss unless the silence is intended.
        return "mastered_off"
    if "mix is quiet" in text or "whole mix is quiet" in text:
        return "quiet"
    if "mix is hot" in text:
        return "hot"
    if "true peak" in text:
        return "peak"
    if "dead air" in text:
        return "dead_air"
    return "other"


def keep_span(row):
    """(start, end) of one keep entry: an EDL stores [start, end] pairs;
    dicts with start/end are accepted too. None when unreadable."""
    try:
        if isinstance(row, dict):
            return float(row["start"]), float(row["end"])
        return float(row[0]), float(row[1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _span_coverage(spans, keep):
    """Share of the kept source time covered by [start, end] spans."""
    total = 0.0
    covered = 0.0
    for row in keep or []:
        span = keep_span(row)
        if span is None:
            continue
        a, b = span
        if b <= a:
            continue
        total += b - a
        for s, e in spans:
            covered += max(0.0, min(b, e) - max(a, s))
    return (covered / total) if total > 0 else 0.0


def volume_coverage(edl, max_gain_db):
    """Share of the programme's kept source time whose original audio is
    set at or below ``max_gain_db`` by volume automation."""
    spans = []
    for row in (edl or {}).get("volume") or []:
        try:
            if float(row.get("gain_db", 0.0)) <= max_gain_db:
                spans.append((float(row["start"]), float(row["end"])))
        except (KeyError, TypeError, ValueError):
            continue
    return _span_coverage(spans, (edl or {}).get("keep") or [])


def _added_audio(edl):
    """Unmuted designed audio layers: music, voice-over or sound effects."""
    for kind in ("music", "voiceover", "sfx"):
        for row in (edl or {}).get(kind) or []:
            if isinstance(row, dict) and not row.get("mute"):
                try:
                    if float(row.get("gain_db", 0.0)) <= -59.0:
                        continue
                except (TypeError, ValueError):
                    pass
                return True
    return False


def _audible_inserts(edl):
    for row in (edl or {}).get("inserts") or []:
        if not isinstance(row, dict) or row.get("mute"):
            continue
        key = str(row.get("asset_key") or row.get("storage_key") or "").lower()
        if row.get("kind") in {"image", "still", "photo"} or re.search(
                r"\.(?:png|jpe?g|webp|gif|heic|avif|bmp|tiff?)$", key):
            continue
        return True
    return False


def silence_by_design(edl, source_has_audio=True):
    """Whether a quiet or silent mix is what this EDL deliberately builds:
    no designed audio layer was added, and the only possible sound (the
    original's own) is absent or muted for nearly the whole programme, with
    no audible video insert. A silent mix with an unmuted original that HAS
    sound is a real defect (the sound was lost) and keeps blocking."""
    edl = edl or {}
    if _added_audio(edl) or _audible_inserts(edl):
        return False
    if not edl.get("keep"):
        return True
    if source_has_audio is False:
        return True
    return volume_coverage(edl, -40.0) >= 0.9


def deliberately_lowered(edl):
    """The original was turned down on purpose for (nearly) the whole
    programme ("lower the volume to 20%")."""
    return volume_coverage(edl, -1.0) >= 0.9


def split_audio_qc(findings, edl, request_text="", source_has_audio=True):
    """(blocking, advisory) audio QC lines for this version.

    A silent or quiet measurement does not hold a request that asked for no
    audio, an EDL that is silent by design (no music supplied, the original
    muted or absent), or a level the user lowered on purpose; neither does a
    natural-level note once the mix ships at its natural level by choice.
    Dead air in a silent-by-request programme is the request, not a gap."""
    blocking, advisory = [], []
    # A request for silence does not excuse a silent mix that HAS music or a
    # voice-over on it ("mute the original and add music"): that sound is lost.
    silent_request = wants_no_audio(request_text) and not _added_audio(edl)
    by_design = silence_by_design(edl, source_has_audio)
    lowered = deliberately_lowered(edl)
    for line in findings or []:
        kind = audio_kind(line)
        scoped = (
            (kind in _SILENCE_KINDS and (silent_request or by_design))
            or (kind in {"quiet", "mastered_off"} and lowered)
            or kind == "level"
            or (kind == "dead_air" and (silent_request or by_design)))
        (advisory if scoped else blocking).append(line)
    return blocking, advisory


# ── One repair per reviewer category ────────────────────────────────────────

_REVIEW_CATEGORY = re.compile(
    r"independent (visual|story) review[^\[]{0,80}\[([a-z_]+)\]", re.I)


def _norm(text, words=6):
    clean = re.sub(r"\d+(?:\.\d+)?", "#", str(text or "").lower())
    clean = re.sub(r"[^a-z#: ]+", " ", clean)
    return " ".join(clean.split()[:words])


def finding_key(row):
    """Stable reviewer category of one durable finding, across versions.

    Reviewers phrase the same complaint differently on every render and name
    new times; the category is what recurs."""
    if not isinstance(row, dict):
        return "line:" + _norm(row)
    message = str(row.get("message") or "")
    m = _REVIEW_CATEGORY.search(message)
    if m:
        return f"{m.group(1).lower()}:{m.group(2).lower()}"
    if row.get("department") == "audio_review" \
            or message.lower().startswith("audio qc:"):
        kind = audio_kind(message)
        return "audio:" + (kind if kind != "other" else _norm(message, 4))
    if message.lower().startswith("picture qc:"):
        return "picture:" + _norm(message[len("picture qc:"):], 4)
    return "craft:" + _norm(message)


def is_review(row):
    """A reviewer judgement (visual/story/audio critics, render QC, taste and
    treatment-execution lines), as opposed to a deterministic contract or
    safety finding with its own code."""
    return isinstance(row, dict) and row.get("code") == "review_finding"


def recurring_keys(prior_records, version):
    """Categories the request already asked the editor to repair: blocking
    reviewer findings on earlier versions of this request."""
    keys = set()
    for v, record in (prior_records or {}).items():
        try:
            if int(v) >= int(version):
                continue
        except (TypeError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("record"), dict):
            record = record["record"]
        for row in (record or {}).get("unresolved_findings") or []:
            if is_review(row):
                keys.add(finding_key(row))
        for row in (record or {}).get("findings") or []:
            # Already demoted once: it stays advisory on every later version.
            if is_review(row) and (row.get("evidence") or {}).get(
                    "recurring_after_repair"):
                keys.add(finding_key(row))
    return keys


def demote_recurring(findings, prior_records, version):
    """Reviewer findings whose category was already raised (and repaired)
    on an earlier version of this request become advisory: one repair per
    category. Deterministic codes (the request contract, flash safety,
    corrupt glyphs, missing render evidence) are never demoted here."""
    keys = recurring_keys(prior_records, version)
    if not keys:
        return findings
    out = []
    for row in findings:
        if is_review(row) and row.get("severity") != "advisory" \
                and finding_key(row) in keys:
            row = dict(row, severity="advisory",
                       evidence=dict(row.get("evidence") or {},
                                     recurring_after_repair=True),
                       repair=("advisory: this reviewer category was already "
                               "repaired once in this request"))
        out.append(row)
    return out


# ── The request contract (what the brief explicitly fixes) ──────────────────

_ALL_CLIPS = re.compile(
    r"\b(?:use|using|include|including|put in|incorporate|need|want|from|"
    r"with)\s+(?:(?:all|every|each)"
    r"\s+(?:of\s+)?(?:the\s+|my\s+|these\s+|those\s+)?(?:\d+\s+)?|(?:\d+\s+))"
    r"(?:clips?|videos?|footage|shots?|files?|photos?|pictures?|images?|"
    r"media|uploads?|assets?)\b"
    r"|\b(?:all|every one of)\s+(?:the\s+|my\s+)?(?:clips?|videos?|footage|"
    r"files?|photos?|media)\s+(?:i|we)\s+(?:gave|sent|uploaded|shared|"
    r"provided|attached)\b"
    r"|\bnot\s+(?:just\s+)?(?:only\s+)?one\s+clip\b"
    r"|\b(?:use|using|include)\s+everything\b",
    re.I)
_ALL_CLIPS_NUMBER = re.compile(
    r"\b(?:all|use)\s+(?:the\s+|my\s+|of\s+the\s+)?(\d{1,3})\s+"
    r"(?:clips?|videos?|files?|shots?|photos?)\b", re.I)
_CHRONOLOGICAL = re.compile(
    r"\b(?:chronological(?:ly)?|in (?:the )?(?:original |same )?order|"
    r"in sequence|not jumbled|don'?t (?:shuffle|jumble|reorder|mix up)|"
    r"do not (?:shuffle|jumble|reorder|mix up)|keep (?:the )?order|"
    r"as it happened|start to finish|beginning to end)\b", re.I)
_PHOTO_WORDS = re.compile(r"\b(?:photos?|pictures?|images?|stills?)\b", re.I)


def wants_all_clips(text):
    """The brief asks for every supplied clip ("Use all 50 clips", "all the
    clips I gave you"). Returns (True, n or None) or (False, None)."""
    text = str(text or "")
    if not _ALL_CLIPS.search(text):
        return False, None
    m = _ALL_CLIPS_NUMBER.search(text)
    return True, (int(m.group(1)) if m else None)


def wants_chronological(text):
    return bool(_CHRONOLOGICAL.search(str(text or "")))


def keep_in_source_order(edl):
    """Whether the main video's kept segments play in source order."""
    starts = [span[0] for span in
              (keep_span(row) for row in (edl or {}).get("keep") or [])
              if span is not None]
    return all(b >= a - 0.01 for a, b in zip(starts, starts[1:]))


def clip_coverage(edl, media_keys):
    """(used, available) of the customer's supplied clips/photos."""
    if not media_keys:
        return 0, 0
    used = set()
    for kind in ("inserts", "overlays"):
        for row in (edl or {}).get(kind) or []:
            if isinstance(row, dict) and row.get("asset_key"):
                used.add(row["asset_key"])
    available = set(media_keys)
    return len(available & used), len(available)


def contract_lines(text, duration_target=None):
    """Plain contract lines for the editor's system context: what this brief
    explicitly fixes, checked deterministically before delivery. Short-form
    defaults (a hook-first 9:16 montage with captions) do not apply to what
    the brief fixes otherwise."""
    lines = []
    if duration_target:
        lo, hi = duration_target["min_s"], duration_target["max_s"]
        span = (f"at most {hi:g} s" if lo <= 0.2 else
                f"{lo:g}-{hi:g} s" if not duration_target.get("target_s")
                else f"about {duration_target['target_s']:g} s "
                     f"({lo:.1f}-{hi:.1f} s)")
        lines.append(f"Programme length: {span}"
                     + (" (the user's wording was unclear: treat it as a "
                        "guide)" if duration_target.get("advisory") else
                        " — checked before delivery."))
        if (duration_target.get("target_s") or hi) >= 300 and lo > 0.2:
            lines.append(
                "LONG-FORM: this is a long programme, not a short. Build "
                "the whole runtime as a story with chapters (setup, build, "
                "peak, resolution) from the supplied footage; let moments "
                "breathe; no hook-first montage, no burned captions and no "
                "9:16 reframing unless the user asked for them. Lay the "
                "full-length timeline down first (keep_segments / "
                "insert_media covering the runtime), then refine it.")
    if wants_no_captions(text):
        lines.append("Captions/subtitles: OFF (the user said no "
                     "subtitles) — checked before delivery.")
    if wants_no_audio(text):
        lines.append("Audio: the user wants it silent; a quiet or silent "
                     "mix is correct here.")
    every, n = wants_all_clips(text)
    if every:
        lines.append("Use EVERY supplied clip" + (f" (the user says {n})"
                                                  if n else "")
                     + " at least once — checked before delivery; list_assets "
                       "shows which are not in the edit yet.")
    if wants_chronological(text):
        lines.append("Order: chronological, as recorded — no reordering of "
                     "the footage.")
    return lines
