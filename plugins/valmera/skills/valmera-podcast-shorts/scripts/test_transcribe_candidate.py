#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("transcribe_candidate.py")
SPEC = importlib.util.spec_from_file_location("transcribe_candidate", SCRIPT)
assert SPEC and SPEC.loader
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)


class CandidateTranscriptTests(unittest.TestCase):
    def test_builds_full_processed_coverage_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media = root / "candidate.mp4"
            media.write_bytes(b"video-bytes")
            (root / "worker").mkdir()
            (root / "worker" / "transcribe.py").write_bytes(b"transcriber-code")

            def fake_extract(_media: Path, wav: Path) -> None:
                wav.write_bytes(b"wav-bytes")

            with mock.patch.object(candidate, "probe_media", return_value={
                     "duration_s": 12.5,
                     "has_audio": True,
                     "audio_stream": {"stream_index": 1, "codec_name": "aac", "source_start_s": 0.5, "declared_duration_s": 10.0},
                 }), \
                 mock.patch.object(candidate, "extract_wav", side_effect=fake_extract), \
                 mock.patch.object(candidate, "pcm_wav_duration_s", return_value=10.0), \
                 mock.patch.object(candidate, "faster_whisper_version", return_value="1.2.3"), \
                 mock.patch.object(candidate, "model_artifact_lineage", return_value={
                     "model_artifact_id": "Systran/faster-whisper-small",
                     "model_revision": "abc123",
                     "model_revision_status": "resolved_cache_ref",
                 }), \
                 mock.patch.object(candidate, "transcribe_with_worker", return_value=(
                     [{"text": "Hello", "start_s": 0.2, "end_s": 0.7}],
                     [{"text": "Hello", "start_s": 0.2, "end_s": 0.7}],
                     "en", [], {"model": "small", "device": "cpu"},
                 )):
                result = candidate.build_evidence(media, root, "small")

            self.assertEqual(result["status"], "complete")
            self.assertRegex(result["evidence_id"], r"^transcript-evidence:[a-f0-9]{64}$")
            self.assertEqual(result["evidence_role"], "factual_transcription_only")
            self.assertFalse(result["creative_decision_made"])
            self.assertEqual(result["asr"]["processed_coverage"], [[0.0, 10.0]])
            self.assertEqual(result["asr"]["processing_gaps"], [])
            self.assertEqual(result["audio_stream"]["media_timeline_coverage"], [[0.5, 10.5]])
            self.assertEqual(result["audio_stream"]["media_timeline_gaps"], [[0.0, 0.5], [10.5, 12.5]])
            self.assertEqual(result["asr"]["media_time_offset_s"], 0.5)
            self.assertEqual(result["tool_lineage"]["faster_whisper_version"], "1.2.3")
            self.assertEqual(result["tool_lineage"]["model_revision"], "abc123")
            self.assertTrue(result["tool_lineage"]["decode_config_sha256"].startswith("sha256:"))
            self.assertTrue(result["media_sha256"].startswith("sha256:"))
            self.assertTrue(result["audio_pcm_sha256"].startswith("sha256:"))
            self.assertIn("auditory_review", result["claims_not_made"])
            self.assertIn("direct_audio_perception", result["claims_not_made"])

    def test_no_audio_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media = root / "silent.mp4"
            media.write_bytes(b"silent")
            with mock.patch.object(candidate, "probe_media", return_value={"duration_s": 3.0, "has_audio": False, "audio_stream": None}):
                with self.assertRaisesRegex(candidate.CandidateTranscriptError, "no audio stream"):
                    candidate.build_evidence(media, root, "small")

    def test_empty_transcript_is_reported_without_claiming_silence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media = root / "music.mp4"
            media.write_bytes(b"music")
            (root / "worker").mkdir()
            (root / "worker" / "transcribe.py").write_bytes(b"transcriber-code")

            def fake_extract(_media: Path, wav: Path) -> None:
                wav.write_bytes(b"wav")

            with mock.patch.object(candidate, "probe_media", return_value={
                     "duration_s": 8.0,
                     "has_audio": True,
                     "audio_stream": {"stream_index": 1, "codec_name": "aac", "source_start_s": 0.0, "declared_duration_s": 8.0},
                 }), \
                 mock.patch.object(candidate, "extract_wav", side_effect=fake_extract), \
                 mock.patch.object(candidate, "pcm_wav_duration_s", return_value=8.0), \
                 mock.patch.object(candidate, "faster_whisper_version", return_value="1.2.3"), \
                 mock.patch.object(candidate, "model_artifact_lineage", return_value={
                     "model_artifact_id": "Systran/faster-whisper-small",
                     "model_revision": "abc123",
                     "model_revision_status": "resolved_cache_ref",
                 }), \
                 mock.patch.object(candidate, "transcribe_with_worker", return_value=(
                     [], [], "unknown", [], {"model": "small", "device": "cpu"}
                 )):
                result = candidate.build_evidence(media, root, "small")
            self.assertEqual(result["asr"]["processed_coverage"], [[0.0, 8.0]])
            self.assertIn("detected no speech", result["asr"]["warnings"][0])


if __name__ == "__main__":
    unittest.main()
