import json
import os
from pathlib import Path
import sys

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routes import video
from video_services.direct_edits import command_identity, read_receipt, receipt_meta


@pytest.mark.parametrize("case", json.loads((Path(__file__).parent / "fixtures" / "editor-command-conformance.json").read_text()), ids=lambda c: c["name"])
def test_command_conformance(case):
    result, _ = video._apply_edl_op(case["before"], case["op"], case["args"], {}, src_dur=case["sourceDuration"])
    if case["op"] != "split_keep":
        result, _ = video._reanchor_after_op(case["before"], result, "")
    result = video.wschemas.validate_edl(result, None if result.get("canvas") else case["sourceDuration"]).model_dump()
    assert result == case["after"]
    if case["op"] == "split_keep":
        assert video._program_signature(result) == video._program_signature(case["before"])


def test_receipt_replays_acknowledgement_and_rejects_reused_id():
    data = dict(operation_id="operation-0123456789", op="split_keep", args={"at_program_s": 3}, base_version=8)
    operation_id, fingerprint = command_identity(data)
    ack = {"version": 9, "preview_job_id": None}
    class Cursor:
        def execute(self, sql, args):
            assert args == (17, operation_id)
        def fetchone(self):
            return {"meta": receipt_meta(operation_id, fingerprint, ack)}
    assert read_receipt(Cursor(), 17, operation_id, fingerprint) == ack
    _, changed = command_identity({**data, "base_version": 9})
    with pytest.raises(ValueError, match="different change"):
        read_receipt(Cursor(), 17, operation_id, changed)
