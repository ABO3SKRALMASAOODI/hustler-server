"""Craft for pictures baked into one upload (picture_map.py measures them).

  photo_fill  a tall photo in a wide frame (or a wide one in a tall frame)
              over a blurred, enlarged copy of itself instead of bars.
              Its spans are SOURCE seconds, one per picture, with the
              rectangle the picture really fills; the renderer draws it in
              the picture's own block, so a transition, a zoom, a grade and
              a caption all read the filled picture.
  filmstrip   over [start, end) the pictures run past horizontally, at least
              two in view, each its own frame from the source, whole, in its
              own aspect and colour. Compiled by photo_craft in stage world.
"""

from typing import ClassVar, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from . import FxItemBase

TRACK = "photo"
FILL_BLUR = (0.01, 0.2)
MAX_SPANS = 4000
MAX_PHOTOS = 240


class PictureRect(BaseModel):
    """[x0, y0, x1, y1]: the part of the source frame that is picture."""
    x0: float = Field(ge=0, le=1, allow_inf_nan=False)
    y0: float = Field(ge=0, le=1, allow_inf_nan=False)
    x1: float = Field(ge=0, le=1, allow_inf_nan=False)
    y1: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def _ordered(self):
        if self.x1 - self.x0 < 0.02 or self.y1 - self.y0 < 0.02:
            raise ValueError("a picture rectangle needs an area")
        return self


class FillSpan(BaseModel):
    """One picture: source seconds [t0, t1) and where it sits."""
    t0: float = Field(ge=0, allow_inf_nan=False)
    t1: float = Field(gt=0, allow_inf_nan=False)
    rect: PictureRect
    # Nothing in the picture moves: the renderer composes one frame of it.
    still: Optional[bool] = None

    @model_validator(mode="after")
    def _span(self):
        if self.t1 <= self.t0:
            raise ValueError("a fill span ends after it starts")
        return self


class PhotoFillFx(FxItemBase):
    STAGES: ClassVar[tuple] = ("world",)
    COMPILER: ClassVar[str] = "photo_craft:FILL"

    type: Literal["photo_fill"] = "photo_fill"
    stage: Literal["world"] = "world"
    spans: List[FillSpan] = Field(min_length=1, max_length=MAX_SPANS)
    # Blur radius and how far the copy is dimmed, as shares of the frame.
    blur: float = Field(default=0.05, ge=FILL_BLUR[0], le=FILL_BLUR[1],
                        allow_inf_nan=False)
    dim: float = Field(default=0.2, ge=0, le=0.9, allow_inf_nan=False)

    def check(self, ctx):
        if self.start is None or self.end is None or self.end <= self.start:
            raise ValueError(f"fx {self.id}: photo_fill needs start < end")

    def describe(self):
        return (f"photo_fill {self.id}: {len(self.spans)} picture(s) on a "
                f"blurred copy of themselves, {self.start:g}-{self.end:g}s")


class FilmstripPhoto(BaseModel):
    """A picture of the strip: its frame's SOURCE second and rectangle."""
    t: float = Field(ge=0, allow_inf_nan=False)
    rect: Optional[PictureRect] = None


class FilmstripFx(FxItemBase):
    STAGES: ClassVar[tuple] = ("world",)
    COMPILER: ClassVar[str] = "photo_craft:FILMSTRIP"

    type: Literal["filmstrip"] = "filmstrip"
    stage: Literal["world"] = "world"
    photos: List[FilmstripPhoto] = Field(min_length=2,
                                         max_length=MAX_PHOTOS)
    direction: Literal["left", "right"] = "left"
    height: float = Field(default=0.78, ge=0.3, le=1.0, allow_inf_nan=False)
    gap: float = Field(default=0.02, ge=0, le=0.2, allow_inf_nan=False)
    backdrop: Literal["blur", "black"] = "blur"
    fade_s: float = Field(default=0.5, ge=0, le=2, allow_inf_nan=False)
    # A stitched piece holds part of the run: [start, end] of the whole run
    # on the piece's clock, so its pictures sit where the full render has
    # them. Absent: the item's own window.
    run: Optional[List[float]] = Field(default=None, min_length=2,
                                       max_length=2)

    def check(self, ctx):
        if self.start is None or self.end is None or self.end <= self.start:
            raise ValueError(f"fx {self.id}: a filmstrip needs start < end")
        if self.run is None and self.end - self.start < 1.0 \
                and not ctx.render_fragment:
            raise ValueError(f"fx {self.id}: a filmstrip runs at least 1 s")

    def describe(self):
        return (f"filmstrip {self.id}: {len(self.photos)} pictures running "
                f"{self.direction}, {self.start:g}-{self.end:g}s")


FX_MEMBERS = (PhotoFillFx, FilmstripFx)
