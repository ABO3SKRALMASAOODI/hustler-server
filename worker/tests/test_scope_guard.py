"""Explicit user scope is enforced at the one real EDL commit boundary."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools
import db as dbx
import scope_guard
from schemas import default_edl


def _music(key="music/approved.mp3", **extra):
    return {"id": "mu1", "storage_key": key, "start": 0.0, "end": 20.0,
            "gain_db": -18.0, "duck": True, **extra}


def test_only_high_confidence_preservation_language_creates_a_guard():
    assert scope_guard.protected_lanes(
        "Preserve the current music. Do NOT add or modify text overlays "
        "in this pass. Leave the captions as-is.") == {
            "music", "texts", "captions"}

    # This is a local composition instruction, not a request to freeze every
    # designed-text item in the EDL.
    assert "texts" not in scope_guard.protected_lanes(
        "Do NOT overlay large DOPE SPORTS text on the homepage. Add a small "
        "brand reveal later.")
    assert scope_guard.protected_lanes(
        "Make a nice energetic edit with tasteful captions and music") == set()


def test_protected_music_keeps_identity_and_mix_but_allows_clock_remap():
    previous = default_edl(20.0)
    previous["music"] = [_music()]

    changed_track = default_edl(20.0)
    changed_track["music"] = [_music("music/first-search-hit.mp3")]
    assert scope_guard.preservation_violations(
        previous, changed_track, "keep the current music") == [
            "music selection and mix"]

    changed_mix = default_edl(20.0)
    changed_mix["music"] = [_music(gain_db=-10.0)]
    assert scope_guard.preservation_violations(
        previous, changed_mix, "do not touch the music") == [
            "music selection and mix"]

    # A shorter picture edit clamps the same bed to the new output clock. It
    # is still the same music decision and should not make cutting impossible.
    remapped = default_edl(20.0)
    remapped["keep"] = [[0.0, 12.0]]
    remapped["music"] = [_music(end=12.0)]
    assert scope_guard.preservation_violations(
        previous, remapped, "preserve the current music") == []


def test_text_and_caption_guards_reject_creative_changes():
    previous = default_edl(20.0)
    previous["captions"] = {"mode": "from_transcript",
                            "design_version": 2,
                            "style": {"preset": "clean"}}
    proposed = default_edl(20.0)
    proposed["captions"] = {"mode": "from_transcript",
                            "design_version": 2,
                            "style": {"preset": "beast"}}
    proposed["texts"] = [{"id": "tx1", "text": "WATCH THIS",
                           "start": 2.0, "end": 4.0,
                           "template": "title"}]
    assert scope_guard.preservation_violations(
        previous, proposed,
        "Do not change the captions. Do not add or modify text overlays.") \
        == ["captions", "designed text overlays"]


def test_preserved_audio_mix_does_not_make_deleted_picture_undeletable():
    previous = default_edl(20.0)
    previous["inserts"] = [
        {"id": "ins1", "asset_key": "clips/unwanted.mp4", "kind": "video",
         "at_output_s": 2.0, "duration_s": 5.0, "source_start_s": 0.0,
         "mute": False},
        {"id": "ins2", "asset_key": "clips/keeper.mp4", "kind": "video",
         "at_output_s": 10.0, "duration_s": 3.0, "source_start_s": 0.0,
         "mute": True},
    ]
    removed = {**previous, "inserts": [previous["inserts"][1]]}
    assert scope_guard.preservation_violations(
        previous, removed, "preserve the current audio mix") == []

    unmuted = {**removed, "inserts": [
        {**previous["inserts"][1], "mute": False}]}
    assert scope_guard.preservation_violations(
        previous, unmuted, "preserve the current audio mix") == [
            "audio mix"]

    added_audible = {**previous, "inserts": previous["inserts"] + [{
        "id": "ins3", "asset_key": "clips/new.mp4", "kind": "video",
        "at_output_s": 15.0, "duration_s": 2.0, "source_start_s": 0.0,
        "mute": False}]}
    assert scope_guard.preservation_violations(
        previous, added_audible, "preserve the current audio mix") == [
            "audio mix"]


def test_text_timing_can_follow_a_real_cut_without_weakening_its_design():
    previous = default_edl(20.0)
    previous["texts"] = [{"id": "tx1", "text": "CHAPTER TWO",
                           "start": 10.0, "end": 12.0,
                           "template": "chapter", "color": "#FFFFFF"}]
    remapped = default_edl(20.0)
    remapped["keep"] = [[2.0, 20.0]]
    remapped["texts"] = [{**previous["texts"][0],
                           "start": 8.0, "end": 10.0}]
    assert scope_guard.preservation_violations(
        previous, remapped, "preserve the existing text overlays") == []

    restyled = {**remapped, "texts": [
        {**remapped["texts"][0], "color": "#FF0000"}]}
    assert scope_guard.preservation_violations(
        previous, restyled, "preserve the existing text overlays") == [
            "designed text overlays"]


class _Db:
    def __init__(self, edl):
        self.rows = [{"version": 7, "json": edl}]
        self.inserts = 0

    def run(self, fn, *args):
        if fn is dbx.latest_edl:
            return self.rows[-1]
        if fn is dbx.insert_edl:
            self.inserts += 1
            self.rows.append({"version": 8, "json": args[1]})
            return 8
        raise AssertionError(f"unexpected DB call: {fn}")


def test_real_commit_boundary_rejects_an_atomic_recipe_scope_violation():
    edl = default_edl(20.0)
    edl["music"] = [_music()]
    fake = _Db(edl)
    ctx = agent_tools.ToolContext(
        fake, {"id": 9, "user_id": 3},
        {"id": 7, "chat_session_id": 11},
        {"video": {"duration": 20.0, "width": 1920, "height": 1080,
                   "fps": 30.0}, "words": []},
        tempfile.mkdtemp())
    ctx.user_message = (
        "Preserve the current music. Do NOT add or modify text overlays "
        "in this pass.")
    proposed = default_edl(20.0)
    proposed["music"] = [_music("music/replacement.mp3")]
    proposed["texts"] = [{"id": "tx1", "text": "NEW",
                           "start": 1.0, "end": 2.0,
                           "template": "title"}]

    result = ctx.write_edl(proposed, "expanded surgical request")
    assert result.startswith("REJECTED (EDL v7 unchanged)")
    assert "music selection and mix" in result
    assert "designed text overlays" in result
    assert fake.inserts == 0
    assert fake.rows[-1]["version"] == 7
