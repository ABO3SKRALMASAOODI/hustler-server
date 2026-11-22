"""Cast a spoken moat around the island of the speaker's actual profile."""

from pathlib import Path

import cv2
import numpy as np

from . import material as m


def head_island(frame, bounds):
    h, w = frame.shape[:2]
    x, y, bw, bh = bounds
    x0, y0 = max(0, int(x - 0.25 * bw)), max(0, int(y - 0.48 * bh))
    x1, y1 = min(w, int(x + 1.25 * bw)), min(h, int(y + 1.05 * bh))
    mask = np.full((h, w), cv2.GC_BGD, np.uint8)
    mask[y0:y1, x0:x1] = cv2.GC_PR_BGD
    center = (int(x + 0.5 * bw), int(y + 0.36 * bh))
    cv2.ellipse(mask, center, (int(0.54 * bw), int(0.65 * bh)), 0, 0, 360, cv2.GC_PR_FGD, -1)
    cv2.ellipse(mask, center, (int(0.28 * bw), int(0.32 * bh)), 0, 0, 360, cv2.GC_FGD, -1)
    model = np.zeros((1, 65), np.float64)
    cv2.grabCut(frame, mask, None, model.copy(), model.copy(), 3, cv2.GC_INIT_WITH_MASK)
    return (mask % 2).astype(np.uint8)


