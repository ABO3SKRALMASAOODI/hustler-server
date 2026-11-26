from concurrent.futures import ThreadPoolExecutor
import json
import time

import studio


def test_parallel_recordings_start_one_stack_and_a_resume_reuses_it(tmp_path):
    calls = []
    class Stack:
        def run_dir(self, run):
            return tmp_path / run

        def load_stack(self, run):
            return json.loads((self.run_dir(run) / "stack.json").read_text())

        def load_settings(self, path):
            return json.loads(path.read_text())

        def up(self, run, settings):
            calls.append(settings["slots"]["agent"])
            time.sleep(.03)
            (self.run_dir(run) / "stack.json").write_text('{"state":"up"}')
    settings = tmp_path / "settings.json"
    settings.write_text('{"slots":{"agent":2}}')
    def attach(_):
        studio.ensure_batch_stack(Stack(), "shared", settings, None)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(attach, (0, 1)))
    attach(2)
    assert calls == [2]


def test_tutorial_recut_uses_the_same_checked_studio_session(tmp_path, monkeypatch):
    import json
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    import proof
    source = tmp_path / "record.json"
    source.write_text(json.dumps(dict(sid="session-a", source=dict(t0=2, t1=12))))
    monkeypatch.setattr(proof, "ui_shots", lambda record: {"typing": ["actual.png"]})
    card = next(iter(proof.cards()))
    receipts = dict(asks=[{"text": "show the two layouts"}], exports=[{"sha256": "original"}])
    script = dict(record=str(source), receipts=receipts, script=dict(format="tutorial"),
                  idea_full=dict(title="Show the two layouts"), style=dict(id=card), export_check=dict(ok=True))
    checks = dict(ok=False, marketing=dict(picture_check=dict(misfits_left=["wrong picture"])))
    recut = proof.recorded_recut(script, checks)
    assert recut["record"] == str(source) and recut["receipts"] == receipts
    assert recut["recut"]["recording"] == "session-a" and recut["script"]["format"] == "showcase"
    assert proof.recorded_recut(recut, checks) is None
    assert proof.recorded_recut(script, dict(ok=True)) is None


def test_showcase_recut_shows_every_followup_and_the_original_send(tmp_path, monkeypatch):
    import proof
    ui = dict(dir=str(tmp_path), typing=["type_00_00.png"], sent="type_00_sent.png")
    monkeypatch.setattr(proof, "ui_shots", lambda record: ui)
    record = dict(asks=[{"text": "Add titles"}, {"text": "Move lower"}])
    names = ["type_00_00.png", "type_00_01.png", "type_00_02.png", "type_00_sent.png",
             "type_01_00.png", "type_01_01.png", "type_01_02.png", "type_01_sent.png"]
    for name in names:
        (tmp_path / name).touch()
    shots = proof.showcase_ui(record)
    assert shots["typing"] == names[:4] + names[5:7]
    assert shots["sent"] == "type_01_sent.png"
    (tmp_path / "type_01_01.png").unlink()
    assert proof.showcase_ui(record) is None
