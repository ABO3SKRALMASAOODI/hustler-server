"""The film's music bed (lane K round five, 13 Nov 2026).

The owner, 13 Nov: "Music: choose it the way the references do ... Valmera may pick the track itself. Optimise for
the highest quality; the customer decides whether to keep it." The judge's films carry a bed under 87% of their
length that drops out on the turn (judge/metromedia/TEARDOWN.md, rows 3 to 5).

What chooses is lane MUSIC's worker/music_choose.py: a licensed library, measured against the references' music by
kind of film, a signal shortlist and one model pick. What this module does is the Director's side of it:

    start(work, scout, per, index)  as soon as the Scout has read the upload, on a thread beside Supply: is a bed
                                    possible here, and which track? (bed.json; the track's audio starts on its way
                                    into storage at once, so it is there when the film is written)
    brief_line(work)                the Brief: the bed's tempo, so a film with no voice can cut on its beat
    planned(ctx)                    the compiler's question: will a bed play under this film?
    settle(work, comp, ...)         after the compile: the stretch of the track, its level and the turn, from the
                                    film's own numbers
    items(work, prog_s)             lower.py: the EDL's music item and the drop on the turn, on the programme clock
    project_asset(ctx, db, doc)     score.film: the track as an asset of this project, with its licence and credit

A bed is never added over an upload that carries its own song (taste.has_bed), nor when the Score says
`"music":"none"`, nor when the customer's asks speak about music (the Scout bound one to the music probe: the
agent's own music tools answer those). Nothing here reads what anyone typed. The track is a library asset with its
licence and credit on the asset row, placed with provenance `valmera_chosen`; the customer can remove it.

Every step fails closed: no library, no chooser, no storage, a late answer, and the film goes out as it would have
without a bed. VALMERA_BED=0 turns the whole thing off.
"""
import json
import math
import os
import time

PROVENANCE = "valmera_chosen"
VOICE_LUFS = -20.0          # the renderer levels speech here before the mix (dialogue_level)
# The bed against the voice, dB under it while words play. The judge sits 5.3 under (0.7 to 20); the references by
# kind, measured on stems with their placed sounds inside (lanes/S study): stories 5.8, marketing 7.8, podcast clips
# 12.0. Ours is one step under each, as the sound engine keeps it, and a customer's phone voice is not a narrator's:
# a talking take stays clear first.
UNDER_VOICE = {"story": 8.0, "marketing": 8.5, "podcast": 10.0, "longform": 15.0}
LEAD_LUFS = -19.0           # with nobody speaking the music is the lead; the master brings the whole film to -14
DROP_S = 0.85               # the bed stops this long before the turn and is back on it (the judge: 0.5 to 2.3 s)
DROP_FADE_MS = 90.0
FADE_IN_S, FADE_OUT_S = 0.12, 0.6
MIN_S = 2.5                 # a film this short still carries a bed: a product held up for 3 s is a sting, and went out dry at 4 (p5582)
PICK_S = 6.0                # the model's pick; past it the signal's first option stands
FILE = "bed.json"


OWN_TRACK_DB = -38.0        # an upload's own sound is its music from here up: under it, it is the room it was shot in


def on():
    return os.environ.get("VALMERA_BED", "1") != "0"


def own_track(bins, n_words, dur):
    """Does the upload carry a track of its own that a bed would fight: a song, a game's sound, a crowd, steady
    under most of it, nobody talking over it, and loud enough to be the programme? The governor's has_bed counts the
    hum of a quiet room as a bed too (top over -45 dB); a product held in a silent kitchen at -47 dB was told it
    had its own music and went out dry (p5582, 13 Nov). bins: perception's level every half second, dB."""
    bins = [float(b) for b in bins or []]
    if not bins or n_words / max(float(dur or 0), 1e-3) >= 1.0:
        return False
    top = sorted(bins)[int(len(bins) * 0.9)]
    return sum(1 for b in bins if b >= top - 20.0) >= 0.5 * len(bins) and top > OWN_TRACK_DB


