"""What the engine raises. One class, so the render branch catches one thing and falls back.

A fault never reaches a customer as a failure: the caller renders the same EDL on today's renderer in
the same job (DESIGN 4.12) and files a ticket with ``code`` and ``detail``.
"""

CODES = ("ineligible",    # the plan holds something this engine does not draw: decided before any work
         "plan",          # the plan broke its own contract (strict mode): a missing picture, a font, a gap
         "gl",            # no GL context, or a shader that does not compile on this driver
         "reader",        # a source that cannot be decoded
         "encode",        # ffmpeg refused the frames
         "segment",       # a rendered segment failed its check: frames, length, black
         "stall",         # the watchdog: no frame and no reader progress
         "crash",         # the render process died
         "audio")


class SceneFault(Exception):
    def __init__(self, code, detail="", **data):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code if code in CODES else "crash"
        self.detail = str(detail)[:2000]
        self.data = data

    def as_dict(self):
        return {"code": self.code, "detail": self.detail, **self.data}
