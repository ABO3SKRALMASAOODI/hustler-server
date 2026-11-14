"""Explicit active/v9 surfaces: archived prose must not define the v10 contract."""
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2] / "plugins/valmera/skills/valmera-podcast-shorts"
LEGACY = SKILL / "legacy/v9"


def active_text():
    # Enumerate the entry points; a missing file must fail, never silently glob to zero.
    return " ".join((SKILL / "SKILL.md").read_text().split())


def script(name="podcast.py"):
    return (SKILL / "scripts" / name).read_text()


def assert_current_sound(text):
    for phrase in ("Sound effects are part of the film", "same kinds and about the same amount",
                   "real recordings only", "A short with no designed sound is a failure",
                   "So is a sound on every caption", "one level hierarchy under the voice",
                   "no recording twice"):
        assert phrase in text, phrase


def assert_current_motion_and_music(text):
    for phrase in ("owner, 13 Nov 2026", "@metromedia.house", "Fast and dynamic, done well",
                   "motivated by the words, eased, faces whole", "16 cuts per 10 s",
                   "69% of shots moving video", "4.3 camera moves", "3.6 audible sound effects",
                   "Every short gets music", "Valmera chooses a track from its licensed library",
                   "turn and payoff", "same short without music is delivered beside it"):
        assert phrase in text, phrase
    # Historical wording can be quoted only as explicitly withdrawn doctrine.
    withdrawn = text[text.index("Withdrawn:"):text.index("## The styles")]
    for rule in ('"sound effects default to zero"', '"restraint is the default"',
                 '"music is off unless the owner supplies a song"'):
        assert rule in withdrawn
        assert rule not in text.replace(withdrawn, "")
