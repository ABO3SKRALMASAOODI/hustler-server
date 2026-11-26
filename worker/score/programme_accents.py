"""Restore a missing workflow sound from the recorded visual event and its encoded picture."""
from pathlib import Path

from scipy.ndimage import maximum_filter1d, gaussian_filter1d

import cv2
import numpy as np

from . import sound


def retained_events(record):
    rendered = record.get("checks", {}).get("stories", {}).get("render") or {}
    if (rendered.get("audio") or {}).get("sounds") != 0:
        return []
    events = []
    for device in (record.get("devices") or {}).get("log") or []:
        if not (device.get("done") and device.get("landed")):
            continue
        pictures = {w["f"] for w in (device.get("log") or {}).get("words", [])
                    if isinstance(w, dict) and w.get("picture") and "f" in w}
        for mark in device.get("marks") or []:
            if mark.get("f") in pictures:
                events.append(dict(mark, dev=device["landed"], kind="picture", picture=True))
            elif mark.get("strength",0) >= sound.DEVICE_MIN:
                events.append(dict(mark, dev=device["landed"], seat=device.get("seat")))
    return events


def visible_event(path, events):
    """Find the strongest recorded landing with a visible change in its own frame region."""
    if not events:
        return None
    movie = cv2.VideoCapture(str(path))
    try:
        fps = movie.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            return None
        for event in sorted(events, key=lambda e: (not e.get("picture"), -float(e.get("strength", 0)))):
            if event.get("kind") in sound.DEVICE_QUIET or (not event.get("picture") and event.get("strength", 0) < sound.DEVICE_MIN):
                continue
            frame = int(event["f"])
            if frame < 3:
                continue
            movie.set(cv2.CAP_PROP_POS_FRAMES, frame - 3)
            images = []
            for _ in range(7):
                ok, image = movie.read()
                if not ok:
                    break
                h, w = image.shape[:2]
                box = event.get("seat")
                if box and all(k in box for k in ("x", "y", "w", "h")):
                    x, y = box["x"], box["y"]
                    image = image[max(0,int(y*h)):min(h,int((y+box["h"])*h)+1),
                                  max(0,int(x*w)):min(w,int((x+box["w"])*w)+1)]
                if not image.size:
                    break
                gray = cv2.cvtColor(cv2.resize(image, (128, 72)), cv2.COLOR_BGR2GRAY)
                images.append(gray.astype(np.float32))
            if len(images) != 7:
                continue
            # A hold and normal codec jitter produce little two-sided contrast.
            delta = float(np.percentile(np.abs(np.median(images[:3], axis=0) -
                                                np.median(images[4:], axis=0)), 96))
            if delta >= 10:
                return dict(event, at_s=frame/fps, visible_change=round(delta, 2))
    finally:
        movie.release()
    return None


def add(programme, path, events):
    """Seat one approved recording against the actual programme plus music before mastering."""
    event = visible_event(path, events)
    if not event:
        return None
    typed = event.get("kind") == "type"
    preferred = sound.STORY_SOUNDS["tick" if typed else "air"]
    fallback = "tick_1" if typed else "whoosh_soft_1"
    lib = sound.library()
    sid = next((k for k in (preferred, fallback)
                if k in lib and (Path(sound.SOUNDS) / lib[k]["file"]).is_file()), None)
    if not sid:
        return None
    rec = lib[sid]
    wave = sound._full_band(sid)
    if not len(wave):
        return None
    rate = sound.SR
    at = round(event["at_s"] * rate)
    peak = float(rec.get("hit_s") or rec.get("peak_s") or 0)
    start = at - round((peak + sound.LEAD_S) * rate)
    trim = max(0, -start)
    start = max(0, start)
    n = min(len(wave)-trim, len(programme)-start,
            round(float(rec.get("max_s") or len(wave)/rate)*rate))
    if n <= 0:
        return None
    effect = wave[trim:trim+n]
    window = max(1, round(.05*rate))
    def rms_level(data):
        return 10*np.log10(max(float(np.mean(np.square(data, dtype=np.float64))), 1e-12))
    energy = np.mean(np.square(effect, dtype=np.float64), axis=1)
    sums = np.r_[0, np.cumsum(energy)]
    win = min(window, n)
    offset = int(np.argmax(sums[win:]-sums[:-win]))
    local = slice(start+offset,start+offset+win)
    # Control isolated peaks on the effect stem so the final limiter needn't
    # turn down the entire mix on its attack.
    attack_rms = 10**(rms_level(effect[offset:offset+win])/20)
    ceiling = max(attack_rms*2.8, 1e-5)
    envelope = maximum_filter1d(np.max(np.abs(effect),axis=1), max(3,round(.002*rate)))
    reduction = gaussian_filter1d(np.minimum(1.,ceiling/np.maximum(envelope,1e-8)), max(1,round(.0004*rate)))
    effect = effect * reduction[:, None]
    neighbors = programme[max(0,at-round(.3*rate)):min(len(programme),at+round(.3*rate))]
    ref = np.clip(max(rms_level(programme[local]),rms_level(neighbors)), -31, -12)
    gain = min(18., ref + 7. - rms_level(effect[offset:offset+win]))
    pre = rms_level(programme[local])
    programme[start:start+n] += effect * 10**(gain/20)
    return dict(device=event.get("dev"), frame=event["f"], at_s=round(event["at_s"],3),
                visible_change=event["visible_change"], sound=sid, gain_db=round(float(gain),2),
                measurement_s=round((start+offset)/rate,5), measurement_duration_s=win/rate,
                transient_lift_db=round(float(rms_level(programme[local])-pre),2))
