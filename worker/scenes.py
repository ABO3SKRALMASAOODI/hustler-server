"""Shot boundaries via PySceneDetect ContentDetector, run on the 720p proxy."""

import config
from schemas import Shot


def detect_shots(proxy_path, duration, warnings=None):
    """Returns the shot list. When scene detection crashes (missing dep,
    corrupt proxy) it degrades to one full-length shot — but no longer
    silently: the failure is logged and, when a `warnings` list is passed,
    recorded there so a degraded index is visible in admin."""
    try:
        from scenedetect import detect, ContentDetector
        scene_list = detect(proxy_path,
                            ContentDetector(threshold=config.SCENE_THRESHOLD))
    except Exception as e:
        print(f"[scenes] detection failed, degrading to one shot: {e}",
              flush=True)
        if warnings is not None:
            warnings.append(f"shot detection failed ({str(e)[:120]}) — the "
                            "whole video is treated as a single shot")
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
