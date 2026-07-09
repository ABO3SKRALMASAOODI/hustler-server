"""Typed EDL + index schemas.

All timestamps everywhere are SECONDS as floats. The EDL is pure data — the
agent edits it through validated tools, the renderer turns it into ffmpeg
filtergraphs. A TypeScript mirror of the EDL type lives in the frontend repo
at src/types/edl.ts — keep the two in sync.
"""

import json
import re
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

MIN_SPAN_S = 0.05
GAIN_MIN_DB = -60.0
GAIN_MAX_DB = 12.0
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_WORDS_PER_CAPTION = 12


class EDLValidationError(ValueError):
    """Raised with a short, instructive, model-readable message."""


def _r(t):
    return round(float(t), 2)


# ------------------------------------------------------------------ #
#  EDL                                                                 #
# ------------------------------------------------------------------ #

class CaptionStyle(BaseModel):
    """Burn style. color is #RRGGBB; the renderer converts it to the .ass
    &HBBGGRR order. Defaults match the pre-style captions exactly, so EDLs
    written before styling existed render unchanged."""
    color: str = "#FFFFFF"
    size: Literal["s", "m", "l", "xl"] = "m"
    position: Literal["bottom", "top", "middle"] = "bottom"
    # word-by-word pop captions; Optional so pre-round-7 EDLs keep their
    # signatures (None-valued keys are stripped by edl_signature).
    dynamic: Optional[bool] = None

    @field_validator("color")
    @classmethod
    def _color_hex(cls, v):
        v = (v or "").strip()
        if not HEX_COLOR.match(v):
            raise ValueError(
                f"color '{v}' must be #RRGGBB hex, e.g. #FF0000 for red")
        return v.upper()


def _coerce_style(v):
    # Legacy EDLs stored style as the string "default" — treat any string
    # as "use defaults" instead of failing to load old versions.
    if isinstance(v, str) or v == {}:
        return None
    return v


class CaptionItem(BaseModel):
    text: str
    start: float   # source-timeline seconds
    end: float
    style: Optional[CaptionStyle] = None   # per-item override

    _style = field_validator("style", mode="before")(_coerce_style)


class CaptionsFromTranscript(BaseModel):
    mode: Literal["from_transcript"] = "from_transcript"
    # Chunk word-timed captions into groups of at most N words. Timing always
    # comes from the real word timestamps in the index — never invented.
    max_words_per_caption: Optional[int] = None
    style: Optional[CaptionStyle] = None

    _style = field_validator("style", mode="before")(_coerce_style)


class MusicItem(BaseModel):
    # id is optional so pre-round-6 EDLs (whose music items have none) stay
    # valid and signature-compatible; new items always get one.
    id: Optional[str] = None
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


FRAME_RATIOS = ("source", "16:9", "9:16", "1:1", "4:5")


class Frame(BaseModel):
    """Output frame. ratio 'source' keeps the original dimensions; anything
    else is achieved by crop (center-crop + scale), pad (fit + black bars) or
    pad_blur (fit over a blurred scaled copy). Never upscales beyond the
    source's pixel budget — see renderer.frame_dims."""
    ratio: Literal["source", "16:9", "9:16", "1:1", "4:5"] = "source"
    mode: Literal["crop", "pad", "pad_blur"] = "crop"


MAX_INSERT_DURATION_S = 600.0


class InsertItem(BaseModel):
    """A clip or image spliced into the program at a keep-segment boundary.
    at_output_s is a position in the PRE-INSERT output timeline (the keep
    list alone), so items are stable when other inserts change. duration_s is
    always concrete: the tool resolves it (image default 3.0s, video default
    the full clip length)."""
    id: str
    asset_key: str
    kind: Literal["video", "image"]
    at_output_s: float
    duration_s: float


class VoiceoverItem(BaseModel):
    """Audio laid over the program. start_output_s is a position in the
    FINAL program timeline (after inserts). duck_others lowers program audio
    12dB while the voiceover is active."""
    id: str
    asset_key: str
    start_output_s: float
    gain_db: float = 0.0
    duck_others: bool = True


class EDL(BaseModel):
    keep: List[List[float]]
    captions: Optional[Union[CaptionsFromTranscript, List[CaptionItem]]] = None
    music: List[MusicItem] = Field(default_factory=list)
    volume: List[VolumeItem] = Field(default_factory=list)
    frame: Optional[Frame] = None
    inserts: List[InsertItem] = Field(default_factory=list)
    voiceover: List[VoiceoverItem] = Field(default_factory=list)


def default_edl(duration):
    return EDL(keep=[[0.0, _r(duration)]]).model_dump()


def output_duration(keep):
    return round(sum(e - s for s, e in keep), 2)


def keep_boundaries(keep):
    """Output-time positions (pre-insert timeline) where a splice may sit:
    0, each segment join, and the end."""
    bounds, acc = [0.0], 0.0
    for s, e in keep:
        acc = round(acc + (e - s), 2)
        bounds.append(acc)
    return bounds


