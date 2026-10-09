#!/usr/bin/env python3
"""Tests for the v9 run state (run with `python3 test_run.py` or pytest)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402

SCRIPT = Path(__file__).with_name("run.py")
CHECKS = "hook=yes,payoff=yes,targets=yes,attention=yes,clean=yes"


def call(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dir = str(self.root / "run")
        self.ok("init", "--run-dir", self.dir, "--run-id", "pod-1",
                "--source", "https://youtu.be/x", "--source-title", "A talk",
                "--channel", "Archive", "--people", "Steve Jobs",
                "--parent-project-id", "100")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def ok(self, *argv: str) -> str:
        code, out, err = call(*argv)
        self.assertEqual(code, 0, err)
        return out

    def fails(self, expected: str, *argv: str) -> None:
        code, _out, err = call(*argv)
        self.assertEqual(code, 2, f"expected failure, got success for {argv}")
        self.assertIn(expected, err)

    def state(self) -> dict:
        return json.loads((Path(self.dir) / "run.json").read_text())

    def brief(self, short_id: str, **extra) -> str:
        value = {
            "short_id": short_id, "look": "kinetic-poster",
            "structure": "fast-conversation", "speaker": "Steve Jobs",
            "headline": "Steve Jobs: Computers look like garbage",
            "structure_reason": "a list of concrete objects",
            "closest_alternative": "headline-conversation",
            "story": {"viewer_question": "Why ugly?", "hook": "they look like garbage",
                      "turn": "people buy them anyway", "payoff": "great costs nothing"},
            "beats": [{"role": "hook", "cue": "garbage", "source_s": 466.2,
                       "move": "word_slam"}],
            "brief": "Open on the slam of garbage, cut wide, payoff in serif.",
        }
        value.update(extra)
        path = self.root / f"brief-{short_id}.json"
        path.write_text(json.dumps(value))
        return str(path)

    def add(self, short_id: str, project: int, **kw) -> None:
        argv = ["add-short", "--run-dir", self.dir, "--short-id", short_id,
                "--project-id", str(project), "--title", f"Title {short_id}",
                "--source-start", "459.31", "--source-end", "515.455"]
        for key, value in kw.items():
            argv += [f"--{key.replace('_', '-')}", str(value)]
        self.ok(*argv)

    def to_candidate(self, short_id: str, editor: str, version: int = 7) -> None:
        self.ok("assign", "--run-dir", self.dir, "--short-id", short_id,
                "--brief", self.brief(short_id), "--editor", editor)
        self.ok("candidate", "--run-dir", self.dir, "--short-id", short_id,
                "--preview", "https://example.invalid/preview.mp4",
                "--edl-version", str(version), "--note", "hook slam at 0.2s\nweak: 18s")

    def final(self, short_id: str, structure: str = "fast-conversation") -> Path:
        path = self.root / "run" / "exports" / f"{structure}__{short_id}__slug.mp4"
        path.write_bytes(b"not really an mp4 " + short_id.encode())
        return path

    # ── tests ────────────────────────────────────────────────────────────

    def test_init_is_idempotent_and_guards_other_runs(self) -> None:
        for name in ("source", "assignments", "candidates", "exports"):
            self.assertTrue((Path(self.dir) / name).is_dir())
        self.ok("init", "--run-dir", self.dir, "--run-id", "pod-1",
                "--source", "https://youtu.be/x", "--max-editors", "5", "--music", "on")
        state = self.state()
        self.assertEqual((state["max_editors"], state["music"]), (5, "on"))
        self.assertEqual(state["max_jobs"], 3)  # unchanged from the default
        self.fails("belongs to run", "init", "--run-dir", self.dir,
                   "--run-id", "other", "--source", "x")

    def test_refuses_v7_runs(self) -> None:
        legacy = self.root / "legacy"
        legacy.mkdir()
        (legacy / "run.json").write_text(json.dumps(
            {"version": "valmera-podcast-shorts-v7", "run_id": "old", "shorts": {}}))
        self.fails("run_state.py", "status", "--run-dir", str(legacy))

    def test_full_flow_writes_compatible_manifests(self) -> None:
        self.add("s01", 11, rank=1, speaker="Steve Jobs")
        self.add("s02", 12)
        self.add("s03", 13)
        self.to_candidate("s01", "e1")
        self.fails("ship needs a yes", "review", "--run-dir", self.dir,
                   "--short-id", "s01", "--verdict", "ship", "--note", "x",
                   "--checks", "hook=yes,payoff=no")
        self.ok("review", "--run-dir", self.dir, "--short-id", "s01",
                "--verdict", "ship", "--note", "slam lands", "--checks", CHECKS)
        final = self.final("s01")
        self.fails("is not the shipped", "export", "--run-dir", self.dir,
                   "--short-id", "s01", "--file", str(final), "--job-id", "9",
                   "--edl-version", "6", "--duration", "40.6")
        self.ok("export", "--run-dir", self.dir, "--short-id", "s01",
                "--file", str(final), "--job-id", "9", "--edl-version", "7",
                "--duration", "40.641", "--width", "1080", "--height", "1920",
                "--verified-full")
        self.to_candidate("s02", "e2")
        self.ok("review", "--run-dir", self.dir, "--short-id", "s02",
                "--verdict", "kill", "--note", "payoff restates the hook")
        self.ok("exception", "--run-dir", self.dir, "--short-id", "s03",
                "--reason", "render shard busy twice", "--next-action", "retry export",
                "--status", "failed_technical")
        skipped = self.root / "not-selected.json"
        skipped.write_text(json.dumps({"not_selected": ["Hamurabi (1103-1218): too long"]}))
        self.ok("finalize", "--run-dir", self.dir, "--not-selected", str(skipped))

        exports = Path(self.dir) / "exports"
        manifest = json.loads((exports / "manifest.json").read_text())
        self.assertEqual(manifest["version"], "valmera-shorts-export-v1")
        self.assertEqual(manifest["source"], "https://youtu.be/x")
        item = manifest["items"][0]
        for key in ("short_id", "child_project_id", "edl_version", "title",
                    "style_lane", "file", "sha256", "duration_s", "status"):
            self.assertIn(key, item)
        self.assertEqual(item["sha256"], hashlib.sha256(final.read_bytes()).hexdigest())
        self.assertEqual({e["short_id"]: e["status"] for e in manifest["exceptions"]},
                         {"s02": "killed", "s03": "failed_technical"})

        publishing = json.loads((exports / "publishing-manifest.json").read_text())
        self.assertEqual(publishing["version"], "valmera-publishing-manifest-v1")
        self.assertEqual(set(publishing), {"version", "run_id", "generated_at", "source",
                                           "run", "publishing", "items", "exceptions",
                                           "not_selected"})
        pub = publishing["items"][0]
        old_keys = {"short_id", "status", "title", "style_lane", "child_project_id",
                    "lane_reason", "closest_alternative", "speaker", "source_range_s",
                    "source_range_hms", "file", "path", "sha256", "bytes", "duration_s",
                    "editorial_s", "native_ending_s", "dimensions", "edl_version",
                    "export_job_id", "end_card", "headline", "story", "kept_transcript",
                    "kept_source_ranges_s", "broll", "required_credits", "rights_note",
                    "qc", "branding_check", "final_evidence", "previews_not_final",
                    "music", "published"}
        self.assertLessEqual(old_keys, set(pub))
        self.assertEqual(pub["source_range_hms"], ["7:39.3", "8:35.5"])
        self.assertEqual(pub["file"], final.name)
        self.assertEqual(pub["editorial_s"], 35.641)
        self.assertEqual(pub["dimensions"], [1080, 1920])
        self.assertEqual(pub["story"]["final_payoff"], "great costs nothing")
        self.assertEqual(pub["qc"]["verdict"], "ready")
        self.assertEqual(pub["qc"]["review"], "ship")
        self.assertEqual(publishing["source"]["people_verified"], ["Steve Jobs"])
        self.assertEqual(publishing["not_selected"], ["Hamurabi (1103-1218): too long"])
        markdown = (exports / "PUBLISHING.md").read_text()
        self.assertIn("## s01 - Title s01", markdown)
        self.assertIn("## Not exported", markdown)
        self.assertEqual(self.state()["stage"], "complete")

    def test_finalize_requires_every_short_accounted_for(self) -> None:
        self.fails("no selected shorts", "finalize", "--run-dir", self.dir)
        self.add("s01", 11)
        self.add("s02", 12)
        self.to_candidate("s01", "e1")
        self.fails("s01 (candidate), s02 (queued)", "finalize", "--run-dir", self.dir)

    def test_one_targeted_fix_then_ship_or_kill(self) -> None:
        self.add("s01", 11)
        self.to_candidate("s01", "e1")
        self.fails("non-empty", "review", "--run-dir", self.dir, "--short-id", "s01",
                   "--verdict", "fix", "--note", " ")
        self.ok("review", "--run-dir", self.dir, "--short-id", "s01",
                "--verdict", "fix", "--note", "18.2s: payoff word too small; word_slam")
        self.assertEqual(self.state()["shorts"]["s01"]["status"], "fix")
        self.ok("assign", "--run-dir", self.dir, "--short-id", "s01", "--editor", "e2")
        self.ok("candidate", "--run-dir", self.dir, "--short-id", "s01",
                "--preview", "p2", "--edl-version", "9", "--note", "fixed payoff")
        self.fails("already used", "review", "--run-dir", self.dir, "--short-id", "s01",
                   "--verdict", "fix", "--note", "again")
        self.ok("review", "--run-dir", self.dir, "--short-id", "s01",
                "--verdict", "ship", "--note", "ok", "--checks", CHECKS)
        self.assertEqual(self.state()["shorts"]["s01"]["review"]["edl_version"], 9)

    def test_editor_pool_limits(self) -> None:
        for n in range(1, 5):
            self.add(f"s0{n}", 10 + n)
            self.ok("assign", "--run-dir", self.dir, "--short-id", f"s0{n}",
                    "--brief", self.brief(f"s0{n}"))
        self.ok("assign", "--run-dir", self.dir, "--short-id", "s01", "--editor", "e1")
        self.fails("still on s01", "assign", "--run-dir", self.dir,
                   "--short-id", "s02", "--editor", "e1")
        self.ok("assign", "--run-dir", self.dir, "--short-id", "s02", "--editor", "e2")
        self.ok("assign", "--run-dir", self.dir, "--short-id", "s03", "--editor", "e3")
        self.fails("3 editors are busy", "assign", "--run-dir", self.dir,
                   "--short-id", "s04", "--editor", "e4")
        status = json.loads(self.ok("status", "--run-dir", self.dir, "--json"))
        self.assertEqual(status["editors"]["idle"], 0)
        self.ok("candidate", "--run-dir", self.dir, "--short-id", "s02",
                "--preview", "p", "--edl-version", "3", "--note", "n")
        self.ok("assign", "--run-dir", self.dir, "--short-id", "s04", "--editor", "e2")
        text = self.ok("status", "--run-dir", self.dir)
        self.assertIn("review candidates: s02", text)

    def test_brief_and_note_validation(self) -> None:
        self.add("s01", 11)
        self.fails("record a brief", "assign", "--run-dir", self.dir,
                   "--short-id", "s01", "--editor", "e1")
        self.fails("look must be one of", "assign", "--run-dir", self.dir,
                   "--short-id", "s01", "--brief", self.brief("s01", look="neon"))
        self.fails("story.payoff", "assign", "--run-dir", self.dir, "--short-id", "s01",
                   "--brief", self.brief("s01", story={"viewer_question": "q",
                                                       "hook": "h", "turn": "t"}))
        self.fails("keep it near 150", "assign", "--run-dir", self.dir,
                   "--short-id", "s01", "--brief", self.brief("s01", brief="word " * 300))
        self.fails("not 's01'", "assign", "--run-dir", self.dir, "--short-id", "s01",
                   "--brief", self.brief("s02"))
        self.ok("assign", "--run-dir", self.dir, "--short-id", "s01",
                "--brief", self.brief("s01", look="custom-quiet-confession"),
                "--editor", "e1")
        self.fails("keep it to 10", "candidate", "--run-dir", self.dir,
                   "--short-id", "s01", "--preview", "p", "--edl-version", "2",
                   "--note", "\n".join(f"line {i}" for i in range(11)))

    def test_export_checks_name_and_duration(self) -> None:
        self.add("s01", 11)
        self.to_candidate("s01", "e1")
        self.ok("review", "--run-dir", self.dir, "--short-id", "s01",
                "--verdict", "ship", "--note", "ok", "--checks", CHECKS)
        wrong = self.final("s01", "headline-conversation")
        self.fails("must start with fast-conversation__s01__", "export",
                   "--run-dir", self.dir, "--short-id", "s01", "--file", str(wrong),
                   "--job-id", "1", "--edl-version", "7", "--duration", "30")
        right = self.final("s01")
        self.fails("allows 15-45s", "export", "--run-dir", self.dir, "--short-id", "s01",
                   "--file", str(right), "--job-id", "1", "--edl-version", "7",
                   "--duration", "52")

    def test_hero_cap(self) -> None:
        for n in range(run.HERO_CAP):
            self.add(f"h{n:02d}", 100 + n)
        self.fails("hero cap reached", "add-short", "--run-dir", self.dir,
                   "--short-id", "h99", "--project-id", "999", "--title", "x")
        self.ok("add-short", "--run-dir", self.dir, "--short-id", "h99",
                "--project-id", "999", "--title", "x", "--tier", "standard")

    def test_concurrent_writers_do_not_lose_updates(self) -> None:
        procs = [subprocess.Popen(
            [sys.executable, str(SCRIPT), "add-short", "--run-dir", self.dir,
             "--short-id", f"c{n:02d}", "--project-id", str(500 + n),
             "--title", f"t{n}", "--tier", "standard"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for n in range(12)]
        for proc in procs:
            _out, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(len(self.state()["shorts"]), 12)


if __name__ == "__main__":
    unittest.main()
