"""Cinematic B-roll: EDL.fx tag ``broll`` (track 4C; worker/devices/
INTERFACES_W4.md §3.5).

A full-frame cutaway to a sourced clip or photo over [start, end), drawn at
the ``screen`` stage (after the camera, before overlays and captions: the
speech and its captions continue over it). The item carries everything the
picture needs and everything the law needs:

* ``source``   the project asset (an FxSource of kind ``asset``; a clip from
               ``offset_s`` into it, ``loop`` when the window outlasts it)
* ``fit``      ``cover`` (crop to fill, aimed at ``focus``), ``contain``
               (letterboxed on black) or ``frame`` (archival: the whole
               picture on a soft, darkened copy of itself)
* ``move``     a still's camera path (2.5D through its measured depth, or
               Ken Burns when there is none — a photo never sits dead)
* ``match``    the de-stocking grade: a .cube matched to the hero frame
               (``asset_key``), plus ``grain`` and ``soften`` so stock sits
               in the same world as the camera
* ``credit``   the licence ledger line: provider, title, author, licence,
               the page it came from. Required: a cutaway with no licence
               record is refused.

Like every fx_schemas member this imports pydantic, the standard library and
its own package only (the backend loads schemas.py by path).
"""

from typing import ClassVar, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from . import FxItemBase, FxSource, live

TRACK = "4c"
# Track 4C: broll_fx.py compiles the type.
LIVE = True

# A cutaway shorter than this over the full frame reads as a flash cut.
FLASH_WINDOW_S = 0.5
# Licences a cutaway may carry. NC and ND are refused (a short is
# commercial and the de-stocking grade is a modification).
LICENCE_OK = ("public domain", "cc0", "pdm", "cc by", "cc by-sa",
              "pexels", "pixabay", "nasa", "us gov", "unsplash")
LICENCE_REFUSED = ("-nc", " nc", "noncommercial", "non-commercial", "-nd",
                   " nd", "noderiv", "no deriv", "all rights reserved")


class BrollMove(BaseModel):
    """A still's camera path (2.5D through depth, or Ken Burns)."""
    path: Literal["push_in", "pull_out", "drift_left", "drift_right",
                  "rise", "fall", "orbit_left", "orbit_right"] = "push_in"
    amount: float = Field(default=0.3, ge=0.05, le=1.0, allow_inf_nan=False)
    ease: Literal["linear", "in_out", "out"] = "in_out"


class BrollMatch(BaseModel):
    """The de-stocking grade: a LUT matched to the hero frame."""
    asset_key: Optional[str] = Field(default=None, min_length=1,
                                     max_length=400)
    strength: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    hero_t: Optional[float] = Field(default=None, ge=0.0,
                                    allow_inf_nan=False)
    de_before: Optional[float] = Field(default=None, ge=0.0, le=200.0,
                                       allow_inf_nan=False)
    de_after: Optional[float] = Field(default=None, ge=0.0, le=200.0,
                                      allow_inf_nan=False)


class BrollCredit(BaseModel):
    """One licence-ledger line (what the export's credits read)."""
    provider: str = Field(min_length=1, max_length=40)
    license: str = Field(min_length=1, max_length=80)
    title: Optional[str] = Field(default=None, max_length=200)
    author: Optional[str] = Field(default=None, max_length=200)
    license_url: Optional[str] = Field(default=None, max_length=400)
    page_url: Optional[str] = Field(default=None, max_length=600)
    source_url: Optional[str] = Field(default=None, max_length=800)
    attribution_required: bool = False
    line: Optional[str] = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def _licence(self):
        low = f" {self.license.lower()} "
        if any(x in low for x in LICENCE_REFUSED):
            raise ValueError(f"licence '{self.license}' forbids commercial "
                             "use or modification (NC/ND): not a cutaway")
        if self.attribution_required and not (self.author or self.line):
            raise ValueError("this licence requires attribution: credit "
                             "needs author or line")
        return self


def licence_ok(text):
    """Is a licence name one a cutaway may carry?"""
    low = f" {str(text or '').lower()} "
    if any(x in low for x in LICENCE_REFUSED):
        return False
    return any(x in low for x in LICENCE_OK)


class BrollFx(FxItemBase):
    """A full-frame cutaway (see the module doc)."""
    STAGES: ClassVar[tuple] = ("screen",)
    COMPILER: ClassVar[str] = "broll_fx:BROLL"

    type: Literal["broll"] = "broll"
    source: FxSource
    media: Literal["video", "still"] = "video"
    fit: Literal["cover", "contain", "frame"] = "cover"
    focus_x: float = Field(default=0.5, ge=0.0, le=1.0, allow_inf_nan=False)
    focus_y: float = Field(default=0.5, ge=0.0, le=1.0, allow_inf_nan=False)
    move: Optional[BrollMove] = None
    depth: bool = True
    match: Optional[BrollMatch] = None
    grain: Optional[float] = Field(default=None, ge=0.0, le=1.0,
                                   allow_inf_nan=False)
    soften: Optional[float] = Field(default=None, ge=0.0, le=1.0,
                                    allow_inf_nan=False)
    credit: BrollCredit
    moment: Optional[str] = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def _fields(self):
        if self.source.kind != "asset":
            raise ValueError("a cutaway's source is a project asset "
                             "(source.kind 'asset')")
        if self.media == "video" and self.move is not None:
            raise ValueError("move belongs to a still (a clip keeps its own "
                             "camera)")
        if self.media == "still" and self.source.loop:
            raise ValueError("loop belongs to a clip")
        return self

    def check(self, ctx):
        if self.start is None or self.end is None:
            raise ValueError(f"fx {self.id}: a cutaway needs start and end")
        if self.end - self.start < 0.2:
            raise ValueError(f"fx {self.id}: a cutaway under 0.2 s is a "
                             "flash, not B-roll")

    def flash_times(self):
        if self.start is None or self.end is None:
            return []
        if (self.end - self.start) < FLASH_WINDOW_S:
            return [self.start]
        return []

    def describe(self):
        bits = [self.media, self.fit, self.credit.provider]
        if self.move is not None:
            bits.append(f"{self.move.path} {self.move.amount:g}")
        if self.match is not None:
            bits.append("matched")
        return f"{super().describe()} ({', '.join(bits)})"


FX_MEMBERS = (BrollFx,) if live(LIVE, TRACK) else ()