def available():
    """Is there a library to choose from, and does this build's write path know a track Valmera chose?"""
    if not on():
        return False
    try:
        import music_choose
        import music_gate
        return PROVENANCE in getattr(music_gate, "PROVENANCES", ()) and bool(music_choose.tracks())
    except Exception:  # noqa: BLE001 - no chooser in this build: no bed
        return False


def _read(work):
    try:
        with open(os.path.join(work, FILE), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(work, doc):
    with open(os.path.join(work, FILE), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)


def _spoken_share(index, dur):
    words = (index or {}).get("words") or []
    return min(1.0, sum(max(0.0, float(w.get("t1") or 0) - float(w.get("t0") or 0)) for w in words) / max(float(dur or 0), 1e-3))


def prechoose(work, scout, per, index, deadline=None, model=None, fetch=None, used=()):
    """Before the Director writes: can this film have a bed, and which track? The film does not exist yet, so the
    track is chosen on what is known (who speaks how much, how long the upload is, how many pictures the Bin can
    give, what the Scout says it is about); the stretch of it, its level and its turn are settled on the compiled
    film. An ask the Scout bound to the music probe (a model read it) leaves music to the customer's own request.
    fetch(track) starts the track's audio on its way into storage. Never raises."""
    try:
        if not available():
            return None
        if any(str(a.get("probe")) == "music" for a in (scout or {}).get("asks") or () if isinstance(a, dict)):
            _write(work, {"on": False, "why": "the customer asked about music: their request decides it"})
            return None
        import music_choose
        dur = float((per or {}).get("dur") or 0.0)
        if own_track(((per or {}).get("audio") or {}).get("rms_db"), len((index or {}).get("words") or []), dur):
            _write(work, {"on": False, "why": "the upload carries its own track: it is the film's music"})
            return None
        share = _spoken_share(index, dur)
        est = max(6.0, min(dur, 40.0))
        wishes = len((scout or {}).get("wishes") or [])
        kind = "marketing" if share < 0.15 else "story" if wishes >= 4 else "podcast"
        f = {"kind": kind, "duration_s": round(est, 1), "turns": [], "pace": {"cuts10": 8.0 if kind != "podcast" else 3.0},
             "speech_share": round(share, 3), "brief": str((scout or {}).get("about") or "")[:300]}
        ask = None
        if model and deadline is not None and deadline - time.monotonic() > 3.0:
            ask = _pick_call(model, min(deadline, time.monotonic() + PICK_S))
        # No house song: by signal the same two or three tracks lead every film of a kind. Two of the four the signal
        # ranks first step back for this film, which two decided by the film itself (its length and what it is
        # about), so films of one kind draw on different parts of the library and the model picks among eight.
        skip = []
        try:
            import hashlib
            import random
            top, _prof = music_choose.shortlist(f, used=used, n=6)
            lead = [o["cid"] for o in top[:4]]
            rng = random.Random(int(hashlib.sha256(f"{f['brief']}|{dur:.2f}".encode()).hexdigest()[:8], 16))
            skip = rng.sample(lead, k=min(2, len(lead)))
        except Exception:  # noqa: BLE001 - the signal's own order stands
            skip = []
        ch = music_choose.choose(f, used=list(used) + skip, call=ask or (lambda *_a: {}), n=8)
        if not ch:
            _write(work, {"on": False, "why": "the library offered nothing"})
            return None
        doc = {"on": True, "cid": ch["cid"], "title": ch.get("title"), "author": ch.get("author"), "license": ch.get("license"), "credit": ch.get("credit"),
               "page_url": ch.get("page_url"), "storage_key": ch.get("storage_key"), "bpm": ch.get("bpm"), "kind": kind, "why": ch.get("why"),
               "chosen_by": ch.get("chosen_by"), "offset_s": float(ch.get("start_s") or 0.0), "options": ch.get("options"), "settled": False}
        _write(work, doc)
        if fetch is not None:
            try:
                fetch(music_choose.find(ch["cid"]) or {})
            except Exception:  # noqa: BLE001 - the film's write tries once more, and goes dry if it is not there
                pass
        return doc
    except Exception as e:  # noqa: BLE001 - no bed, and the reason on file
        try:
            _write(work, {"on": False, "why": f"{type(e).__name__}: {str(e)[:200]}"})
        except OSError:
            pass
        return None


def start(work, scout, per, index, deadline=None, model=None, fetch=None, used=()):
    """prechoose on a thread beside Supply. Returns the thread (join it with a short timeout before the Brief)."""
    import threading
    carried = None
    try:
        import broll_source
        carried = broll_source.carried(prechoose)        # the film's metered scope follows the call onto the thread
    except Exception:  # noqa: BLE001
        carried = prechoose
    th = threading.Thread(target=carried, args=(work, scout, per, index, deadline, model, fetch, used), name="film-bed", daemon=True)
    th.start()
    return th


def brief_line(work):
    """What the Director is told about the bed it will get: that there is one, and its beat in frames."""
    d = _read(work)
    if not d.get("on"):
        return None
    line = "A music bed will play under the film, chosen for it from a licensed library and mixed under the voice after you write (`film.music` says what it does)."
    try:
        bpm = float(d.get("bpm") or 0)
    except (TypeError, ValueError):
        bpm = 0.0
    if 60 <= bpm <= 200:
        beat = 60.0 / bpm * 30
        line += (f" It runs at {bpm:.0f} bpm: a beat every {beat:.1f} frames, a bar every {4 * beat:.0f}. A film with no voice cuts on it: shot lengths of "
                 f"{round(beat)}, {round(2 * beat)}, {round(3 * beat)} or {round(4 * beat)} frames, a title or a flash on a bar.")
    return line


def refused(film):
    """The Score said this film stays dry."""
    m = (film or {}).get("music")
    return isinstance(m, str) and m.strip().lower() in ("none", "no", "off", "dry")


def planned(ctx):
    """Will a bed play under the film being compiled? The work folder offered one, the Score did not refuse it."""
    work = getattr(ctx, "work", None)
    if not work or refused(getattr(ctx, "film", None)):
        return False
    return bool(_read(work).get("on"))


# ----------------------------------------------------------------- the film's numbers

def _frame_of_word(l1, index, w):
    """Programme frame where source word w starts, through the spine. None when the film dropped it."""
    words = (index or {}).get("words") or []
    if not (0 <= w < len(words)):
        return None
    t = float(words[w].get("t0") or 0.0)
    fps = float(l1["fps"])
    for s in sorted(l1.get("spine") or [], key=lambda s: s["f"][0]):
        if s["src"][0] - 0.02 <= t < s["src"][1]:
            return int(round(s["f"][0] + (t - s["src"][0]) * fps))
    return None


def _beat_frame(l1, bid):
    fs = [it["f"][0] for it in l1.get("items") or [] if str(it.get("id", "")).split(".")[0] == bid and it.get("k") == "shot"]
    return min(fs) if fs else None


def turn_frame(l1, index, film):
    """The frame the Score named as the film's turn: a beat id or a word. None when it named none."""
    m = (film or {}).get("music")
    ref = str((m or {}).get("turn") or "").strip() if isinstance(m, dict) else ""
    if not ref:
        return None
    if ref.lstrip("w").isdigit() and ref.startswith("w"):
        return _frame_of_word(l1, index, int(ref[1:]))
    return _beat_frame(l1, ref.split(".")[0])


def facts(l1, index, film, treat, meter, about=""):
    """What the chooser reads of the film: its kind, length, turns, pace, how much of it is speech, one line."""
    fps = float(l1["fps"])
    dur = l1["dur_f"] / fps
    words = (index or {}).get("words") or []
    spoken = 0.0
    for s in l1.get("spine") or []:
        a, z = s["src"]
        spoken += sum(max(0.0, min(float(w.get("t1") or 0), z) - max(float(w.get("t0") or 0), a)) for w in words)
    share = min(1.0, spoken / max(dur, 1e-3))
    np10 = float((meter or {}).get("new_pictures10") or 0.0)
    cuts10 = float((meter or {}).get("cuts10") or np10)
    kind = "marketing" if share < 0.15 else "story" if np10 >= 3.0 else "podcast"
    turns = []
    shots = sorted((it for it in l1.get("items") or [] if it.get("k") == "shot"), key=lambda it: it["f"][0])
    beats = []
    for it in shots:
        b = str(it.get("id", "")).split(".")[0]
        if b and b not in beats:
            beats.append(b)
    if len(beats) > 1:
        f1 = _beat_frame(l1, beats[1])
        if f1:
            turns.append({"t": round(f1 / fps, 2), "role": "hook_end"})
    tf = turn_frame(l1, index, film)
    if tf is not None:
        turns.append({"t": round(tf / fps, 2), "role": "turn"})
    if len(beats) > 2:
        fl = _beat_frame(l1, beats[-1])
        if fl:
            turns.append({"t": round(fl / fps, 2), "role": "payoff"})
    m = (film or {}).get("music")
    feel = str(m.get("feel") or "")[:160] if isinstance(m, dict) else ""
    brief = " ".join(x for x in (str((treat or {}).get("story") or "")[:240], str(about or "")[:240], ("The director asks for: " + feel) if feel else "") if x)
    return {"kind": kind, "duration_s": round(dur, 2), "turns": turns, "pace": {"cuts10": round(cuts10, 2), "wps": round(len(words) / max(dur, 1e-3), 2)},
            "speech_share": round(share, 3), "brief": brief}, (tf / fps if tf is not None else None)


def _section_lufs(track, a, b):
    """The loudness of one stretch of a track, from its measured whole and its level curve (one number a second)."""
    lufs = track.get("lufs")
    curve = [float(v) for v in track.get("curve") or []]
    if lufs is None:
        return None
    if len(curve) < 4:
        return float(lufs)
    seg = curve[max(0, int(a)):max(int(a) + 1, int(math.ceil(b)))] or curve

    def power(xs):
        return 10 * math.log10(sum(10 ** (x / 10.0) for x in xs) / len(xs))
    return float(lufs) + power(seg) - power(curve)


def _pick_call(model, deadline):
    """music_choose's `call(system, user, schema) -> dict`, on the film's own metered stream."""
    from . import call

    def ask(system, user, schema):
        r = call.stream(model, system + ' Answer with one JSON object and nothing else: {"option": number, "why": "one sentence"}.',
                        [{"type": "text", "text": user}], max_tokens=600, effort="low", purpose="director_bed", deadline=deadline, thinking=False)
        t = r.text or ""
        return json.loads(t[t.find("{"):t.rfind("}") + 1])
    return ask


def settle(work, comp, index, score_lines, about=""):
    """The bed for the compiled film: the stretch of the chosen track that fits it, its level against the voice and
    the turn it drops out on, written to bed.json. Returns the record, or None when the film has none.
    score_lines: the Score's parsed lines (for the film and treat lines)."""
    doc = _read(work)
    if not doc.get("on") or not doc.get("cid"):
        return None
    film = next((o for o in score_lines if o.get("k") == "film"), {})
    treat = next((o for o in score_lines if o.get("k") == "treat"), {})
    try:
        sup = ((comp.meter.get("taste") or {}).get("supply") or {})
        if refused(film):
            raise ValueError("the Score keeps this film dry")
        if sup.get("own_bed"):
            raise ValueError("the upload carries its own track")
        import music_choose
        f, turn_s = facts(comp.l1, index, film, treat, comp.meter, about)
        if f["duration_s"] < MIN_S:
            raise ValueError(f"under {MIN_S:g} s: no room for a bed")
        track = music_choose.find(doc["cid"])
        if not track:
            raise ValueError("the chosen track left the library")
        prof = (music_choose.profiles() or {}).get(music_choose.kind_of(f["kind"])) or {}
        secs = music_choose.best_section(track, f, prof, top=1)
        sec = secs[0] if secs else {"start_s": float(doc.get("offset_s") or 0.0), "loop": False, "align": [], "why": []}
        a = float(sec.get("start_s") or 0.0)
        lead = f["speech_share"] < 0.15
        lufs = _section_lufs(track, a, a + f["duration_s"])
        want = LEAD_LUFS if lead else VOICE_LUFS - UNDER_VOICE.get(f["kind"], 9.0)
        gain = round(max(-40.0, min(want - (lufs if lufs is not None else -14.0), 6.0)), 1)
        doc.update({"settled": True, "kind": f["kind"], "lead": lead, "gain_db": gain, "section_lufs": None if lufs is None else round(lufs, 1),
                    "offset_s": round(a, 2), "loop": bool(sec.get("loop")), "turn_s": None if turn_s is None else round(turn_s, 2),
                    "dur_s": f["duration_s"], "align": sec.get("align"), "section_why": sec.get("why"), "film": f})
        _write(work, doc)
        return doc
    except Exception as e:  # noqa: BLE001 - the film goes out without a bed, and says why
        _write(work, {"on": False, "why": f"{type(e).__name__}: {str(e)[:200]}", "cid": doc.get("cid")})
        return None


def chosen(work):
    d = _read(work)
    return d if d.get("on") and d.get("cid") and d.get("settled") else None


def unset(work, why):
    """The film goes out dry after all (the track did not reach storage in time)."""
    d = _read(work)
    _write(work, {"on": False, "why": str(why)[:200], "cid": d.get("cid")})


def fetch_library(track):
    """The track's audio into storage at its library key, on a thread: fetched from its publisher once, checked
    against the sha256 measured when it joined the library (music_choose.ensure_library_object)."""
    import threading
    import music_choose
    th = threading.Thread(target=lambda: music_choose.ensure_library_object(track), name="film-bed-fetch", daemon=True)
    th.start()
    return th


def project_asset(ctx, worker_db, doc):
    """(storage key, error): the chosen track as a music asset of this project, registered with its licence and
    credit and granted to the write (music_gate), so the customer sees it and can remove it."""
    import music_choose
    import music_gate

    class _C:
        project_id = ctx.project_id
        db = worker_db
    key, err = music_choose.ensure_project_asset(_C, {"cid": doc["cid"]})
    if err or not key:
        return None, err or "no key"
    music_gate.grant(ctx, key, PROVENANCE)
    return key, None


def local_key(doc):
    return f"bed:{doc['cid']}"


def items(work, prog_s):
    """(music items, fx items) for the EDL, on the programme clock; ([], []) when the film has no chosen bed.
    The storage key is local (`bed:<cid>`): score.film swaps it for the project's asset before the write."""
    doc = chosen(work)
    if not doc or prog_s < MIN_S:
        return [], []
    purpose = (f"Valmera's choice for this film: \"{doc.get('title')}\" by {doc.get('author')} ({doc.get('license')}). {doc.get('why') or ''}")[:400]
    m = {"id": "bed", "storage_key": local_key(doc), "start": 0.0, "end": round(prog_s, 2), "gain_db": float(doc["gain_db"]), "duck": False,
         "fade_in_s": FADE_IN_S, "fade_out_s": FADE_OUT_S, "purpose": purpose, "provenance": PROVENANCE}
    if doc.get("offset_s"):
        m["offset_s"] = float(doc["offset_s"])
    if doc.get("loop"):
        m["loop"] = True
    fx = []
    t = doc.get("turn_s")
    if t is not None and 2.5 <= float(t) <= prog_s - 1.5:
        fx.append({"id": "bed.drop", "type": "arrange", "stage": "music_bus", "kind": "hole", "music_id": "bed",
                   "start": round(float(t) - DROP_S, 2), "end": round(float(t) - 0.03, 2), "fade_ms": DROP_FADE_MS})
    return [m], fx
