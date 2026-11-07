"""The Director on today's renderer (Director DESIGN 2.7, 3.2-3.5, 8).

A model reads a customer's upload and writes a Score. The compiler turns the Score into exact
frames, the lowering turns those into ordinary EDL items, and today's renderer draws them. There
is no scene layer and no new engine here. Off unless app_kv.director says otherwise (score.flags).

The package imports nothing heavy at import time, so a job that never reaches it pays nothing.
"""
