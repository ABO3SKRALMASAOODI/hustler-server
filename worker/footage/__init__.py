"""Footage: real video of the actual people, places and events a film names (lane footage, 13 Nov 2026).

Why. The references are cut from real video: interviews, launches, news, archive, a new shot every
1-3 s (week-1112/lanes/footage/study: 54 references read shot by shot). Our films were stills and
generic stock, one portrait held for 25 s. This package finds the footage a script's lines need.

How, for each line of a script (one call: ``run.find``):
  plan     a model reads the lines and the named things and writes the searches: YouTube (interviews,
           launches, documentaries, news) and archive.org (newsreels, public-domain film)  plan.py
  search   yt-dlp's search, and archive.org's advanced search                            search.py
  rank     a model keeps the sources most likely to hold the real thing, cleanly        plan.py
  fetch    a 360p proxy of each kept source through the product's YouTube ladder (PO tokens,
           player clients, the residential fallback routes in config)                    fetch.py
  hear     the proxy's speech, transcribed on OpenAI (gpt-4o-transcribe) in short pieces   hear.py
  shots    the proxy's shot changes (ffmpeg's scene score)                               shots.py
  look     a model reads the transcripts for the moments that speak of each line; frames from
           those moments and from the source's shots go on numbered sheets, and a vision check
           (gpt-6-sol, low effort) keeps only frames that plainly show what the line says   look.py
  identity for a named person, the same face on every clip: photographs the product's picture check
           passed as that person anchor it (SFace embeddings, OpenCV); a host or a colleague is dropped  identity.py
  cut      1-3 s inside one shot, from the source's best stream (<=1080p), reframed to the film,
           graded to one look (signal stats, never a guess)                              cut.py
Every clip records its source URL, channel, title and time range for the film's credits.

Rights. YouTube and news footage is not licensed to us. For customers the engine sits behind the
switch in ``switch.py``, OFF: turning it on is the owner's decision. The workflows (the owner's own
films, never published by the shell) use it through ``seam``.

Spend. OpenAI only: gpt-4o-transcribe ($0.006 a minute heard) and gpt-6-sol at low effort. The
OpenAI credit expires 30 Nov 2026; after that every call is paid OpenAI money.
"""
