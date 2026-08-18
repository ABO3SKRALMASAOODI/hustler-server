"""Truthful music-rights evidence survives search, fetch and later audits."""

import hashlib
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_tools                                            # noqa: E402
import music_search                                           # noqa: E402


def _known_hit():
    return {
        "provider": "openverse",
        "id": "openverse:11111111-1111-1111-1111-111111111111",
        "provider_candidate_id": "11111111-1111-1111-1111-111111111111",
        "title": "Clean Pulse",
        "artist": "Artist A",
        "duration_s": 72.0,
        "license": "by-4.0",
        "provider_reported_license_id": "by",
        "provider_reported_license_label": None,
        "provider_reported_license_version": "4.0",
        "provider_reported_license_url": (
            "https://creativecommons.org/licenses/by/4.0/"),
        "canonical_source_url": "https://example.test/track/1",
        "source_url": "https://example.test/track/1",
        "provider_retrieved_at": "2026-08-18T00:00:00Z",
        "_url": "https://cdn.example.test/track.mp3",
    }


def _fetched_meta(sha256="a" * 64):
    hit = _known_hit()
    rights = music_search.provenance(hit)
    rights["downloaded_sha256"] = sha256
    rights["tool_normalized_license_note"] = music_search.license_note(hit)
    return {
        "filename": "Clean Pulse — Artist A.mp3",
        "source": "openverse",
        "source_url": hit["source_url"],
        "license": hit["license"],
        "license_note": rights["tool_normalized_license_note"],
        "author": "Artist A",
        "source_audio_stream_status": "complete",
        "source_has_audio_stream": True,
        "source_audio_stream_codec": "mp3",
        "source_audio_stream_channels": 2,
        "source_audio_stream_probe_tool": "ffprobe",
        "source_audio_stream_probed_at": "2026-08-18T00:01:00Z",
        **rights,
    }


def test_unknown_license_never_becomes_cc_by_or_a_usable_hit():
    unknown = {"provider": "openverse", "id": "openverse:x",
               "license": "custom-copyright", "artist": "Someone"}
    rights = music_search.provenance(unknown)
    assert rights["license_verification_status"] == "unknown"
    assert rights["commercial_use_allowed"] is None
    assert rights["attribution_required"] is None
    assert rights["derivatives_allowed"] is None
    assert "UNKNOWN" in music_search.license_note(unknown)
    assert "CC BY" not in music_search.license_note(unknown)
    assert not music_search.usable(unknown)
    for deceptive in ("written by artist", "https://example.test/?by=4.0",
                      "copyrighted-by-owner"):
        candidate = dict(unknown, license=deceptive)
        assert music_search.provenance(candidate)[
            "license_verification_status"] == "unknown"
        assert "CC BY" not in music_search.license_note(candidate)


def test_nc_nd_and_unknown_never_pass_a_commercial_ready_gate():
    base = {"provider": "openverse", "id": "openverse:x",
            "artist": "Artist"}
    nc = dict(base, license="by-nc-sa-4.0")
    nd = dict(base, license="by-nd-4.0")
    unknown = dict(base, license="all-rights-reserved")

    nc_rights = music_search.provenance(nc)
    assert nc_rights["commercial_use_allowed"] is False
    assert nc_rights["attribution_required"] is True
    assert nc_rights["derivatives_allowed"] is True
    assert music_search.usable(nc, commercial_only=False)
    assert not music_search.usable(nc, commercial_only=True)

    nd_rights = music_search.provenance(nd)
    assert nd_rights["commercial_use_allowed"] is True
    assert nd_rights["derivatives_allowed"] is False
    assert not music_search.usable(nd, commercial_only=False)
    assert not music_search.usable(nd, commercial_only=True)

    unknown_rights = music_search.provenance(unknown)
    assert unknown_rights["license_verification_status"] == "unknown"
    assert unknown_rights["normalized_license_family"] is None
    assert unknown_rights["commercial_use_allowed"] is None
    assert unknown_rights["attribution_required"] is None
    assert unknown_rights["derivatives_allowed"] is None
    assert not music_search.usable(unknown, commercial_only=False)
    assert not music_search.usable(unknown, commercial_only=True)


def test_provider_label_can_be_the_raw_license_basis_without_inventing_id():
    item = {"provider": "catalog", "provider_candidate_id": "42",
            "provider_reported_license_id": None,
            "provider_reported_license_label": "CC BY 4.0"}
    rights = music_search.provenance(item)
    assert rights["provider_reported_license_id"] is None
    assert rights["provider_reported_license_label"] == "CC BY 4.0"
    assert rights["license_verification_status"] == (
        "provider_metadata_exposed")
    assert rights["commercial_use_allowed"] is True
    assert rights["attribution_required"] is True
    assert rights["derivatives_allowed"] is True