def program_duration(edl_dict):
    """Final program length: kept footage plus all spliced inserts."""
    dur = output_duration(edl_dict["keep"])
    for ins in edl_dict.get("inserts") or []:
        dur += float(ins["duration_s"])
    return round(dur, 2)


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
            norm.append(CaptionItem(text=c.text.strip(), start=s, end=e,
                                    style=c.style))
        edl.captions = norm
    elif isinstance(edl.captions, CaptionsFromTranscript):
        mw = edl.captions.max_words_per_caption
        if mw is not None:
            mw = int(mw)
            if not (1 <= mw <= MAX_WORDS_PER_CAPTION):
                raise EDLValidationError(
                    f"max_words_per_caption {mw} outside "
                    f"[1, {MAX_WORDS_PER_CAPTION}].")
            edl.captions.max_words_per_caption = mw

    # Frame: 'source' is the absence of a frame — normalize so old EDLs and
    # explicit-source EDLs compare identical.
    if edl.frame is not None and edl.frame.ratio == "source":
        edl.frame = None

    # Inserts: concrete durations, unique ids, and every splice point must
    # sit exactly on a keep boundary (the tools snap; this is the backstop).
    bounds = keep_boundaries(keep)
    seen_ids = set()
    for i, ins in enumerate(edl.inserts):
        ins.at_output_s = _r(ins.at_output_s)
        ins.duration_s = _r(ins.duration_s)
        if not ins.id or ins.id in seen_ids:
            raise EDLValidationError(
                f"inserts[{i}].id must be non-empty and unique.")
        seen_ids.add(ins.id)
        if not ins.asset_key:
            raise EDLValidationError(f"inserts[{i}].asset_key is empty.")
        if not (0.2 <= ins.duration_s <= MAX_INSERT_DURATION_S):
            raise EDLValidationError(
                f"inserts[{i}].duration_s {ins.duration_s} outside "
                f"[0.2, {MAX_INSERT_DURATION_S:.0f}].")
        nearest = min(bounds, key=lambda b: abs(b - ins.at_output_s))
        if abs(nearest - ins.at_output_s) > 0.02:
            raise EDLValidationError(
                f"inserts[{i}].at_output_s {ins.at_output_s} is not on a "
                f"keep-segment boundary — nearest boundary is {nearest}. "
                "Inserts splice BETWEEN kept segments (or at the start/end).")
        ins.at_output_s = nearest
    edl.inserts.sort(key=lambda x: x.at_output_s)

    prog_dur = out_dur + sum(x.duration_s for x in edl.inserts)

    seen_ids = set()
    for i, vo in enumerate(edl.voiceover):
        vo.start_output_s = _r(vo.start_output_s)
        if not vo.id or vo.id in seen_ids:
            raise EDLValidationError(
                f"voiceover[{i}].id must be non-empty and unique.")
        seen_ids.add(vo.id)
        if not vo.asset_key:
            raise EDLValidationError(f"voiceover[{i}].asset_key is empty.")
        if not (0 <= vo.start_output_s <= max(0.0, prog_dur - 0.05)):
            raise EDLValidationError(
                f"voiceover[{i}].start_output_s {vo.start_output_s} outside "
                f"the program (0 to {round(prog_dur, 2)}s).")
        if not (GAIN_MIN_DB <= vo.gain_db <= GAIN_MAX_DB):
            raise EDLValidationError(
                f"voiceover[{i}].gain_db {vo.gain_db} outside "
                f"[{GAIN_MIN_DB}, {GAIN_MAX_DB}].")

    seen_music_ids = set()
    for i, m in enumerate(edl.music):
        if m.id is not None:
            if not m.id or m.id in seen_music_ids:
                raise EDLValidationError(
                    f"music[{i}].id must be non-empty and unique.")
            seen_music_ids.add(m.id)
        m.start, m.end = _r(m.start), _r(m.end)
        # music positions live in the FINAL program timeline (incl. inserts)
        _check_span(f"music[{i}]", m.start, m.end, prog_dur)
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


def _sig_canon(v):
    # Nested None-valued keys are dropped too, so items written before an
    # optional field existed (e.g. music without 'id') compare equal to
    # re-validated dumps that carry the field as None.
    if isinstance(v, dict):
        return {k: _sig_canon(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_sig_canon(x) for x in v]
    return v


def edl_signature(edl_dict):
    """Canonical string form of an EDL for byte-identity comparison (no-op
    write detection). Assumes the dict is already validate_edl-normalized.
    Keys with empty values are dropped so EDLs written before a field existed
    (no 'frame'/'inserts' key) compare equal to fresh dumps that carry the
    field's empty default."""
    canon = {k: _sig_canon(v) for k, v in edl_dict.items()
             if v not in (None, [], {})}
    return json.dumps(canon, sort_keys=True, separators=(",", ":"))


def _style_desc(style):
    if not style:
        return ""
    s = style if isinstance(style, dict) else style.model_dump()
    bits = []
    if s.get("color") and s["color"] != "#FFFFFF":
        bits.append(s["color"])
    if s.get("size") and s["size"] != "m":
        bits.append(f"size {s['size']}")
    if s.get("position") and s["position"] != "bottom":
        bits.append(s["position"])
    return f" ({', '.join(bits)})" if bits else ""


def describe_edl(edl_dict, duration=None):
    """One-line human summary used in diffs and activity messages."""
    edl = EDL.model_validate(edl_dict)
    parts = [f"{len(edl.keep)} segment{'s' if len(edl.keep) != 1 else ''}",
             f"{output_duration(edl.keep)}s kept"]
    if duration:
        parts[-1] += f" of {round(duration, 1)}s"
    if isinstance(edl.captions, CaptionsFromTranscript):
        d = "captions: transcript"
        if edl.captions.max_words_per_caption:
            d += f" <= {edl.captions.max_words_per_caption} words"
        parts.append(d + _style_desc(edl.captions.style))
    elif isinstance(edl.captions, list):
        parts.append(f"captions: {len(edl.captions)} manual")
    if edl.frame:
        parts.append(f"frame {edl.frame.ratio} ({edl.frame.mode})")
    if edl.inserts:
        parts.append(f"inserts x{len(edl.inserts)} "
                     f"(+{round(sum(i.duration_s for i in edl.inserts), 1)}s)")
    if edl.voiceover:
        parts.append(f"voiceover x{len(edl.voiceover)}")
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
