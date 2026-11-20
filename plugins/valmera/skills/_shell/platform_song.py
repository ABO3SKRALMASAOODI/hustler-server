"""The song a film is posted with, chosen by Valmera, attached by the publisher from the platform's own catalog.

Owner, 15 Nov 2026: "go with the best route that gives us the highest quality without getting banned. TikTok and
YouTube are strict; we can use their libraries; Instagram doesn't care." The judge's films (the 28 @metromedia.house
reels) play recognisable hits, prestige film and TV cues and Artlist or Epidemic library cues (platform_songs.json, lane
MUSIC-REF: identified by fingerprint, titles only, no audio). A film that carries one of those recordings is muted or
taken down on TikTok and YouTube; the same song added from the app's own licensed catalog is not. So the film itself
never carries a commercial recording: the publisher posts the film without our music bed and adds the song Valmera chose
here, at its entrance point, under the voice.

A model reads the film and chooses (no keyword rule decides what fits); its answer is checked against the palette. The
platforms attach one sound per post, so one song carries the whole short, from its first frame to its last (`length_s`,
`cover`; 16 Nov: on YouTube the song chosen in the camera played a few seconds of a 58 s film), entering where it is full. The level is the owner's standing one:
original sound 100%, the added song 25% (half of 50%, so the words stay clear). Any failure leaves the folder without
platform_music.json and never stops a delivery; the publisher then keeps its own standing music rule.
"""
import datetime as dt
import importlib.util
import json
import os
import sys

# The worker has its own llm.py. Publishing this helper as plain `llm`
# breaks the worker's photo checks (fence/spend_lane/record are absent),
# and importing after the worker would bind the song chooser to that client.
_LLM_NAME = "_valmera_shell_llm"
if _LLM_NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_LLM_NAME, os.path.join(os.path.dirname(__file__), "llm.py"))
    _client = importlib.util.module_from_spec(_spec)
    sys.modules[_LLM_NAME] = _client
    _spec.loader.exec_module(_client)
llm = sys.modules[_LLM_NAME]

PALETTE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "platform_songs.json")
USED = "platform_songs_used.jsonl"          # this workflow's recent picks, in its state folder, so a wave is not one song
ADDED_PCT, ORIGINAL_PCT = 25, 100            # owner's standing level for platform music (publishing-rules.md)
SKIP_KEYS = ("spend", "source", "named", "styles_banned", "window")
COVER = "whole film"                         # the song plays from the first frame to the last (owner, 16 Nov 2026)
HOW = ("Post the upload file. In the app's own sound or music catalog, search the song and artist, choose it, set its "
       "start to `start`, and make it cover the whole film: its length is the video's (`length_s`), from the first frame to "
       "the last, checked on the app's own sound timeline before posting (YouTube's camera gives a sound chosen before the "
       "import only its own short segment: extend it to the video's end in the editor). Keep the original sound at 100% and "
       "the added song at 25%. Instagram goes through the phones' Instagram app, which has the catalog. If the song is not "
       "in that platform's catalog, try the alternates in order; if none is there, use the standing music rule. Never post "
       "a file that carries a commercial recording.")


def palette():
    return json.load(open(PALETTE, encoding="utf-8"))


def system_prompt(pal):
    """Static and byte-identical for every film (the cacheable prefix): the task, the rules and the whole palette."""
    songs = [dict(song=s["song"], artist=s["artist"], genre=s.get("genre"), released=s.get("released"), source=s.get("source"),
                  reels=s.get("reels"), uses=[dict(reel=u["reel"], part=u["part"], seconds=u["seconds"], enters_at_s=u["enters_at_s"],
                                                   bpm=u.get("bpm"), mood=u.get("mood")) for u in s.get("uses") or []])
             for s in pal["songs"]]
    return (
        "You choose the one song a finished vertical short is posted with on TikTok, YouTube Shorts and Instagram Reels. "
        "The publisher adds it from the app's own licensed sound catalog, under the film's own sound (voice and sound "
        "design) at a quarter of its level, so it must carry the whole short as one bed.\n\n"
        "The bar is the 28 @metromedia.house reels. Every song below is one they actually play, with where they used it "
        "(opening hook, body of the story, close and call to action), for how long, where in the song they entered "
        "(enters_at_s, seconds into the released track), its tempo and its measured character. Their house sequence: "
        f"{pal.get('house_sequence')}\n\n"
        "Choose like their editor would for THIS film: the song whose character carries its story (tension, ambition, "
        "loss, swagger, wonder, a reveal), its subject and its pace. The mood its brief asks for comes first, and the "
        "film's own music bed (named with the film when it has one) was chosen for that same mood: the posted song "
        "carries it too. A light, funny or mischievous film gets a light, playful song, never a heavy prestige theme; "
        "a raw, intimate or reflective one stays gentle; a tense or triumphant one may drive. A money, power or founder "
        "story leans to the house songs and prestige cues when its mood is tense or ambitious; a science or wonder story "
        "to cinematic and electronic cues; a sharp, funny or confrontational clip to the hip-hop and pop hits. Never "
        "pick a song only because it is the most used. Vary from "
        "the songs listed as recently chosen unless one is clearly the right one. The entrance: the point in the song "
        "where it should start under the film's first frame, as m:ss. The song plays at a quarter of the voice's level "
        "from the first frame to the last, so it must enter where it is already full and moving (its beat or its theme "
        "playing), never on a quiet intro, a fade-in or a near-silent first bar that would sit unheard under the voice; "
        "use the entrances their uses show (enters_at_s) when the song's top is quiet, and the top (0:00) only when the "
        "song is full from its first second.\n\n"
        "Answer with JSON only: {\"song\": exact title from the list, \"artist\": exact artist from the list, "
        "\"start\": \"m:ss\", \"alternates\": [two more {\"song\", \"artist\", \"start\"} from the list, in order, for when "
        "the first is missing from a platform's catalog], \"why\": one sentence on why it fits this film}.\n\n"
        "THE SONGS:\n" + json.dumps(songs, ensure_ascii=False, separators=(",", ":")))