def test_provider_provenance_keeps_raw_values_and_capabilities_distinct():
    rights = music_search.provenance(_known_hit())
    assert rights == {
        "provider": "openverse",
        "provider_candidate_id": "11111111-1111-1111-1111-111111111111",
        "provider_reported_license_id": "by",
        "provider_reported_license_label": None,
        "provider_reported_license_version": "4.0",
        "provider_reported_license_url": (
            "https://creativecommons.org/licenses/by/4.0/"),
        "license_verification_status": "provider_metadata_exposed",
        "normalized_license_family": "by",
        "commercial_use_allowed": True,
        "attribution_required": True,
        "derivatives_allowed": True,
        "canonical_source_url": "https://example.test/track/1",
        "source_url": "https://example.test/track/1",
        "creator": "Artist A",
        "provider_retrieved_at": "2026-08-18T00:00:00Z",
        "downloaded_sha256": None,
    }
    line = music_search.describe(_known_hit())
    assert "provider_candidate_id=11111111-1111-1111-1111-111111111111" in line
    assert "provider_reported_license_id=by" in line
    assert "provider_reported_license_version=4.0" in line
    assert "license_verification_status=provider_metadata_exposed" in line
    assert "commercial_use_allowed=true" in line
    assert "attribution_required=true" in line
    assert "derivatives_allowed=true" in line
    assert "canonical_source_url=https://example.test/track/1" in line


def test_openverse_search_drops_unrecognized_license_even_for_personal_use(
        monkeypatch):
    page = {"results": [
        {"id": "unknown", "title": "Mystery", "creator": "A",
         "license": "copyright", "url": "https://audio.test/a.mp3"},
        {"id": "known", "title": "Known", "creator": "B",
         "license": "by", "license_version": "4.0",
         "license_url": "https://creativecommons.org/licenses/by/4.0/",
         "foreign_landing_url": "https://example.test/known",
         "url": "https://audio.test/b.mp3"},
    ]}
    monkeypatch.setattr(music_search, "_openverse_get_json",
                        lambda *a, **k: page)
    hits = music_search._openverse_search("pulse", None, None, 5,
                                          commercial_only=False)
    assert [hit["id"] for hit in hits] == ["openverse:known"]
    assert hits[0]["provider_reported_license_url"].endswith("/by/4.0/")


def test_audio_stream_probe_is_strict_and_never_assumes_true(monkeypatch):
    monkeypatch.setattr(
        music_search.subprocess, "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0,
            stdout=(b'{"streams":[{"index":0,"codec_name":"aac",'
                    b'"channels":2}]}')))
    complete = music_search.probe_audio_stream("candidate.audio")
    assert complete["source_audio_stream_status"] == "complete"
    assert complete["source_has_audio_stream"] is True
    assert complete["source_audio_stream_codec"] == "aac"
    assert complete["source_audio_stream_channels"] == 2

    monkeypatch.setattr(
        music_search.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0,
                                         stdout=b'{"streams":[]}'))
    silent = music_search.probe_audio_stream("candidate.audio")
    assert silent["source_audio_stream_status"] == "complete"
    assert silent["source_has_audio_stream"] is False

    monkeypatch.setattr(
        music_search.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("probe failed")))
    unavailable = music_search.probe_audio_stream("candidate.audio")
    assert unavailable["source_audio_stream_status"] == "unavailable"
    assert unavailable["source_has_audio_stream"] is None


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.inserted_meta = None
        self.inserted_sha256 = None

    def run(self, fn, *args, **kwargs):
        name = getattr(fn, "__name__", "")
        if name == "assets_by_kinds":
            return self.rows
        if name == "asset_by_key":
            key = args[1]
            return next((row for row in self.rows
                         if row.get("storage_key") == key), None)
        if name == "insert_asset":
            self.inserted_meta = kwargs["meta"]
            self.inserted_sha256 = kwargs["sha256"]
            return None
        raise AssertionError(name)


class _Ctx:
    def __init__(self, rows, edl, workdir):
        self.project_id = 9
        self.db = _DB(rows)
        self._edl = edl
        self.workdir = str(workdir)
        self._read_evidence_seen = set()
        self.audio_fetched = []
        self._last_music_listener_choice = None
        self.editing_metrics = {}
        self.last_preview = {}

    def latest_edl(self):
        return {"version": 3, "json": self._edl}


