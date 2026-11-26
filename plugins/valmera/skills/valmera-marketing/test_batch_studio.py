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
