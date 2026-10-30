"""A website demo asked for in the Studio gets the engine, for every account.

Owner, 30 Oct 2026 (his own test, account 60): "the edit was nothing
absolutely nothing but a scroll on the website, wheres the quality of 10 of
10 shorts?" The engine's URL First cut (first_cut.url_chain: a tour of the
site's sections with beats, eased push-ins, a poster title, the phone
layout, a closing card) scored 7.75-8.38 in the harness, but production only
ran it for a walled free-trial account (routes/video._trial_url_direct).
Every other account's request, the "Record my website" tile's included, went
to the Studio agent, which read the tile's own words ("scroll the page from
top to bottom at a steady pace") and called record_website: a plain pan down
the page under a grade and a fade (project 5246, job 126656).

So a Studio turn whose request is a website demo on a blank canvas (no main
video, nothing on the timeline) runs that same URL First cut instead of the
agent: the tile and a typed request alike. The agent still edits it from the
next message on, and a website request in a project that already has footage
stays the agent's (a cutaway inside an edit). Who may make one is decided
where the gate lives (feature_gate, in first_cut.run): a gated account gets
the upgrade card there, never a recording. ``WEBSITE_ENGINE_ROUTE=0`` turns
the route off (the agent answers as before).
"""

import os
import re
from urllib.parse import urlparse

ON = os.getenv("WEBSITE_ENGINE_ROUTE", "1") != "0"

_URL = re.compile(r"(?:https?://|\bwww\.)[^\s<>\"')\]]+", re.I)
# A link to footage or a song is an import, never a website to demo.
_MEDIA_HOSTS = (
    "youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "instagram.com",
    "twitter.com", "x.com", "facebook.com", "fb.watch", "twitch.tv",
    "drive.google.com", "docs.google.com", "dropbox.com", "loom.com",
    "streamable.com", "soundcloud.com", "spotify.com", "music.apple.com",
    "bandcamp.com", "dailymotion.com", "reddit.com", "imgur.com",
    "wetransfer.com", "we.tl", "icloud.com", "onedrive.live.com",
    "1drv.ms", "box.com", "mega.nz", "giphy.com", "pexels.com",
    "pixabay.com", "unsplash.com", "freesound.org", "archive.org",
)
_MEDIA_PATH = re.compile(
    r"\.(?:mp4|mov|webm|m4v|mkv|avi|mp3|wav|m4a|aac|flac|ogg|png|jpe?g|gif|"
    r"webp|heic|pdf|zip)(?:$|[?#])", re.I)
# The request asks for a recording or a demo of it (English, Portuguese,
# Spanish, French, German, Italian: real first requests, C2/Q7)...
_ASK = re.compile(
    r"\b(?:record\w*|screen[\s-]*record\w*|screen\s*capture|captur\w*|"
    r"demo\w*|walk[\s-]*through|showcase\w*|scroll\w*|tour|promo\w*|"
    r"promotion\w*|promocion\w*|promocional|navigat\w*|naveg\w*|grava\w*|"
    r"graba\w*|enregistr\w*|aufnehm\w*|aufnahme|registra\w*|explor\w*|"
    r"presenta\w*|apresent\w*|launch\s+video|product\s+video|explainer|"
    r"commercial|teaser|trailer|ad\s+for|advert\w*)\b", re.I)
# ... or names the site itself
_SITE = re.compile(
    r"\b(?:web\s*site|site|sitio|landing\s*page|web\s*page|homepage|"
    r"home\s*page|web\s*app|saas|portfolio|store|shop|storefront|"
    r"pricing\s+page|página|pagina|webseite)\b", re.I)


def _media_link(url):
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except ValueError:
        return True
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host or "." not in host:
        return True
    if any(host == h or host.endswith("." + h) for h in _MEDIA_HOSTS):
        return True
    return bool(_MEDIA_PATH.search(parsed.path or ""))


def site_link(text):
    """The first link in ``text`` that is a website (not footage, a song or
    a file), normalised to http(s); None for none."""
    for match in _URL.finditer(str(text or "")):
        url = match.group(0).rstrip(".,;:!?*")
        if _media_link(url):
            continue
        return url if "://" in url else f"https://{url}"
    return None


def request_url(text):
    """The website a typed request asks to demo, else None: a website link
    with words asking for a recording or demo of it, or naming the site, or
    a link sent on its own (the tile's request always qualifies)."""
    text = str(text or "")[:4000]
    url = site_link(text)
    if not url:
        return None
    rest = _URL.sub(" ", text)
    if _ASK.search(rest) or _SITE.search(rest):
        return url
    return url if len(re.findall(r"[^\W_]+", rest)) <= 3 else None


def _blank(worker_db, project_id):
    """No main video and nothing on the timeline (the Studio's blank canvas:
    the tile's own condition)."""
    import db as dbx
    if worker_db.run(dbx.latest_asset, project_id, "original"):
        return False
    row = worker_db.run(dbx.latest_edl, project_id)
    if not row:
        return True
    edl = row.get("json") if isinstance(row, dict) else None
    if not isinstance(edl, dict):
        return False
    try:
        import schemas
        if schemas.program_duration(edl) > 0.05:
            return False
    except Exception:  # noqa: BLE001 - any doubt: not blank, the agent works
        return False
    return not (edl.get("overlays") or edl.get("texts") or edl.get("music"))


def route_url(worker_db, job, user_message):
    """The URL this Studio turn should answer with the engine's URL First
    cut, or None (the agent runs as before)."""
    if not ON or not user_message:
        return None
    payload = job.get("payload") or {}
    # never inside the trial's own lane (its URL First cut starts at the
    # post), a continuation, an operator repair, a resumed slice or a First
    # cut's follow-up: those are the agent's
    for key in ("first_cut", "trial_lane", "logical_turn_continuation",
                "operator_repair", "operator_instruction", "after_first_cut",
                "continuation_state", "root_agent_job_id"):
        if payload.get(key):
            return None
    if not payload.get("message_id") or \
            int(payload["message_id"]) != int(user_message.get("id") or 0):
        return None
    if str(user_message.get("role") or "") != "user":
        return None
    text = str(user_message.get("content") or "")
    try:
        import request_intent
        if request_intent.is_review_repair(text):
            return None
    except Exception:  # noqa: BLE001
        pass
    url = request_url(text)
    if not url:
        return None
    try:
        if not _blank(worker_db, job["project_id"]):
            return None
    except Exception as exc:  # noqa: BLE001 - any doubt: the agent works
        print(f"[website_engine] blank-canvas check failed: {exc}", flush=True)
        return None
    return url[:2000]


def engine_job(job, url):
    """The job as the URL First cut reads it (the row itself is unchanged)."""
    payload = dict(job.get("payload") or {})
    payload.update({"url": url, "website_engine": True})
    return dict(job, payload=payload)
