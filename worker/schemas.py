"""Typed EDL + index schemas.

All timestamps everywhere are SECONDS as floats. The EDL is pure data — the
agent edits it through validated tools, the renderer turns it into ffmpeg
filtergraphs. A TypeScript mirror of the EDL type lives in the frontend repo
at src/types/edl.ts — keep the two in sync.
"""

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field

MIN_SPAN_S = 0.05
GAIN_MIN_DB = -60.0
GAIN_MAX_DB = 12.0


class EDLValidationError(ValueError):
    """Raised with a short, instructive, model-readable message."""


def _r(t):
    return round(float(t), 2)


# ------------------------------------------------------------------ #
#  EDL                                                                 #
# ------------------------------------------------------------------ #

class CaptionItem(BaseModel):
    text: str
    start: float   # source-timeline seconds
    end: float


class CaptionsFromTranscript(BaseModel):
    mode: Literal["from_transcript"] = "from_transcript"
    style: str = "default"


class MusicItem(BaseModel):
    storage_key: str
    # Music is new content with no source-time meaning, so start/end are
    # positions in the OUTPUT (edited) timeline. Documented in the tool spec.
    start: float
    end: float
    gain_db: float = -18.0
    duck: bool = True


class VolumeItem(BaseModel):
    start: float   # source-timeline seconds
    end: float
    gain_db: float


class EDL(BaseModel):
    keep: List[List[float]]
    captions: Optional[Union[CaptionsFromTranscript, List[CaptionItem]]] = None
    music: List[MusicItem] = Field(default_factory=list)
    volume: List[VolumeItem] = Field(default_factory=list)


def default_edl(duration):
    return EDL(keep=[[0.0, _r(duration)]]).model_dump()


def output_duration(keep):
    return round(sum(e - s for s, e in keep), 2)


def _check_span(name, s, e, max_end, min_len=MIN_SPAN_S):
    if s < 0 or e < 0:
        raise EDLValidationError(f"{name}: negative time ({s}, {e}). "
                                 "Times are seconds from 0.")
    if e - s < min_len:
        raise EDLValidationError(
            f"{name}: span [{s}, {e}] is shorter than {min_len}s.")
    if max_end is not None and e > max_end + 0.01:
        raise EDLValidationError(
            f"{name}: end {e} exceeds the limit {round(max_end, 2)}s.")


def validate_edl(data, duration):
    """Parse + validate an EDL dict against the video duration.

    Returns a normalized EDL (times rounded to 0.01s). Raises
    EDLValidationError with a message the agent can act on.
    """
    try:
        edl = EDL.model_validate(data)
    except Exception as e:
        raise EDLValidationError(f"EDL shape invalid: {str(e)[:300]}")

    if not edl.keep:
        raise EDLValidationError("keep must contain at least one [start, end] span.")

    keep = []
    for i, span in enumerate(edl.keep):
        if len(span) != 2:
            raise EDLValidationError(
                f"keep[{i}] must be [start, end], got {span}.")
        s, e = _r(span[0]), _r(span[1])
        _check_span(f"keep[{i}]", s, e, duration)
        keep.append([s, e])

    keep.sort(key=lambda x: x[0])
    for i in range(1, len(keep)):
        if keep[i][0] < keep[i - 1][1] - 0.001:
            raise EDLValidationError(
                f"keep segments overlap: [{keep[i-1][0]}, {keep[i-1][1]}] and "
                f"[{keep[i][0]}, {keep[i][1]}]. Segments must be sorted and "
                "non-overlapping.")
    edl.keep = keep
    out_dur = output_duration(keep)

    if isinstance(edl.captions, list):
        norm = []
        for i, c in enumerate(edl.captions):
            s, e = _r(c.start), _r(c.end)
            _check_span(f"captions[{i}]", s, e, duration)
            if not c.text.strip():
                raise EDLValidationError(f"captions[{i}] has empty text.")
            norm.append(CaptionItem(text=c.text.strip(), start=s, end=e))
        edl.captions = norm

    for i, m in enumerate(edl.music):
        m.start, m.end = _r(m.start), _r(m.end)
        # music positions live in the output timeline
        _check_span(f"music[{i}]", m.start, m.end, out_dur)
        if not (GAIN_MIN_DB <= m.gain_db <= GAIN_MAX_DB):
            raise EDLValidationError(
                f"music[{i}].gain_db {m.gain_db} outside "
                f"[{GAIN_MIN_DB}, {GAIN_MAX_DB}].")

    for i, v in enumerate(edl.volume):
        v.start, v.end = _r(v.start), _r(v.end)
        _check_span(f"volume[{i}]", v.start, v.end, duration)
        if not (GAIN_MIN_DB <= v.gain_db <= GAIN_MAX_DB):
            raise EDLValidationError(
                f"volume[{i}].gain_db {v.gain_db} outside "
                f"[{GAIN_MIN_DB}, {GAIN_MAX_DB}].")

    return edl


def describe_edl(edl_dict, duration=None):
    """One-line human summary used in diffs and activity messages."""
    edl = EDL.model_validate(edl_dict)
    parts = [f"{len(edl.keep)} segment{'s' if len(edl.keep) != 1 else ''}",
             f"{output_duration(edl.keep)}s kept"]
    if duration:
        parts[-1] += f" of {round(duration, 1)}s"
    if isinstance(edl.captions, CaptionsFromTranscript):
        parts.append("captions: transcript")
    elif isinstance(edl.captions, list):
        parts.append(f"captions: {len(edl.captions)} manual")
    if edl.music:
        parts.append(f"music x{len(edl.music)}")
    if edl.volume:
        parts.append(f"volume x{len(edl.volume)}")
    return ", ".join(parts)


# ------------------------------------------------------------------ #
#  Index                                                               #
# ------------------------------------------------------------------ #

class Word(BaseModel):
    w: str
    t0: float
    t1: float


class Sentence(BaseModel):
    id: str          # "s1", "s2", ...
    text: str
    t0: float
    t1: float
    wi0: int         # index into words[]
    wi1: int         # inclusive


class ShotCaption(BaseModel):
    setting: str = ""
    people: str = ""
    action: str = ""
    on_screen_text: str = ""


class Shot(BaseModel):
    id: int
    start: float
    end: float
    thumb_key: Optional[str] = None
    caption: Optional[ShotCaption] = None


class VideoInfo(BaseModel):
    duration: float
    fps: float
    width: int
    height: int
    has_audio: bool
    vfr_normalized: bool = False


class VideoIndex(BaseModel):
    version: int = 1
    video: VideoInfo
    shots: List[Shot] = Field(default_factory=list)
    words: List[Word] = Field(default_factory=list)
    sentences: List[Sentence] = Field(default_factory=list)
    silences: List[List[float]] = Field(default_factory=list)
    sheet_keys: List[str] = Field(default_factory=list)
