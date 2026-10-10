"""Content-addressed persistence for motion-graphic clips.

WAVE 0 INTERFACE STUB — owned by track 0A (storage), wired into
motion_engine by track 0B; pinned in worker/devices/INTERFACES.md §2.4.

motion_engine keys every rendered clip by a content hash of its document,
runtime and capture settings, and today keeps it only in a container-local
3 GB LRU. This store adds the shared tier: a cold interactive container
fetches the clip instead of starting Chromium (AT-0A-2).

Contract: best effort, never raises, never blocks a render on the network
for long. ``get`` returns None on any miss or error; ``put`` returns False on
any error. The R2 object is ``object_key(key)`` with a JSON sidecar
``<object_key>.json`` holding the capture box meta motion_engine stores
locally; R2 lifecycle expires both 90 days after last use (ROADMAP §8).
"""

PREFIX = "motion-clips"


def object_key(key):
    return f"{PREFIX}/{key[:2]}/{key}.mov"


def get(key, dest_dir):
    """(local .mov path, meta dict) for a stored clip, or None. STUB."""
    return None


def put(key, mov_path, meta):
    """Persist a rendered clip and its meta. True when stored. STUB."""
    return False
