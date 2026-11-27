import fcntl
import subprocess
import sys
from pathlib import Path

import guarded
from measure_voice import share_evidence


def test_measurement_finishes_in_heavy_slot_while_synth_lock_is_held(tmp_path):
    root = tmp_path / ".valmera/week-1106"
    root.mkdir(parents=True)
    client = guarded.ROOT / ".valmera/tools/codex-proxy/client"
    if client.is_dir():
        target = tmp_path / ".valmera/tools/codex-proxy/client"
        target.parent.mkdir(parents=True)
        target.symlink_to(client, target_is_directory=True)
    (root / "heavy.sh").write_text('echo "$1" > "$0.label"\nshift\nexec "$@"\n')
    script = ("import guarded,sys; from pathlib import Path; guarded.ROOT=Path(sys.argv[1]);"
              "sys.exit(guarded.run([sys.executable,'-c','print(42)'],sys.argv[2],'measure-copy',measure_only=True))")
    with (root / ".narrate.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX)
        subprocess.run([sys.executable, "-c", script, str(tmp_path), str(tmp_path / "guard")],
                       cwd=Path(guarded.__file__).parent, check=True, timeout=20)
    assert (root / "heavy.sh.label").read_text().strip() == "measure-copy"
    assert "42" in (tmp_path / "guard/run.log").read_text()


def test_only_transfer_evidence_for_identical_inputs(tmp_path):
    src, dest = tmp_path / "finish", tmp_path / "check"
    src.mkdir()
    dest.mkdir()
    (src / "input.sha256").write_text("matching")
    (src / "heard.json").write_text('{"text":"heard it"}')
    (src / "metrics.json").write_text('{"ok":true}')
    share_evidence(src, dest, "changed")
    assert list(dest.iterdir()) == []
    share_evidence(src, dest, "matching")
    assert (dest / "heard.json").read_text() == (src / "heard.json").read_text()
    assert not (dest / "metrics.json").exists()
