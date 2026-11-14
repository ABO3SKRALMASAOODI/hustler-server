"""Bounded uploads of completed render artifacts, joined before scratch cleanup."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import time

import storage


class ArtifactUploads:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.futures = []
        self.keys = []
        self.seconds = {}

    def submit(self, path, key, mime, stage=None):
        def upload():
            started = time.monotonic()
            storage.upload_file(path, key, mime)
            self.keys.append(key)
            if stage:
                self.seconds[stage] = round(time.monotonic() - started, 3)
        # I/O accounting belongs to this job even on the upload thread.
        self.futures.append(self.pool.submit(copy_context().run, upload))

    def wait(self):
        # Observe every result before raising, including a late success after
        # an earlier failure. The caller can safely remove exactly its keys.
        error = None
        for future in self.futures:
            try:
                future.result()
            except Exception as exc:
                if error is None:
                    error = exc
        if error is not None:
            raise error

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