def film_facts(workflow, script, seconds):
    """The variable part, last: what this film is."""
    s = {k: v for k, v in (script or {}).items() if k not in SKIP_KEYS}
    style = s.pop("style", None)
    facts = dict(workflow=workflow, style=(style or {}).get("id") if isinstance(style, dict) else style, seconds=seconds, script=s)
    return json.dumps(facts, ensure_ascii=False, default=str)[:6000]


def recent(state_root, n=12):
    p = os.path.join(state_root or "", USED)
    if not state_root or not os.path.exists(p):
        return []
    rows = []
    for line in open(p, encoding="utf-8"):
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return [f"{r.get('song')} - {r.get('artist')}" for r in rows[-n:]]


def _key(song, artist):
    return (" ".join(str(song or "").casefold().split()), " ".join(str(artist or "").casefold().split()))


def _start(v):
    """"m:ss" from the model's start (a string or seconds); 0:00 when unreadable."""
    try:
        if isinstance(v, (int, float)):
            sec = max(0, int(v))
        else:
            parts = [int(x) for x in str(v).strip().split(":")]
            sec = max(0, parts[0] * 60 + parts[1]) if len(parts) == 2 else max(0, parts[0])
    except (ValueError, IndexError):
        sec = 0
    return f"{sec // 60}:{sec % 60:02d}"


def check(answer, pal):
    """The model's pick and alternates, kept only where they are songs of the palette (exact title and artist)."""
    known = {_key(s["song"], s["artist"]): s for s in pal["songs"]}
    picks = [answer] + list(answer.get("alternates") or [])
    ok = []
    for p in picks:
        if not isinstance(p, dict):
            continue
        s = known.get(_key(p.get("song"), p.get("artist")))
        if s and all(_key(s["song"], s["artist"]) != _key(o["song"], o["artist"]) for o in ok):
            ok.append(dict(song=s["song"], artist=s["artist"], start=_start(p.get("start"))))
    return ok


def bed_line(music):
    """What the film's own music bed is, for the song chooser: its track and the mood it was chosen for (the film's
    music record: chain.music_version), or "" when the film has none."""
    m = music or {}
    if not m.get("title"):
        return ""
    does = m.get("does") or {}
    mood = str((does.get("marks") or {}).get("mood") or does.get("mood") or "").strip()
    out = f"\n\nTHE FILM'S OWN MUSIC BED (in the film file; the posted song replaces it): \"{m.get('title')}\" by {m.get('author')}"
    if mood:
        out += f", chosen for the mood: {mood}"
    if m.get("why"):
        out += f". Why: {str(m['why'])[:300]}"
    return out


def choose(workflow, script, seconds, upload, state_root=None, ask=None, film_music=None):
    """platform_music.json's content for one film, or None (the reason is in the return of choose_safely).
    film_music: the film's own music record (its track and the mood it was chosen for), so the posted song carries
    the same mood as the bed it replaces."""
    pal = palette()
    ask = ask or llm.ask
    content = [{"type": "text", "text": "Recently chosen for other films: " + (", ".join(recent(state_root)) or "none")
                + "\n\nTHE FILM:\n" + film_facts(workflow, script, seconds) + bed_line(film_music)}]
    text, use = ask(system_prompt(pal), content, effort="low", max_tokens=2000)
    answer = llm.json_block(text)
    ok = check(answer if isinstance(answer, dict) else {}, pal)
    if not ok:
        raise ValueError(f"the model's song is not in the palette: {str(text)[:200]}")
    first = ok[0]
    rec = dict(contract=1, upload=upload, song=first["song"], artist=first["artist"], start=first["start"],
               original_sound_pct=ORIGINAL_PCT, added_sound_pct=ADDED_PCT, alternates=ok[1:3],
               length_s=round(float(seconds), 2) if seconds else None, cover=COVER,
               platforms=["tiktok", "youtube", "instagram"], how=HOW,
               why=str(answer.get("why") or "")[:300], chosen_by=dict(model=use.get("model"), provider=use.get("provider")),
               chosen=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    if state_root:
        os.makedirs(state_root, exist_ok=True)
        with open(os.path.join(state_root, USED), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(song=rec["song"], artist=rec["artist"], workflow=workflow, at=rec["chosen"]), ensure_ascii=False) + "\n")
    return rec, use


def choose_safely(workflow, script, seconds, upload, state_root=None, log=None, ask=None, film_music=None):
    """(record or None, spend rows). Never raises: a delivery never waits on its song."""
    try:
        rec, use = choose(workflow, script, seconds, upload, state_root=state_root, ask=ask, film_music=film_music)
    except Exception as ex:  # noqa: BLE001 - the film is delivered either way; the publisher keeps its standing rule
        if log:
            log(f"platform song not chosen: {type(ex).__name__}: {str(ex)[:300]}")
        return None, []
    spend = [dict(stage="deliver", provider=use.get("provider"), model=use.get("model"), what="the platform song", usd=float(use.get("usd") or 0))]
    return rec, spend
