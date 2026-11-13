"""Track 4F loop: the sound desk (agents can never approve): the owner's ear with his typed line, and the
reference match of the owner's decision of 13 Nov 2026; the critic's read-only verb on the MCP read-only list."""

import json
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import sound_approval                                         # noqa: E402


def _wav(path):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "anoisesrc=d=0.6:c=pink:a=0.3", "-af",
                    "afade=t=out:st=0.3:d=0.3", path], check=True)
    return path


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_stage_needs_a_library_licence_and_the_owners_door_needs_his_typed_line(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "manifest.json").write_text("[]")
    aud = str(tmp_path / "aud")
    src = _wav(str(tmp_path / "rain.wav"))
    # the library's licence policy: CC0 or the Sonniss bundle licence, nothing that owes attribution
    with pytest.raises(sound_approval.ApprovalError, match="CC0"):
        sound_approval.stage("sr_x", src, source_url="https://f/1",
                             licence="CC BY 4.0", author="a",
                             audition_dir=aud)
    assert sorted(sound_approval.LICENCES) == ["CC0 1.0", "Sonniss GameAudioGDC"]
    cid = sound_approval.stage("sr_x", src, source_url="https://f/1",
                               licence="CC0", author="Someone",
                               title="Rain", audition_dir=aud)
    with pytest.raises(sound_approval.ApprovalError, match="attestation"):
        sound_approval.approve("sr_x", cid, role="rain", use="rain on glass",
                               attest="yes", audition_dir=aud,
                               lib_dir=str(lib))
    rec = sound_approval.approve(
        "sr_x", cid, role="rain", use="rain on glass under a quiet line",
        attest=sound_approval.attestation("sr_x"), audition_dir=aud,
        lib_dir=str(lib))
    man = json.loads((lib / "manifest.json").read_text())
    assert rec == "rain_1" and man[0]["license"] == "CC0 1.0"
    assert man[0]["source_url"] == "https://f/1"
    assert man[0]["approval"] == "approved by the owner's ear"
    assert (lib / "rain_1.flac").exists()


LIB = os.path.join(os.path.dirname(HERE), "sound_library")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_the_reference_door_joins_a_matching_recording_and_writes_no_attestation(tmp_path, monkeypatch):
    """Owner decision 13 Nov 2026: the references judge. A lossless recording whose signal matches a class of sound
    the reference films place joins with the moments it matched; the owner's typed line is never asked for,
    written or simulated."""
    def never(*a, **k):
        raise AssertionError("the reference door never touches the owner's attestation")
    monkeypatch.setattr(sound_approval, "attestation", never)
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "manifest.json").write_text("[]")
    aud = str(tmp_path / "aud")
    rows = {r["id"]: r for r in json.load(open(os.path.join(LIB, "manifest.json")))}
    src = os.path.join(LIB, rows["whoosh_3"]["file"])                    # a professional recording that joined this way
    cid = sound_approval.stage("rq", src, source_url="https://archive.org/download/x/y.flac", licence="Sonniss GameAudioGDC",
                               author="A recordist", title="A sweep", audition_dir=aud,
                               trail={"source_file": "pack/y.flac", "source_sha256": "0" * 64, "bundle": "sonniss-gdc-2015"})
    rec = sound_approval.approve_by_reference("rq", cid, role="whoosh", use="a sweep on a moving cut", audition_dir=aud, lib_dir=str(lib))
    row = json.loads((lib / "manifest.json").read_text())[0]
    assert rec == "whoosh_1" and row["approval"] == "approved by reference match, owner decision 13 Nov 2026"
    ref = row["reference"]
    assert ref["class"] == "whoosh" and ref["distance"] <= ref["limit"]
    assert len(ref["moments"]) == 5 and len({m[0] for m in ref["moments"]}) == 5      # five films, a second in each
    assert all(isinstance(m[0], str) and m[1] >= 0 and m[2] >= 0 for m in ref["moments"])
    assert row["license"] == "Sonniss GameAudioGDC" and row["source_sha256"] == "0" * 64 and row["bundle"]
    assert -36.0 <= row["gain_db"] <= -2.0 and row["shape"]["dur_s"] > 0
    assert "listened" not in json.dumps(row).lower() and "attest" not in json.dumps(row).lower()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_the_reference_door_turns_away_what_the_references_do_not_place(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "manifest.json").write_text("[]")
    aud = str(tmp_path / "aud")
    # a held tone: no class of the references has this shape
    tone = str(tmp_path / "tone.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=880:d=3", "-af", "volume=0.5", tone], check=True)
    cid = sound_approval.stage("rq", tone, source_url="https://f/2", licence="CC0", author="a", audition_dir=aud)
    with pytest.raises(sound_approval.ApprovalError, match="reference"):
        sound_approval.approve_by_reference("rq", cid, role="whoosh", use="x", audition_dir=aud, lib_dir=str(lib))
    # a lossy source is not a professional recording, whatever it sounds like
    rows = {r["id"]: r for r in json.load(open(os.path.join(LIB, "manifest.json")))}
    mp3 = str(tmp_path / "w.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", os.path.join(LIB, rows["whoosh_3"]["file"]), "-c:a", "libmp3lame", "-b:a", "96k", mp3], check=True)
    cid = sound_approval.stage("rq", mp3, source_url="https://f/3", licence="CC0", author="a", audition_dir=aud)
    with pytest.raises(sound_approval.ApprovalError, match="lossless"):
        sound_approval.approve_by_reference("rq", cid, role="whoosh", use="x", audition_dir=aud, lib_dir=str(lib))
    # a recording driven into the ceiling
    import numpy as np
    x = np.clip(np.random.default_rng(4).standard_normal(48000).astype(np.float32) * 4, -1, 1)
    assert sound_approval._clipped(np.stack([x, x], axis=1)) and not sound_approval._clipped(np.stack([x, x], axis=1) * 0.5)
    assert json.loads((lib / "manifest.json").read_text()) == []


