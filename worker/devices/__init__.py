"""Device contract: every creative device is a card (ROADMAP A3).

WAVE 0 INTERFACE STUB — owned by track 0C (card schema validation, compiler,
resolver, ledger), pinned in worker/devices/INTERFACES.md §5. The constants
below ARE the card contract other tracks write cards against; 0C implements
``load_cards`` / ``resolve`` behind these signatures without renaming a
field.

Cards live in worker/devices/cards/<id>.yaml, one device per file, owned by
the track that ships the device.
"""

import os

CARDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cards")

# Required card fields, in the order a card is written.
CARD_REQUIRED = ("id", "family", "verb", "stage", "params", "needs",
                 "binds_to", "budget", "owner_flags", "rungs", "cost",
                 "exemplars", "verify", "status")
CARD_OPTIONAL = ("fx_type", "never", "description", "skill", "flag")

STATUSES = ("live", "beta", "dark")
INTENSITIES = ("calm", "designed", "charged")
OWNER_FLAGS = ("adds_sound", "adds_zoom", "flash")
# Rungs, best first. A card lists the subset it supports, always ending in
# "omit"; a substitute rung names the device it falls back to.
RUNGS = ("full", "reduced", "substitute", "omit")
FAMILIES = ("punctuation", "matte_fx", "type_depth", "transition", "camera",
            "sfx", "composite", "finish", "rhythm", "layout", "world")
# Stages a card may declare: the EDL.fx stages plus the layers devices that
# do not compile through fxgraph already write (motion graphics, texts).
STAGES = ("world", "screen", "pre_captions", "over_type", "junction",
          "music_bus", "motion", "texts")
# Moment kinds a card may bind to; "beat.kick" style dotted refinements of
# these are allowed (cls of a RhythmMap onset).
BINDS_TO = ("word", "word.payoff", "syllable", "stress", "phrase_end",
            "beat", "beat.kick", "beat.snare", "downbeat", "onset", "cut",
            "section", "drop", "gap", "time")
# Lane features a card's needs.lane_features may name.
LANE_FEATURES = ("chromium", "onnx.rvm", "onnx.u2net", "onnx.yunet",
                 "demucs", "whisper")   # plus "ffmpeg:<filter>"


def load_cards():
    """{card id: card dict}. STUB: no cards yet."""
    return {}


def resolve(card_id, *, lane, sidecar_status, budget_ms=None):
    """(rung, reason) for one device on this render. STUB: every device is
    "omit" until 0C's resolver lands — nothing is registered to resolve."""
    return "omit", "device contract not installed"
