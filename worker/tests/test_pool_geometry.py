from PIL import Image
from score.lower import Bake, lower_flashes


def test_pool_raster_scales_to_authored_bounds(tmp_path):
    bake = Bake(str(tmp_path))
    rects = [[0, .72, 1, .84], [0, .55, 1, .95]]
    items = [dict(k="flash", mode="pool", id=str(i), rect=r, a=.45,
                  f=[30 * i, 30 * (i + 1)]) for i, r in enumerate(rects)]
    _, _, layers = lower_flashes(dict(fps=30, frame=[1080, 1920], items=items), bake, {})
    for layer, rect in zip(layers, rects, strict=True):
        with Image.open(bake.locals[layer["asset_key"]]) as im:
            display_height = layer["scale"] * 1080 * im.height / im.width
            assert abs(display_height - (rect[3] - rect[1]) * 1920) < 2
        assert layer["opacity"] == .45
