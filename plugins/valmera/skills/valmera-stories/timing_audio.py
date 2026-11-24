"""Assemble a timing rewrite from finished native generation groups on the same run."""
import difflib
import itertools
import json
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf


def load(folder, name):
    return json.loads((folder / name).read_text())


# The master is normalized to -14 LUFS. Below -45 dBFS its joins carry room tone.
QUIET_POWER = 10 ** (-45 / 10)


def quiet_head(audio, sr):
    """Number of quiet samples before speech, in 5 ms frames (also used on a reversed tail)."""
    frame = max(1, sr // 200)
    frames = audio[:len(audio) // frame * frame].reshape(-1, frame)
    on = np.flatnonzero(np.mean(frames ** 2, axis=1) >= QUIET_POWER)
    return int(on[0]) * frame if len(on) else len(audio)


def signatures(script, groups):
    # Member boundaries matter even when concatenated speech happens to be identical.
    # Stories sets inter-group pauses from the beat at the join, not the writer's pause hint.
    segments = [{k: v for k, v in s.items() if k != "pause_after"} for s in script["segments"]]
    return [json.dumps([segments[i] for i in g], sort_keys=True) for g in groups]


class Master:
    def __init__(self, folder):
        self.report = load(folder, "render.json")
        self.words = load(folder, "words.json")
        self.audio, self.sr = sf.read(folder / "narration.wav", dtype="float32")
        self.groups = [g["segments"] for g in self.report["stories_read"]["generations"]]
        self.rows = [[] for _ in self.groups]
        for word in self.words["words"]:
            self.rows[word["chunk"]].append(word)

    def cut(self, i):
        if i == 0:
            return 0
        if i == len(self.groups):
            return len(self.audio)
        # ASR word edges alone can fall inside a consonant. Keep both words and
        # cut inside the quiet core of the native join on the final PCM.
        lo = max(0, round(self.rows[i-1][-1]["end"] * self.sr))
        hi = min(len(self.audio), round(self.rows[i][0]["start"] * self.sr))
        n = self.sr // 100
        frames = self.audio[lo:hi][:max(0, (hi-lo)//n)*n].reshape(-1, n)
        if not len(frames):
            return None
        power = np.mean(frames ** 2, axis=1)
        quiet = np.flatnonzero(power < QUIET_POWER)
        if not len(quiet):
            return None
        center = self.report["boundaries"][i-1]["t"] * self.sr
        centers = lo + quiet * n + n//2
        return int(centers[np.argmin(abs(centers - center))])

    def chosen(self, i):
        line = self.report["lines"][i]
        return next(t for t in line["takes"] if t["file"] == line["kept"])


def remember(rd, source, i):
    take = source.chosen(i)
    ends = take.get("_ends")
    v = source.report["lines"][i].get("kept_voice")
    if v is None or ends is None:  # older receipts omitted drift, and a gate retake can lack pitch ends
        rows = source.rows[i]
        x = source.audio[int(rows[0]["start"]*source.sr):int(rows[-1]["end"]*source.sr)]
        if v is None:
            v = rd.PF.voice_of(x, source.sr, rows)
        if ends is None:
            ends = rd.ends(dict(_x=x, _sr=source.sr))
    rd.keep_contour(ends, take.get("cadence"))
    rd.prior_voices.append(v)


def reuse_map(old, old_script, script, groups, misheard):
    keys = signatures(old_script, old.groups)
    for i in misheard:
        keys[i] = None
    match = difflib.SequenceMatcher(None, keys, signatures(script, groups), autojunk=False)
    result = [None] * len(groups)
    for a, b, size in match.get_matching_blocks():
        # If no clean splice is available, include that neighbor in the new native read.
        while size and old.cut(a) is None:
            size, a, b = size-1, a+1, b+1
        while size and old.cut(a + size) is None:
            size -= 1
        result[b:b+size] = range(a, a+size)
    return result


def repair(previous, out, script, gs, groups, band, voice, rd, read_block):
    started = time.monotonic()
    try:
        check = load(previous, "check.json")
        old_script = load(previous, "narration_script.json")
        old = Master(previous)
        global_keys = (set(old_script) | set(script)) - {"title", "segments", "voice", "phonon_voice"}
        if (not check.get("gate") or old.report["engine"] != "lines:phonon" or old.report["voice"] != voice
                or old.report["model"] != rd.LN.phonon_source()[0] or old.report["band"] != band
                or any(old_script.get(k) != script.get(k) for k in global_keys)
                or rd.LN.MAX_TEMPO != old.report["stories_read"].get("max_tempo", 1.0)):
            return False
        expected = [" ".join(old_script["segments"][i]["text"] for i in g) for g in old.groups]
        if expected != [c["text"] for c in old.report["chunks"]] or any(
                [w["w"] for w in row] != text.split() for row, text in zip(old.rows, expected)):
            return False
        # Older receipts predate max_tempo. A non-unity ride proves they used a different setting.
        if "max_tempo" not in old.report["stories_read"] and any(line["tempo"] != 1 for line in old.report["lines"]):
            return False
        misheard = []
        if check["wer"]["wer"]:
            heard = check.get("heard_spoken") or check["heard"]
            alignment = rd.LN.seg_errors(old.report["chunks"], heard)
            if alignment is None:
                return False
            misheard = alignment["by_line"]
        matches = reuse_map(old, old_script, script, groups, misheard)
    except (OSError, sf.LibsndfileError, ValueError, IndexError, KeyError, TypeError):
        return False
    if all(m is None for m in matches):
        return False

    blocks = []
    with tempfile.TemporaryDirectory(dir=out, prefix="repair-") as tmp:
        def source_offset(item):
            index, old_index = item
            return None if old_index is None else old_index - index

        for offset, items in itertools.groupby(enumerate(matches), key=source_offset):
            span = list(items)
            start, end = span[0][0], span[-1][0] + 1
            if offset is not None:
                blocks.append((old, start + offset, end + offset))
                continue
            # The new takes compete in the register and cadence of the accepted lead-in.
            rd.reset_context()
            for source, first, last in blocks:
                for i in range(first, last):
                    remember(rd, source, i)
            folder = Path(tmp) / str(start)
            folder.mkdir()
            members = groups[start][0]
            local = dict(script, segments=script["segments"][members:groups[end-1][-1]+1])
            indices = [[j-members for j in g] for g in groups[start:end]]
            grouped = dict(gs, segments=[dict(s, members=g) for s, g in zip(gs["segments"][start:end], indices)])
            read_block(folder, local, indices, grouped, start)
            source = Master(folder)
            if source.report["voice"] != voice or source.sr != old.sr:
                return False
            blocks.append((source, 0, end-start))
        assemble(out, blocks, gs, groups, matches, old, check, started, rd.generated_takes)
    return True


def assemble(out, blocks, gs, groups, matches, old, check, started, generated_takes):
    import lines
    import master
    from measure import PAUSE_S
    from voice import lra
    sr = old.sr
    parts, words, phrases, cuts, bounds, chunks, picks, reports = [], [], [], [], [], [], [], []
    cursor = 0
    for source, first, last in blocks:
        a, b = source.cut(first), source.cut(last)
        audio = source.audio[a:b].copy()
        offset = len(chunks)
        member_ids = {}
        if parts:
            gap = gs["segments"][offset-1]["pause_after"]
            # Preserve the source tails. Fill only the missing room between
            # them, with a short fade at each cut to prevent a sample step.
            target = round(gap * sr)
            tail = quiet_head(parts[-1][-target:][::-1], sr)
            lead = quiet_head(audio[:target], sr)
            n = max(0, target - tail - lead)
            fade = min(sr//250, len(parts[-1]), len(audio))
            if fade:
                ramp = np.linspace(0, 1, fade, dtype=np.float32)
                parts[-1][-fade:] *= ramp[::-1]
                audio[:fade] *= ramp
            if n:
                parts.append(lines.room_tone(parts[-1], audio, sr, n/sr, seed=offset-1)[:n])
                cursor += len(parts[-1])
            join = round((cursor - (n + tail - lead) / 2) / sr, 3)
            bounds.append(dict(after_chunk=offset-1, t=join, pause_set=gap))
            first_word = (cursor - a) / sr + source.rows[first][0]["start"]
            if first_word - words[-1]["end"] >= PAUSE_S:
                cuts.append(round((first_word + words[-1]["end"])/2, 3))
        shift = (cursor-a)/sr
        def moved(t):
            return round(t + shift, 3)
        for i in range(first, last):
            j = len(chunks)
            segmap = dict(zip(source.groups[i], groups[j]))
            member_ids.update(segmap)
            rows = [dict(w, start=moved(w["start"]), end=moved(w["end"]), seg=segmap[w["seg"]], chunk=j)
                    for w in source.rows[i]]
            words.extend(rows)
            chunks.append(dict(seg=j, text=gs["segments"][j]["text"], start=rows[0]["start"], end=rows[-1]["end"]))
            reports.append(dict(source.report["lines"][i], line=j))
            picks.append(source.report["stories_read"]["picks"][i])
            if i+1 < last:
                boundary = source.report["boundaries"][i]
                bound = dict(boundary, after_chunk=j, t=moved(boundary["t"]))
                if "gap" in bound:
                    bound["gap"] = [moved(t) for t in bound["gap"]]
                bounds.append(bound)
        for phrase in source.words["phrases"]:
            if phrase["seg"] in member_ids:
                phrases.append(dict(phrase, seg=member_ids[phrase["seg"]], start=moved(phrase["start"]), end=moved(phrase["end"])))
        cuts.extend(moved(t) for t in source.words["cut_points"]
                    if source.rows[first][0]["start"] < t < source.rows[last-1][-1]["end"])
        parts.append(audio)
        cursor += len(audio)
    x = np.concatenate(parts)
    duration = round(len(x)/sr, 2)
    wj = dict(voice=old.words["voice"], duration=duration, words=words, cut_points=cuts, phrases=phrases)
    fresh = [i for i, m in enumerate(matches) if m is None]
    receipt = dict(reused_groups=matches, generated_groups=fresh, previous_gate=check["gate"],
                   generated_takes=generated_takes, evaluated_takes=sum(len(reports[i]["takes"]) for i in fresh))
    stories = dict(max_tempo=old.report["stories_read"].get("max_tempo", 1.0), generations=[dict(segments=g, text_words=len(c["text"].split())) for g,c in zip(groups,chunks)],
                   picks=picks, lufs=round(master.lufs(x,sr),2), lra=dict(after=round(lra(x,sr),2)), timing_repair=receipt)
    report = dict(engine=old.report["engine"], model=old.report["model"], band=old.report["band"], voice=old.report["voice"],
                  names=gs.get("names", []), chunks=chunks, lines=reports, boundaries=bounds, duration_s=duration,
                  master=dict(assembled_from_native_masters=True), lufs_target=old.report["lufs_target"],
                  stories_read=stories, timing=dict(total_s=round(time.monotonic()-started,1)))
    (out / "check.json").unlink(missing_ok=True)
    sf.write(out / "narration.wav", x, sr, subtype="PCM_24")
    (out / "render.json").write_text(json.dumps(report, indent=1))
    tmp = out / "words.tmp"
    tmp.write_text(json.dumps(wj, indent=1))
    tmp.replace(out / "words.json")