def test_list_and_mix_audit_reexpose_persisted_provenance(tmp_path):
    meta = _fetched_meta()
    key = "music/9/pulse.mp3"
    row = {"id": 1, "kind": "music", "storage_key": key,
           "duration_s": 72.0, "sha256": "a" * 64, "meta": meta}
    edl = {"music": [{"id": "m1", "storage_key": key, "start": 0,
                       "end": 20, "offset_s": 4, "gain_db": -20,
                       "duck": True, "duck_mode": "smooth", "loop": True,
                       "mute": False, "fade_in_s": 1, "fade_out_s": 2}],
           "voiceover": [], "sfx": [], "master": {}}
    ctx = _Ctx([row], edl, tmp_path)

    listed = agent_tools.list_assets(ctx, "music")
    marker = listed.split("MUSIC_PROVENANCE=", 1)[1].split(
        " — MUSIC_SOURCE_PROBE=", 1)[0]
    listed_rights = json.loads(marker)
    assert listed_rights["provider_reported_license_id"] == "by"
    assert listed_rights["downloaded_sha256"] == "a" * 64
    assert listed_rights["tool_normalized_license_note"].startswith("CC BY")

    audited = json.loads(agent_tools.audit_audio_mix(ctx))
    rights = audited["mix"]["music"][0]["provenance"]
    assert rights["canonical_source_url"] == "https://example.test/track/1"
    assert rights["commercial_use_allowed"] is True
    assert rights["attribution_required"] is True
    assert rights["derivatives_allowed"] is True
    music = audited["mix"]["music"][0]
    assert music["loop"] is True and music["mute"] is False
    assert music["fade_in_s"] == 1 and music["fade_out_s"] == 2
    assert music["source_duration_s"] == 72
    assert music["source_coverage_s"] == 20
    assert music["gain_effectively_muted"] is False
    assert music["source_has_audio_stream"] is True
    assert music["source_audio_stream_status"] == "complete"
    assert not any("unknown rights" in warning
                   for warning in audited["warnings"])


def test_legacy_or_uploaded_music_reports_unknown_instead_of_defaults(
        tmp_path):
    key = "music/9/upload.mp3"
    row = {"id": 2, "kind": "music", "storage_key": key,
           "duration_s": 10.0, "sha256": None,
           "meta": {"filename": "upload.mp3"}}
    edl = {"music": [{"id": "m2", "storage_key": key,
                       "start": 0, "end": 10}],
           "voiceover": [], "sfx": [], "master": {}}
    ctx = _Ctx([row], edl, tmp_path)
    listed = agent_tools.list_assets(ctx, "music")
    raw_rights, raw_probe = listed.split("MUSIC_PROVENANCE=", 1)[1].split(
        " — MUSIC_SOURCE_PROBE=", 1)
    rights = json.loads(raw_rights)
    probe = json.loads(raw_probe)
    assert rights["license_verification_status"] == "unknown"
    assert rights["commercial_use_allowed"] is None
    assert rights["attribution_required"] is None
    assert rights["derivatives_allowed"] is None
    assert probe["source_audio_stream_status"] == "not_exposed"
    assert probe["source_has_audio_stream"] is None

    audited = json.loads(agent_tools.audit_audio_mix(ctx))
    music = audited["mix"]["music"][0]
    assert music["source_audio_stream_status"] == "not_exposed"
    assert music["source_has_audio_stream"] is None
    assert any("unknown rights" in warning for warning in audited["warnings"])


def test_fetch_persists_content_hash_and_raw_provider_fields(
        tmp_path, monkeypatch):
    ctx = _Ctx([], {"music": [], "voiceover": [], "sfx": []}, tmp_path)
    hit = _known_hit()
    ctx._music_hits = {hit["id"]: hit}
    payload = b"truthful-audio-payload"

    def fake_download(_hit, path):
        with open(path, "wb") as handle:
            handle.write(payload)

    monkeypatch.setattr(music_search, "download", fake_download)
    monkeypatch.setattr(music_search, "probe_duration_s", lambda _path: 72.0)
    monkeypatch.setattr(
        music_search, "probe_audio_stream",
        lambda _path: {
            "source_audio_stream_status": "complete",
            "source_has_audio_stream": True,
            "source_audio_stream_codec": "mp3",
            "source_audio_stream_channels": 2,
            "source_audio_stream_probe_tool": "ffprobe",
            "source_audio_stream_probed_at": "2026-08-18T00:01:00Z",
        })
    monkeypatch.setattr(agent_tools.storage, "upload_file",
                        lambda *a, **k: None)

    result = agent_tools.fetch_music(ctx, hit["id"])
    meta = ctx.db.inserted_meta
    expected_sha = hashlib.sha256(payload).hexdigest()
    assert meta["downloaded_sha256"] == expected_sha
    assert ctx.db.inserted_sha256 == expected_sha
    assert meta["provider_candidate_id"] == hit["provider_candidate_id"]
    assert meta["provider_reported_license_id"] == "by"
    assert meta["provider_reported_license_version"] == "4.0"
    assert meta["canonical_source_url"] == "https://example.test/track/1"
    assert (meta["license_verification_status"] ==
            "provider_metadata_exposed")
    assert meta["source_has_audio_stream"] is True
    assert meta["source_audio_stream_status"] == "complete"
    assert expected_sha in result
    assert "MUSIC_PROVENANCE=" in result
    assert "fetch_music" in agent_tools.WRITE_TOOLS
