"""A forecast is a closed book. Its fore-edge portrait opens on the actual year."""

import cv2
import numpy as np

from . import material as m


def leaves(years):
    start, forecast, actual = years
    return forecast - start, actual - start


def plan(l1, work, ask):
    fps, (w, h) = l1["fps"], l1["frame"]
    mention, forecast, actual, result = ask["beats"]
    count, elapsed = leaves(ask["years"])
    if not 0 < elapsed < count:
        raise ValueError("The two dates must bound the earlier-than-forecast event")
    a = max(0, mention)
    z = min(l1["dur_f"], result + round(0.52 * fps))
    cover, future = m.moments(l1, work, [mention, result])
    stock = m.paper((h, w), (33, 43, 41))
    # Parallel leaves: each page has a narrow face on which a different column
    # of the future exposure is printed. Together their edges carry its portrait.
    corners = np.float32(
        [[0.20 * w, 0.36 * h], [0.79 * w, 0.28 * h], [0.84 * w, 0.67 * h], [0.25 * w, 0.80 * h]]
    )
    upper = np.float32([[-0.06 * w, -0.16 * h], [-0.06 * w, -0.16 * h]])
    source = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])

    def surface(dst, picture, quad):
        mat = cv2.getPerspectiveTransform(source, np.float32(quad))
        warped = cv2.warpPerspective(picture, mat, (w, h), flags=cv2.INTER_LINEAR)
        mask = np.zeros((h, w), np.uint8)
        cv2.fillConvexPoly(mask, np.int32(quad), 255, cv2.LINE_AA)
        cv2.copyTo(warped, mask, dst)

    def pictures():
        for f, live in zip(range(a, z), m.take_frames(l1, work, a, z)):
            canvas = stock.copy()
            q = corners.copy()
            shadow = np.zeros((h, w), np.uint8)
            cv2.fillConvexPoly(shadow, np.int32(q + [w * 0.009, h * 0.025]), 150)
            shadow = cv2.GaussianBlur(shadow, (0, 0), w * 0.009)
            canvas = np.uint8(canvas * (1 - shadow[:, :, None] / 370))
            emerge = m.ease((f - forecast) / (0.42 * fps))
            # The promise is a gilt closed edge; the prediction's own portrait
            # resolves across the ends as "century" is spoken.
            edge = m.mix(np.full_like(future, (184, 150, 94)), future, 0.10 + 0.90 * emerge)
            surface(canvas, edge, q)
            opening = m.ease((f - actual) / (0.85 * fps))
            for j in range(count + 1):
                u = j / count
                top = q[0] * (1 - u) + q[1] * u
                bottom = q[3] * (1 - u) + q[2] * u
                cv2.line(canvas, np.int32(top), np.int32(bottom), (94, 80, 60), 1, cv2.LINE_AA)
            surface(canvas, cover, [q[0] + upper[0], q[1] + upper[1], q[1], q[0]])
            # Only elapsed pages turn; each moves on its own hinge. The last
            # leaf expands from that same hinge to become the incoming exposure.
            for j in range(elapsed):
                u = (j + 1) / count
                top = q[0] * (1 - u) + q[1] * u
                bot = q[3] * (1 - u) + q[2] * u
                p = float(m.ease(opening * 2 - j / max(1, elapsed - 1)))
                if p > 0:
                    lift = np.array([-0.17 * w * p, -0.19 * h * np.sin(p * np.pi * 0.55)])
                    quad = np.float32([top + lift, top, bot, bot + lift])
                    cv2.fillConvexPoly(
                        canvas, np.int32(quad), (222 - j % 4 * 4, 209 - j % 4 * 3, 178), cv2.LINE_AA
                    )
                    cv2.polylines(canvas, [np.int32(quad)], True, (133, 108, 76), 1, cv2.LINE_AA)
            open_full = float(m.ease((f - (result - round(0.82 * fps))) / (0.83 * fps)))
            if open_full > 0:
                u = elapsed / count
                top = q[0] * (1 - u) + q[1] * u
                bot = q[3] * (1 - u) + q[2] * u
                start = np.float32([top + [-0.17 * w, -0.19 * h], top, bot, bot + [-0.17 * w, -0.19 * h]])
                surface(canvas, live, start * (1 - open_full) + source * open_full)
            yield m.mix(live, canvas, m.ease((f - a + 1) / (0.25 * fps)))

    voices = {"foreedge.year": m.serif(h, 0.13)}
    items = []
    for i, (f, e, y) in enumerate(
        zip([a, forecast, actual], [forecast, actual, result - round(0.82 * fps)], ask["years"])
    ):
        items.append(m.word(f"{ask['id']}.{i}", str(y), "foreedge.year", f, e, 0.075, 0.92, "#F8E9CA"))
    return m.plate(
        l1,
        work,
        ask,
        a,
        z,
        pictures(),
        {
            "accent_frame": actual,
            "pages": count,
            "turned": elapsed,
            "cover_exposure": mention,
            "edge_exposure": result,
        },
        items,
        voices,
    )
