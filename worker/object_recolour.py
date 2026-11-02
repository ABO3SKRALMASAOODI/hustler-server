"""Selective hue replacement through an object's measured program-space mask.

There is deliberately no unmasked substitute. Missing tracking leaves the
picture untouched and reports an omitted effect instead of grading the scene.
The LUT preserves shade/detail and leaves nonmatching colours unchanged.
Brightness keys allow black, white and gray surfaces to acquire colour.
"""
import colorsys
import hashlib
import json
import os

import finish
import gradewin
import world_defs


def _rgb(colour):
    return tuple(int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))


def recolour_rgb(rgb, from_colour, colour, tolerance=.06):
    """One RGB sample in [0,1]; used to bake the bounded 3D lookup table."""
    if from_colour.lower() == colour.lower():
        return tuple(rgb)
    source_h, source_s, source_v = colorsys.rgb_to_hsv(*_rgb(from_colour))
    target_h, target_s, target_v = colorsys.rgb_to_hsv(*_rgb(colour))
    h, s, v = colorsys.rgb_to_hsv(*rgb)
    if source_s < .15:
        # Neutrals have no useful hue to rotate. Colourise matching neutral
        # brightness inside the selected surface, preserving its shade order.
        # Bright windows and dark tires are outside a mid-gray key; coloured
        # lights are outside its saturation key. A precise surface selection
        # is still needed when neighbouring parts have the same colour.
        weight = max(0., min(1., (.3 - s) / .15))
        if source_v >= .8:
            weight *= max(0., min(1., (v - .15) / .25))
        elif source_v <= .2:
            weight *= 1 - max(0., min(1., (v - .35) / .3))
        else:
            weight *= max(0., min(1., (.4 - abs(v - source_v)) / .15))
        value = (max(.12, target_v) * (1 - (1 - v) ** (1 / .12))
                 if source_v < .15 else
                 min(1., v * max(.12, target_v) / source_v))
        changed = colorsys.hsv_to_rgb(target_h, target_s, value)
        return tuple(a + weight * (b - a) for a, b in zip(rgb, changed))
    distance = abs((h - source_h + .5) % 1 - .5)
    # A soft hue edge and chroma floor protect windows, tires, whites and
    # grays within a whole-object segmentation. The object mask protects
    # similarly coloured pixels elsewhere in the scene.
    weight = max(0., min(1., (float(tolerance) + .02 - distance) / .02))
    weight *= max(0., min(1., (s - .12) / .18))
    if not weight:
        return tuple(rgb)
    changed = colorsys.hsv_to_rgb(
        (h + target_h - source_h) % 1,
        min(1., s * target_s / max(.15, source_s)),
        min(1., v * max(.12, target_v) / max(.12, source_v)))
    return tuple(a + weight * (b - a) for a, b in zip(rgb, changed))


def write_cube(workdir, item, size=33):
    settings = {k: item.get(k) for k in ('from_colour', 'colour', 'tolerance')}
    tag = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:16]
    path = os.path.join(workdir, f'recolour_{tag}.cube')
    if os.path.exists(path):
        return path
    with open(path, 'w') as output:
        output.write(f'LUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
        for b in range(size):
            for g in range(size):
                for r in range(size):
                    value = recolour_rgb((r / (size - 1), g / (size - 1), b / (size - 1)),
                                         item['from_colour'], item['colour'],
                                         item.get('tolerance', .06))
                    output.write(' '.join(f'{x:.7f}' for x in value) + '\n')
    return path


class _Compiler:
    FILTERS = ('lut3d', 'lut', 'format', 'scale', 'split', 'trim', 'setpts', 'settb', 'tpad',
               'alphamerge', 'overlay', 'null', 'nullsink')
    KEEPS_FRAME_TAGS = True

    def prepare(self, plan, item):
        if not plan.workdir or not plan.W or not plan.H:
            return ('omit', 'no geometry/workdir for object recolouring')
        target = next((x for x in (plan.edl or {}).get('fx') or []
                       if x.get('id') == item['object_id'] and x.get('type') == 'object'), None)
        if target is None:
            return ('omit', 'selected object is missing; no scene-wide substitute')
        if item['object_id'] not in (getattr(plan, '_w3_objects', None) or {}):
            offset = getattr(getattr(plan, 'pm', None), 'clock_offset', 0.) or 0.
            if world_defs.status(target, plan.tl, plan.source, offset) != 'ready':
                return ('omit', 'object tracking is not ready; recolouring has not been applied')
            world_defs._precompute_object(plan, target, offset)
        if item['object_id'] not in (getattr(plan, '_w3_objects', None) or {}):
            return ('omit', 'object mask could not be built; recolouring has not been applied')
        if not hasattr(plan, '_recolour_luts'):
            plan._recolour_luts = {}
        plan._recolour_luts[item['id']] = write_cube(plan.workdir, item)

    def stage(self, ctx, items):
        return finish.emit_each(ctx, items, self._emit)

    def _emit(self, ctx, label, item):
        mask = ctx.mask('object:' + item['object_id'], 'world')
        if mask is None:
            ctx.note(item, 'omit', 'object mask is unavailable; recolouring has not been applied')
            return label
        path = ctx.plan._recolour_luts[item['id']]
        def build(parts, source, uid):
            out = uid + 'recolour'
            parts.append(f'[{source}]{gradewin.RGB_IN},'
                         f"lut3d=file='{gradewin._escape(path)}':interp=tetrahedral,"
                         f'{gradewin.YUV_OUT}[{out}]')
            return out
        return finish.window_composite(ctx, label, [ctx.frames(item)], build,
                                       mask=mask, opacity=item.get('intensity', 1.))

    def cost_ms(self, plan, item):
        return 2.5

    def landing(self, plan, item):
        return []


COMPILER = _Compiler()