def test_every_recording_says_how_it_was_approved_and_keeps_its_trail():
    """The library as shipped: the first 21 by the owner's ear, the rest by reference match with the matched moments,
    a licence the policy names, a lossless source and its hash."""
    rows = json.load(open(os.path.join(LIB, "manifest.json")))
    refs = sound_approval.references()
    films = set(refs["film"])
    ear = [r for r in rows if not r.get("reference")]
    matched = [r for r in rows if r.get("reference")]
    assert len(ear) == 21 and all(r["license"] == "CC0 1.0" and "approval" not in r for r in ear)
    assert len(matched) >= 60
    for r in matched:
        assert r["approval"] == sound_approval.REFERENCE_APPROVAL, r["id"]
        ref = r["reference"]
        assert ref["class"] in refs["limits"] and ref["distance"] <= ref["limit"] == refs["limits"][ref["class"]], r["id"]
        assert len(ref["moments"]) == 5 and all(m[0] in films for m in ref["moments"]), r["id"]
        assert r["license"] in sound_approval.LICENCES and r["source_url"].startswith("https://"), r["id"]
        assert sound_approval.lossless(r["source_codec"]) and r["source_rate"] >= 44100, r["id"]
        assert len(r["source_sha256"]) == 64 and r["source_file"] and r["bundle"] and len(r["bundle_licence_sha256"]) == 64, r["id"]
        assert "attest" not in json.dumps(r).lower() and "listened" not in json.dumps(r).lower(), r["id"]
    # every class the references place is in the library, in several recordings
    by = {}
    for r in matched:
        by[r["reference"]["class"]] = by.get(r["reference"]["class"], 0) + 1
    assert set(by) == {"click", "hit", "impact", "whoosh", "riser"} and min(by.values()) >= 4, by
    fams = {r.get("family") for r in matched}
    assert {"paper", "shutter", "glitch", "braam"} <= fams
    for name in os.listdir(os.path.join(LIB, "licences")):
        assert name.startswith("sonniss_gameaudiogdc_bundle_licence")
    import hashlib
    kept = {hashlib.sha256(open(os.path.join(LIB, "licences", n), "rb").read()).hexdigest() for n in os.listdir(os.path.join(LIB, "licences"))}
    assert {r["bundle_licence_sha256"] for r in matched} <= kept


def test_no_tool_can_reach_the_approval_flow():
    import agent_tools
    import inspect
    for name, spec in agent_tools.TOOLS.items():
        fn = spec[0] if isinstance(spec, tuple) else spec
        mod = getattr(fn, "__module__", "") or ""
        assert "sound_approval" not in mod, name
    d = os.path.join(os.path.dirname(HERE), "tools_ext")
    for f in os.listdir(d):
        if f.endswith(".py"):
            assert "sound_approval" not in open(os.path.join(d, f)).read(), f
    src = inspect.getsource(agent_tools)
    assert "sound_approval" not in src
    assert not [n for n in agent_tools.TOOLS if "approve" in n and "sound"
                in n]


def test_critique_short_is_read_only_everywhere():
    import agent_tools
    assert "critique_short" not in agent_tools.WRITE_TOOLS
    path = os.path.join(os.path.dirname(os.path.dirname(HERE)), "backend",
                        "routes", "mcp.py")
    if os.path.exists(path):
        assert "critique_short" in open(path).read()
