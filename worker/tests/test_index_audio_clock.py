"""Real-media regression: ASR sample positions must share the renderer clock."""

import shutil
import subprocess
import wave

import numpy as np
import pytest

import media


pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="requires ffmpeg and ffprobe",
)
RATE = 16000


def ff(*args):
    return subprocess.run(["ffmpeg", "-y", "-v", "error", *map(str, args)],
                          check=True, capture_output=True).stdout


def wav_samples(path):
    with wave.open(str(path), "rb") as source:
        assert (source.getframerate(), source.getnchannels(),
                source.getsampwidth()) == (RATE, 1, 2)
        return np.frombuffer(source.readframes(source.getnframes()),
                             dtype="<i2").astype(float)


def decode_legacy(src, dst):
    ff("-i", src, "-vn", "-ac", "1", "-ar", RATE,
       "-c:a", "pcm_s16le", dst)
    return wav_samples(dst)


def rendered_window(src, start, duration):
    # The renderer trims by source PTS then resets the kept segment to zero.
    raw = ff("-i", src, "-af",
             f"atrim=start={start}:end={start+duration},asetpts=PTS-STARTPTS",
             "-ac", "1", "-ar", RATE, "-f", "s16le", "-")
    return np.frombuffer(raw, dtype="<i2").astype(float)


def best_lag(signal, reference, expected, radius=.4):
    low = max(0, round((expected-radius)*RATE))
    high = round((expected+radius)*RATE)+len(reference)
    hay = signal[low:high]
    n = 1 << (len(hay)+len(reference)-1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(hay, n) *
                        np.fft.rfft(reference[::-1], n), n)
    corr = corr[len(reference)-1:len(hay)]
    pos = int(np.argmax(corr))
    return (low+pos)/RATE-expected


def test_aac_concat_keeps_asr_and_render_clocks_aligned(tmp_path):
    part = tmp_path / "part.mp4"
    ff("-f", "lavfi", "-i", "color=s=64x64:r=30:d=1.03",
       "-f", "lavfi", "-i",
       "anoisesrc=color=pink:sample_rate=48000:duration=1.03:seed=19",
       "-t", "1.03", "-c:v", "libx264", "-c:a", "aac", part)
    listing = tmp_path / "parts.txt"
    listing.write_text("".join(f"file '{part}'\n" for _ in range(12)))
    source = tmp_path / "joined.mp4"
    ff("-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", source)
    legacy = decode_legacy(source, tmp_path / "old.wav")
    out = tmp_path / "new.wav"
    media.extract_wav(str(source), str(out))
    current = wav_samples(out)
    start = 10.8
    reference = rendered_window(source, start, .3)
    assert abs(best_lag(legacy, reference, start)) > .15
    assert abs(best_lag(current, reference, start)) < .003
    # Drift is cumulative, so check an earlier anchor independently too.
    reference = rendered_window(source, 2.4, .3)
    assert abs(best_lag(current, reference, 2.4)) < .003


@pytest.mark.parametrize("offset", [.4, -.04])
def test_initial_audio_offset_matches_media_timeline(tmp_path, offset):
    source = tmp_path / "offset.mkv"
    ff("-f", "lavfi", "-i", "color=s=64x64:r=25:d=2",
       "-f", "lavfi", "-i",
       "anoisesrc=color=pink:sample_rate=48000:duration=1.5:seed=7",
       "-filter:a", f"asetpts=PTS+({offset})/TB", "-c:v", "libx264",
       "-c:a", "pcm_s16le", "-avoid_negative_ts", "disabled", source)
    out = tmp_path / "offset.wav"
    media.extract_wav(str(source), str(out))
    samples = wav_samples(out)
    reference = rendered_window(source, .8, .3)
    assert abs(best_lag(samples, reference, .8)) < .003
    if offset > 0:
        assert np.max(np.abs(samples[:round((offset-.01)*RATE)])) == 0


@pytest.mark.parametrize("gap", [.25, -.05])
def test_internal_timestamp_discontinuity_matches_renderer(tmp_path, gap):
    source = tmp_path / "discontinuity.mkv"
    ff("-f", "lavfi", "-i",
       "anoisesrc=color=pink:sample_rate=48000:duration=3:seed=23",
       "-af", f"asetpts=PTS+if(gte(T\\,1)\\,{gap}/TB\\,0)",
       "-c:a", "pcm_s16le", source)
    out = tmp_path / "new.wav"
    media.extract_wav(str(source), str(out))
    samples = wav_samples(out)
    reference = rendered_window(source, 2, .3)
    assert abs(best_lag(samples, reference, 2)) < .003


def test_continuous_audio_keeps_identical_samples(tmp_path):
    source = tmp_path / "normal.wav"
    ff("-f", "lavfi", "-i",
       "anoisesrc=color=pink:sample_rate=48000:duration=2:seed=3", source)
    legacy = decode_legacy(source, tmp_path / "old.wav")
    out = tmp_path / "new.wav"
    media.extract_wav(str(source), str(out))
    assert np.array_equal(legacy, wav_samples(out))