def terrain(frame, eyes, bounds):
    """A plaster terrain model. Separate the elevation map from the fluid's clock."""
    h, w = frame.shape[:2]
    island = head_island(frame, bounds)
    scale = 0.43 * h / bounds[3]
    centre = np.float32([0.59 * w, 0.52 * h])
    transform = np.float32([[scale, 0, centre[0] - eyes[0] * scale], [0, scale, centre[1] - eyes[1] * scale]])
    islands = cv2.warpAffine(island, transform, (w, h), flags=cv2.INTER_NEAREST)
    luminance = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    corners = cv2.cornerMinEigenVal(luminance, 7)
    channels = np.zeros_like(islands)
    satellites = []
    for roi in ((0.13, 0.40, 0.31, 0.74), (0.76, 0.24, 0.89, 0.67), (0.42, 0.13, 0.54, 0.34)):
        x0, y0, x1, y1 = np.int32(np.array(roi) * [w, h, w, h])
        _, _, _, (dx, dy) = cv2.minMaxLoc(corners[y0:y1, x0:x1])
        end = np.array([x0 + dx, y0 + dy])
        radius = h * 0.052
        # Use samples around the feature for the perimeter of its smaller island.
        arc = np.linspace(0, 2 * np.pi, 24, endpoint=False)
        dirs = np.column_stack((np.cos(arc), np.sin(arc)))
        pts = np.int32(end + radius * dirs)
        spectrum = luminance[pts[:, 1], pts[:, 0]].astype(np.float32) / 255
        cv2.fillConvexPoly(islands, np.int32(end + radius * dirs * (0.65 + 0.7 * spectrum[:, None])), 1)
        delta = end - centre
        bend = np.float32([-delta[1], delta[0]]) * 0.15
        t = np.linspace(0, 1, 60)[:, None]
        course = centre + t * delta + 4 * t * (1 - t) * bend
        cv2.polylines(channels, [np.int32(course)], False, 1, max(3, round(0.023 * h)), cv2.LINE_AA)
        satellites.append(end.tolist())
    outside = cv2.distanceTransform(1 - islands, cv2.DIST_L2, 5)
    inside = cv2.distanceTransform(islands, cv2.DIST_L2, 5)
    channel_d = cv2.distanceTransform(1 - channels, cv2.DIST_L2, 5)
    lagoon = np.clip(1 - abs(outside - 0.059 * h) / (0.042 * h), 0, 1)
    canal = np.clip(1 - channel_d / (0.010 * h), 0, 1) * (1 - islands)
    depth = np.maximum(lagoon, canal)
    # Cut terraces across the profile, with a tiny bevel on each riser.
    steps = np.floor(np.minimum(inside / h, 0.17) * 58)
    elevation = h * (0.0092 * steps + 0.032 * islands - 0.032 * depth)
    elevation = cv2.GaussianBlur(elevation.astype(np.float32), (0, 0), max(0.7, 0.0016 * w))
    y, x = np.indices((h, w), dtype=np.float32)
    # Ray cast a shallow oblique view: nearest terrain samples occlude the ones behind.
    plane_y = (y - 0.50 * h) / 0.78 + 0.50 * h
    map_y = plane_y.copy()
    for height in np.linspace(-0.034 * h, float(elevation.max()), 40):
        candidate = plane_y + height / 0.78
        hit = cv2.remap(elevation, x, candidate, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        map_y = np.where(hit >= height, candidate, map_y).astype(np.float32)

    def project(texture, exterior=0):
        return cv2.remap(
            texture, x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=exterior
        )

    gy, gx = np.gradient(elevation)
    diffuse = (0.94 - 0.42 * gx - 0.66 * gy) / np.sqrt(1 + gx * gx + gy * gy)
    # Cast the islands' shadows down into the channel in the same lighting direction.
    occlusion = np.zeros_like(elevation)
    for reach in (0.01, 0.025, 0.05, 0.085):
        obstacle = np.roll(elevation, (round(reach * h * 0.6), round(reach * h)), axis=(0, 1))
        occlusion = np.maximum(
            occlusion, np.clip((obstacle - elevation - reach * h * 0.36) / (0.035 * h), 0, 1)
        )
    lit = project(np.clip(0.62 + 0.39 * diffuse - 0.23 * occlusion, 0.36, 1.18), 1.0)
    stone = m.paper((h, w), (224, 214, 192)).astype(np.float32) * lit[:, :, None]
    # Warm edges on each cut layer, like unpainted end grain on an architect's model.
    stone += project(cv2.Laplacian(elevation, cv2.CV_32F))[:, :, None] * 6
    # Fresnel highlights follow the local bank; the studio's actual lights supply their variation.
    reflection = cv2.GaussianBlur(cv2.flip(frame, 0), (0, 0), 5).astype(np.float32)
    water = (np.float32([19, 83, 91]) + reflection * 0.19) * (0.64 + 0.44 * lit[:, :, None])
    radii = np.hypot(x - centre[0], map_y - centre[1]) / w
    return (
        np.clip(stone, 0, 255).astype(np.uint8),
        water,
        project(lagoon),
        project(canal),
        radii,
        project(outside) / h,
        centre,
        satellites,
    )


def plan(l1, work, ask):
    fps, (_w, h) = l1["fps"], l1["frame"]
    key, second, network = ask["beats"]
    z = min(l1["dur_f"], network + round(2.65 * fps))
    frame = m.moments(l1, work, [key])[0]
    eyes, _, bounds = m.face(frame, Path(ask["models"]) / "face_detection_yunet_2023mar.onnx")
    stone, water, lagoon, canal, radii, bank, centre, satellites = terrain(frame, eyes, bounds)
    # Work only on wet pixels while the scene camera travels over the whole model.
    wet = (lagoon + canal) > 0.01
    lg, ch, r, d = lagoon[wet], canal[wet], radii[wet], bank[wet]
    floor = stone[wet].astype(np.float32)
    pool = water[wet]

    def pictures():
        for f in range(key, z):
            t = (f - key) / fps
            # The first shoreline closes around the speaker. "Network" sends a
            # visible front through the dry paths to the other locations.
            outer = np.clip((t * 0.49 - r) * 23, 0, 1)
            connected = np.clip(((f - network) / fps * 0.33 - r + 0.07) * 21, 0, 1)
            local = np.clip((0.155 - r) * 40, 0, 1)
            fill = np.maximum(lg * np.maximum(local * outer, connected), ch * connected)
            glint = (0.5 + 0.5 * np.cos(280 * d - 4 * t + 4 * np.sin(r * 37 + t))) ** 8
            caustic = (glint * 0.7 + (0.5 + 0.5 * np.cos(121 * r + 117 * d - 1.8 * t)) ** 12 * 0.3) * 22
            colour = pool + caustic[:, None] * np.array([0.9, 1.15, 0.95], np.float32)
            out = stone.copy()
            out[wet] = np.uint8(np.clip(floor + (colour - floor) * fill[:, None], 0, 255))
            yield out

    voices = {"cast.type": m.serif(h, 0.083)}
    items = [
        m.word(
            ask["id"] + ".name", ask.get("line", "Moats"), "cast.type", key, second, 0.075, 0.88, "#28332E"
        ),
        m.word(
            ask["id"] + ".network",
            ask.get("next_line", "Network effects"),
            "cast.type",
            network,
            z,
            0.075,
            0.88,
            "#28332E",
        ),
    ]
    patch = m.plate(
        l1,
        work,
        ask,
        key,
        z,
        pictures(),
        {
            "accent_frame": network,
            "eye_xy": eyes.tolist(),
            "principal_island": centre.tolist(),
            "satellite_features": satellites,
        },
        items,
        voices,
    )
    shot = next(i for i in patch["items"] if i["id"] == ask["id"] + ".exposure")
    # A tight match cut to the first island, pulling gently back as the network fills.
    crops = []
    for f in range(key, z):
        p = float(m.ease((f - key) / (z - key)))
        size = 0.69 + 0.29 * p
        x0 = (1 - size) * (0.63 - 0.13 * p)
        y0 = (1 - size) * 0.43
        crops.append([x0, y0, x0 + size, y0 + size])
    shot["crop"] = crops
    return patch
