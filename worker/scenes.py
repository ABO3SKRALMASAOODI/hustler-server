"""Shot boundaries via PySceneDetect ContentDetector, run on the 720p proxy."""

import config
from schemas import Shot


def detect_shots(proxy_path, duration):
    try:
        from scenedetect import detect, ContentDetector
        scene_list = detect(proxy_path,
                            ContentDetector(threshold=config.SCENE_THRESHOLD))
    except Exception:
        scene_list = []

    shots = []
    for i, (start, end) in enumerate(scene_list, start=1):
        s, e = start.get_seconds(), end.get_seconds()
        if e - s < 0.05:
            continue
        shots.append(Shot(id=i, start=round(s, 2), end=round(e, 2)))

    if not shots:
        shots = [Shot(id=1, start=0.0, end=round(duration, 2))]
    else:
        shots[-1].end = max(shots[-1].end, round(duration, 2))
    return shots
