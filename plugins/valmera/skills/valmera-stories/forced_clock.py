"""Force the spoken English script through a CTC acoustic model on the finished narration."""
from collections import defaultdict
import hashlib
import json
import os
import tempfile
import unicodedata
from pathlib import Path


def align(wav, segments, word_file):
    import soundfile
    import torch
    import torchaudio
    from torchaudio.functional import forced_align, merge_tokens, resample

    wav, word_file = Path(wav), Path(word_file)
    doc = json.loads(word_file.read_text())
    tokens = [(i, word) for i, segment in enumerate(segments) for word in segment["text"].split()]
    key = hashlib.sha256(wav.read_bytes() + json.dumps(tokens).encode()).hexdigest()
    if doc.get("alignment", {}).get("sha256") == key:
        return doc
    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    alphabet = {ch: i for i, ch in enumerate(bundle.get_labels())}
    targets, slices = [], []
    for i, word in tokens:
        folded = unicodedata.normalize("NFKD", word.upper().replace("’", "'"))
        letters = [alphabet[ch] for ch in folded if ch in alphabet and ch not in "-|"]
        if targets and letters:
            targets.append(alphabet["|"])
        start = len(targets)
        targets.extend(letters)
        slices.append((start, len(targets)))
    audio, rate = soundfile.read(wav, dtype="float32", always_2d=True)
    audio = resample(torch.from_numpy(audio.mean(axis=1)), rate, bundle.sample_rate)
    # Keep the model inside this run's temporary directory. All windows share the same 20 ms lattice.
    cache = os.environ.get("VALMERA_CTC_MODELS") or str(word_file.parent / ".ctc")
    with torch.inference_mode():
        model = bundle.get_model(dl_kwargs={"model_dir": cache}).eval()
        stride, window, context = 320, 16000 * 16, 16000
        emissions = []
        for start in range(0, len(audio), window):
            left = max(0, start - context)
            end = min(start + window, len(audio))
            probs, _ = model(audio[None, left:min(len(audio), end + context)])
            emissions.append(probs[:, (start-left)//stride:(end-left+stride-1)//stride])
        emission = torch.cat(emissions, dim=1).log_softmax(dim=-1)
        path, scores = forced_align(emission, torch.tensor([targets], dtype=torch.int32))
        aligned = merge_tokens(path[0], scores[0])
    words = []
    previous = defaultdict(list)
    for row in doc.get("words") or []:
        previous[row.get("seg", 0)].append(row)
    positions = defaultdict(int)
    for (seg, word), (a, b) in zip(tokens, slices):
        if a == b:  # punctuation-only tokens have no audible extent
            t0 = t1 = words[-1]["end"] if words else 0.
        else:
            t0 = aligned[a].start * stride / bundle.sample_rate
            t1 = aligned[b-1].end * stride / bundle.sample_rate
        pos = positions[seg]
        positions[seg] += 1
        old = previous[seg]
        meta = old[pos] if pos < len(old) and old[pos]["w"] == word else {}
        if old and "chunk" not in meta:
            meta = dict(chunk=old[0].get("chunk", seg))
        words.append(dict(meta, w=word, seg=seg, start=round(t0, 4), end=round(t1, 4)))
    cursor = 0
    phrases = []
    for phrase in doc.get("phrases") or []:
        count = len(phrase.get("text", "").split())
        members = words[cursor:cursor + count]
        if members and [r["w"] for r in members] == phrase["text"].split():
            phrases.append(dict(phrase, start=members[0]["start"], end=members[-1]["end"]))
            cursor += count
        else:
            phrases = []
            break
    if not phrases:
        for i, segment in enumerate(segments):
            members = [row for row in words if row["seg"] == i]
            if members:
                phrases.append(dict(seg=i, text=segment["text"], start=members[0]["start"], end=members[-1]["end"]))
    doc["phrases"] = phrases
    doc.update(words=words, duration=len(audio)/bundle.sample_rate,
               alignment=dict(model="wav2vec2-base-960h", sha256=key, frame_s=stride/bundle.sample_rate))
    with tempfile.NamedTemporaryFile("w", dir=word_file.parent, delete=False) as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(f.name, word_file)
    return doc


if __name__ == "__main__":
    import sys
    align(sys.argv[1], json.loads(Path(sys.argv[2]).read_text())["segments"], sys.argv[3])
