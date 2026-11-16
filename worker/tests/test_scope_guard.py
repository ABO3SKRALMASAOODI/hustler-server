"""Measuring which protected lanes a change touches (the request brief names the
lanes; scope_guard never reads the customer's words and never refuses a write)."""

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


def test_no_word_list_reads_the_customers_request():
    """Owner, 6 and 16 Nov 2026: no regex on intent in the request path."""
    assert not hasattr(scope_guard, "protected_lanes")
    assert not hasattr(scope_guard, "audio_revision_only")
    assert not hasattr(scope_guard, "rejection_message")
    assert scope_guard.preservation_violations(
        default_edl(20.0), {**default_edl(20.0), "music": [_music()]}) == []


def test_protected_music_keeps_identity_and_mix_but_allows_clock_remap():
    previous = default_edl(20.0)
    previous["music"] = [_music()]

    changed_track = default_edl(20.0)
    changed_track["music"] = [_music("music/first-search-hit.mp3")]
    assert scope_guard.preservation_violations(
        previous, changed_track, protected={'music'}) == [
            "music selection and mix"]

    changed_mix = default_edl(20.0)
    changed_mix["music"] = [_music(gain_db=-10.0)]
    assert scope_guard.preservation_violations(
        previous, changed_mix, protected={'music'}) == [
            "music selection and mix"]

    # A shorter picture edit clamps the same bed to the new output clock. It
    # is still the same music decision and should not make cutting impossible.
    remapped = default_edl(20.0)
    remapped["keep"] = [[0.0, 12.0]]
    remapped["music"] = [_music(end=12.0)]
    assert scope_guard.preservation_violations(
        previous, remapped, protected={'music'}) == []


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
        protected={'captions', 'texts'}) \
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
        previous, removed, protected={'audio_mix'}) == []

    unmuted = {**removed, "inserts": [
        {**previous["inserts"][1], "mute": False}]}
    assert scope_guard.preservation_violations(
        previous, unmuted, protected={'audio_mix'}) == [
            "audio mix"]

    added_audible = {**previous, "inserts": previous["inserts"] + [{
        "id": "ins3", "asset_key": "clips/new.mp4", "kind": "video",
        "at_output_s": 15.0, "duration_s": 2.0, "source_start_s": 0.0,
        "mute": False}]}
    assert scope_guard.preservation_violations(
        previous, added_audible, protected={'audio_mix'}) == [
            "audio mix"]


def test_a_frozen_audio_mix_sees_the_mastering_a_reframe_switches_on():
    # 9:16/4:5/1:1 frames master an unset master by default, so "make it
    # vertical but keep the audio as it is" must not level and master the
    # mix behind the user's back via the frame alone.
    wide = default_edl(20.0)
    wide["frame"] = {"ratio": "16:9", "mode": "crop"}
    tall = {**wide, "frame": {"ratio": "9:16", "mode": "crop"}}
    ask = {"audio_mix"}
    [label] = scope_guard.preservation_violations(
        wide, tall, protected=ask, source_shape=(1920, 1080))
    assert label.startswith("audio mix (loudness mastering natural -> social")
    assert "set_master_loudness" in label
    # Pinning the natural level first makes the same reframe legal, and the
    # pin itself changes nothing a 16:9 render plays.
    pinned = {**wide, "master": {"loudness": "natural"}}
    assert scope_guard.preservation_violations(
        wide, pinned, protected=ask, source_shape=(1920, 1080)) == []
    assert scope_guard.preservation_violations(
        pinned, {**pinned, "frame": tall["frame"]}, protected=ask,
        source_shape=(1920, 1080)) == []
    # A phone recording left at 'source' is already mastered by default:
    # cropping it to 9:16 keeps the same sound.
    portrait = {**wide, "frame": {"ratio": "source", "mode": "crop"}}
    assert scope_guard.preservation_violations(
        portrait, tall, protected=ask, source_shape=(1080, 1920)) == []


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
        previous, remapped, protected={'texts'}) == []

    restyled = {**remapped, "texts": [
        {**remapped["texts"][0], "color": "#FF0000"}]}
    assert scope_guard.preservation_violations(
        previous, restyled, protected={'texts'}) == [
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


def test_the_commit_boundary_saves_what_the_customers_words_once_froze():
    """The word-list freeze refused this write; the write now saves (owner,
    16 Nov 2026). Without a model-read brief nothing is protected at all."""
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
    import music_gate
    ctx._music_grants = {"music/replacement.mp3": "user_upload"}
    result = ctx.write_edl(proposed, "expanded surgical request")
    assert result.startswith("EDL v7 -> v8"), result
    assert fake.inserts == 1
