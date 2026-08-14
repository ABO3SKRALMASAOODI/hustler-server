"""Audio-only uploads carry transcript evidence into editorial decisions."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools                                             # noqa: E402
import db as dbx                                               # noqa: E402


class _Db:
    def __init__(self, asset, index):
        self.asset = asset
        self.index = index

    def run(self, fn, *args, **kwargs):
        if fn is dbx.asset_by_key:
            return self.asset
        if fn is dbx.get_index_by_sha:
            return {"json": self.index}
        raise AssertionError(f"unexpected DB call {fn}")


class _Ctx:
    project_id = 4

    def __init__(self, asset, index):
        self.db = _Db(asset, index)
        self._asset_perception = {asset["storage_key"]: {
            "bpm": 96.0, "bpm_conf": 0.8, "beats": [0.5, 1.125],
            "energy": [-12.0, -9.0], "energy_bin_s": 0.5,
        }}

    def latest_edl(self):
        return {"json": {"music": []}}


def test_audio_analysis_exposes_persisted_spoken_content_without_hearing():
    key = "music/4/interview.m4a"
    asset = {"id": 8, "kind": "music", "storage_key": key,
             "sha256": "abc", "meta": {"filename": "interview.m4a"}}
    index = {"language": "en", "words": [
        {"w": "the"}, {"w": "launch"}, {"w": "failed"},
        {"w": "because"}, {"w": "retention"}, {"w": "collapsed"},
    ]}
    out = agent_tools._asset_audio_analysis(_Ctx(asset, index), key)

    assert "SPEECH/VOCALS TRANSCRIPT EVIDENCE: 6 word(s)" in out
    assert "the launch failed because retention collapsed" in out


def test_audio_analysis_is_honest_when_no_words_are_detected():
    key = "music/4/instrumental.wav"
    asset = {"id": 9, "kind": "music", "storage_key": key,
             "sha256": "def", "meta": {"filename": "instrumental.wav"}}
    out = agent_tools._asset_audio_analysis(
        _Ctx(asset, {"language": None, "words": []}), key)

    assert "No reliable speech/vocals transcript was detected" in out
    assert "not proof" in out
