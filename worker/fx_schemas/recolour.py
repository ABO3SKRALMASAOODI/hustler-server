"""A colour replacement confined to an explicitly selected tracked object."""
from typing import ClassVar, Literal

from pydantic import Field

from . import FxItemBase


class RecolourFx(FxItemBase):
    STAGES: ClassVar[tuple] = ('world',)
    COMPILER: ClassVar[str] = 'object_recolour:COMPILER'
    type: Literal['recolour'] = 'recolour'
    object_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    from_colour: str = Field(pattern=r'^#[0-9a-fA-F]{6}$')
    colour: str = Field(pattern=r'^#[0-9a-fA-F]{6}$')
    tolerance: float = Field(default=.06, ge=.01, le=.15, allow_inf_nan=False)
    intensity: float = Field(default=1., ge=0., le=1., allow_inf_nan=False)

    def check(self, ctx):
        if ctx.canvas:
            raise ValueError('object recolouring needs footage')
        target = next((x for x in ctx.edl.fx or []
                       if x.id == self.object_id and x.type == 'object'), None)
        if target is None:
            raise ValueError('recolour names an existing selected object by object_id')
        if (self.start is None or self.end is None or target.start is None
                or target.end is None or self.start < target.start - 1e-6
                or self.end > target.end + 1e-6):
            raise ValueError('recolour must stay inside its tracked object window '
                             '(at most 6 seconds; select the next window separately)')

    def describe(self):
        return f'{super().describe()} object {self.object_id}: {self.from_colour} -> {self.colour}'


FX_MEMBERS = (RecolourFx,)
