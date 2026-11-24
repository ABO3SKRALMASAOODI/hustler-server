"""Finish an accepted private podcast take from its branded master, before the parent seals."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile

import brand
import chain
import delivered
from wf1_storage import _swap_dirs, write_json


def score_copy(clean, output, doc, used, scratch):
    """Use the licensed audition and the podcast mix on the clock of the retained picture."""
    import numpy as np
    import rescore_music

    chain.use_music_set()
    os.environ.setdefault("FFMPEG_BIN", brand.FF)
    brand._renderer()
    import music_film
    from scene import audio
    from score import audibility, music

    checks = doc["checks"]
    finish = checks["finish"]
    picture = brand.probe(clean)
    fps = picture["fps"]
    duration = picture["frames"] / fps
    programme = round(checks["programme_s"] * fps) / fps
    sr = brand.SR
    boundary = round(programme * sr)
    whole = round(duration * sr)
    dry = brand._pcm(clean, end=whole, total=whole)
    take = dry[:boundary]
    cues = checks.get("music_cues") or {"payoff_s": checks.get("stories", {}).get("turn_s")}
    measured = checks["podcast"].get("measured") or {}
    marks = dict(dur_s=programme, words=music.voice_spans(take),
                 cuts=[a for a, b in measured.get("holds", []) if 0 < a < programme],
                 turn_s=cues.get("turn_s"), payoff_s=cues.get("payoff_s"), turn_drop=cues.get("payoff_s") is not None)
    script = doc["script"]
    brief = " ".join(str(script.get(k) or "") for k in ("title", "hook_line", "direction"))
    facts = music_film.facts("podcast", marks, brief=brief)
    profile = music.profile_for("podcast")
    choice, track, tried = music_film.choose(facts, marks, str(scratch), used=used, profile=profile)
    if not choice:
        raise RuntimeError(f"Licensed music audition could not finish: {tried}")
    marks = music_film.arranged(marks, choice)
    song = music.analyse(track, sung=choice.get("sung"))
    plan = music.arrange(song, marks, profile=profile)
    bed, ride = music.track(track, song, plan, marks, voice=take, profile=profile)
    mix = {}
    music.podcast_bed(bed, take, marks, song, plan, track, mix)

    # A staged master has already been levelled with its effects. Keep the
    # ordinary broadband dialogue margin on that master without lifting a quiet bed.
    calibrated, change_db = audibility.calibrate_bed(take, bed, profile["broadband_bed_db"])
    if change_db < 0:
        bed = calibrated

    # Only the music gets a new closing envelope. The clean take already contains
    # the finished speaker, sting, watermark transition and spoken brand.
    last_word = (finish.get("tail") or {}).get("last_word_s")
    fall = brand.tail_start(programme, last_word)
    bed *= brand.tail_gain(np.arange(boundary) / sr, fall, programme)[:, None]
    overlap = min(round(.20 * sr), boundary)
    ref = brand._k_db(take[-round(brand.CLOSE_S * sr):])
    phrase = brand.card_music(mix["card_bed"], overlap, whole - boundary, ref if ref >= -40 else -18.)
    start = boundary - overlap
    voice = finish.get("voice") or {}
    at = round(voice.get("at_s", programme + brand.CARD_LINE_AT) * sr) - start
    end = (round(voice["end_s"] * sr) - start if "end_s" in voice else at + len(brand._pcm(brand.CARD_LINE)))
    phrase *= brand.card_voice_duck(len(phrase), at, end)
    mixed = dry.copy()
    mixed[:boundary] += bed
    mixed[start:] += phrase
    soundtrack = str(scratch / "score.m4a")
    audio.master(None, mixed, soundtrack, str(scratch), duration)
    audio.mux(str(clean), soundtrack, str(output))

    # The checked watermark and card are in this exact video stream already.
    if brand.probe(output) != picture or rescore_music.video_md5(output) != rescore_music.video_md5(clean):
        raise RuntimeError("Scored remux changed the staged picture or timing")
    balance = audibility.verified_mix(take, np.zeros(boundary), mixed[:boundary] - take,
                                      brand._pcm(output, end=boundary))
    levels = brand.encoded_levels(output)
    card = brand.encoded_card_score(output, clean, programme, duration - programme)
    tail = brand.tail_shape(output, programme, last_word, duration - programme)
    card.update({k: tail[k] for k in ("card_last250_dbfs", "card_quarters_dbfs")})
    return dict(choice, picked_by_valmera=True, film="film.mp4", clean_export="film_clean.mp4", tried=tried,
                checks=dict(levels, length_ok=True, endcard_ok=checks["endcard_ok"], mark_ok=checks["mark_ok"],
                            loud_ok=-18 <= levels["lufs"] <= -10 and levels["true_peak_dbtp"] <= -1),
                does=dict(marks=dict(cues, dur_s=programme), drop_at=plan.get("drop_at"), end_hit_s=plan["end_hit_s"],
                          under_voice_db=ride["under_voice_db"], encoded_card_bed=card,
                          encoded_balance={k: balance[k] for k in ("bed_under_voice_db", "fit_residual_db", "lag_ms")}))


def sync_path(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def recover(run, record):
    if not record or not record.get("ok") or run.state.get("delivered") or Path(run.p("deliver.json")).exists():
        return
    folder = Path(record["folder"]).resolve()
    if folder.parent != Path(run.p("films")).resolve() or delivered.in_delivery_root(str(folder)):
        return
    staged = folder.with_name("." + folder.name + ".music-finish")
    receipt = folder / "film.json"
    doc = json.loads(receipt.read_text())
    rules = doc.get("rules") or {}
    if not staged.exists() and (rules.get("music_picked") is not False or doc.get("music") or doc.get("song")
                                or (folder / "film_clean.mp4").exists()):
        return
    ledger = Path(run.p("music_used.json"))
    with open(str(ledger) + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = json.loads(receipt.read_text())
        used = json.loads(ledger.read_text()) if ledger.exists() else []
        if not doc.get("music"):
            run.log(f"{record['id']} finishing music from the staged branded master")
            # Same-volume directory exchange keeps both versions intact across termination.
            if staged.exists():
                shutil.rmtree(staged)
            shutil.copytree(folder, staged, copy_function=os.link)
            clean = staged / "film_clean.mp4"
            (staged / "film.mp4").rename(clean)
            with tempfile.TemporaryDirectory(dir=staged) as scratch:
                doc["music"] = score_copy(clean, staged / "film.mp4", doc, used, Path(scratch))
            doc["rules"]["music_picked"] = True
            if doc.get("platform_music"):
                doc["platform_music"]["upload"] = clean.name
                write_json(staged / "platform_music.json", doc["platform_music"])
            write_json(staged / "film.json", doc)
            sync_path(staged / "film.mp4")
            sync_path(staged)
            if not _swap_dirs(staged, folder):
                raise OSError(f"Could not atomically exchange the private staging folder {folder}")
            sync_path(folder.parent)
        # If the process stopped after the exchange, the receipt has the chosen id
        # and the sibling still holds the original. Complete the ledger first.
        cid = doc["music"]["cid"]
        if cid not in used:
            write_json(ledger, used + [cid])
            sync_path(ledger.parent)
        if staged.exists():
            shutil.rmtree(staged)
