"""Music on request ends on a choice, never on silence (RS_ENGINE, 31 Oct).

User 2665 asked "Cut out the audio and play something else relating to
nature and outdoors and active but chilly vibes". The trial editor muted the
clip, listed the library (whose result says: offer 2-4 tracks with
ask_user) and ended the turn without asking: a silent video and a reply
that said done. Valmera never picks music itself (owner rule, music_gate):
when the user asks for music and the turn ends with none in the edit and no
offer made, the reply ends with 2-4 fitting library tracks for the user to
pick, and the offer is opened so their pick places it next turn.
"""
import re

_NO_MUSIC = re.compile(
    r"\b(?:no|without|remove|delete|drop|kill|mute)\s+(?:the\s+|any\s+|all\s+)?"
    r"(?:background\s+)?(?:music|song|soundtrack|track)\b|"
    r"\b(?:don'?t|do\s+not|never)\s+(?:add|use|put|play)\s+(?:any\s+)?"
    r"(?:music|songs?)\b", re.I)
_MUSIC = re.compile(
    r"\b(?:music|song|soundtrack|background\s+track|beat|instrumental|tune)\b|"
    r"\bplay\s+something\b|\b(?:replace|swap)\s+the\s+(?:audio|sound)\s+with\b|"
    r"\bcut\s+out\s+the\s+audio\s+and\s+(?:play|put|add)\b", re.I)

# request words -> library moods (music_library.MOODS), most specific first
_MOOD_WORDS = (
    ("chill", r"chill|chilly|relax|calm|lofi|lo-fi|laid.?back|mellow|cozy|cosy"),
    ("ambient", r"nature|outdoor|outdoors|forest|mountain|hill|ocean|sea|"
                r"airy|peaceful|ambient|atmospher|dreamy|serene"),
    ("upbeat", r"active|energetic|energy|upbeat|happy|fun|hype|fast|driving|"
               r"workout|sport"),
    ("cinematic", r"cinematic|epic|movie|film|orchestral|story"),
    ("dramatic", r"dramatic|tense|dark|intense|suspense|thriller"),
    ("inspiring", r"inspir|hopeful|motivat|uplift|emotional|wedding"),
    ("hiphop", r"hip.?hop|rap|boom.?bap|trap|street"),
    ("corporate", r"corporate|business|explainer|product|clean"),
)


def wants_music(text):
    """True when the user asked for music to be added or to replace the
    sound (not when they asked to remove it)."""
    t = str(text or "")
    if not t.strip() or _NO_MUSIC.search(t) and not re.search(
            r"\b(?:play|add|put)\s+(?:some|a|new|other|different)\b", t, re.I):
        return False
    return bool(_MUSIC.search(t))


def moods_for(text):
    t = str(text or "").lower()
    hits = [m for m, pat in _MOOD_WORDS if re.search(r"\b(?:" + pat + r")", t)]
    return hits or ["chill", "upbeat", "cinematic"]


def has_music(edl):
    return any(not (m or {}).get("mute") for m in (edl or {}).get("music") or [])


def offer_tracks(text, catalog, used=(), n=3):
    """2-4 tracks for the moods the request names: one per mood first (the
    request's own order), then more of the first mood; tracks this user
    already used elsewhere go last."""
    moods = moods_for(text)
    pools = {m: [t for t in catalog if t.get("mood") == m] for m in moods}
    for m in pools:
        pools[m].sort(key=lambda t: (t.get("slug") in used, t.get("slug")))
    out = []
    while len(out) < n and any(pools.values()):
        for m in moods:
            if pools[m] and len(out) < n:
                out.append(pools[m].pop(0))
    return out[:4]


def offer_text(tracks):
    """The question the user reads (it names each track's exact title, so
    music_gate.open_offer records the offer and their pick places it)."""
    lines = []
    for i, t in enumerate(tracks, 1):
        lines.append(f"{i}. \"{t['title']}\" ({t['mood']})")
    return ("For the music, pick one and I'll put it under the video: "
            + "; ".join(lines) + ". Reply with its number or name, or "
            "upload your own track.")


def turn_end_offer(ctx, request_text):
    """The offer sentence to end this turn's reply with, or ''. Opens the
    music_gate offer. Never raises."""
    try:
        if not wants_music(request_text):
            return ""
        if getattr(ctx, "_music_offer_made", False):
            return ""
        import music_gate
        import music_library
        if getattr(music_gate._root(ctx), "_music_offer", None):
            return ""          # the editor already asked this turn
        edl = ctx.latest_edl()["json"]
        if has_music(edl):
            return ""
        cat = music_library.catalog()
        if not cat:
            return ""
        try:
            used = music_library.used_by_user(ctx)
        except Exception:  # noqa: BLE001
            used = set()
        tracks = offer_tracks(request_text, cat, used=used)
        if len(tracks) < 2:
            return ""
        text = offer_text(tracks)
        if not music_gate.open_offer(ctx, text):
            return ""
        ctx._music_offer_made = True
        return text
    except Exception as exc:  # noqa: BLE001
        print(f"[music_offer] skipped ({str(exc)[:120]})", flush=True)
        return ""
