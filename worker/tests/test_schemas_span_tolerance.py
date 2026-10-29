"""Span checks forgive one centisecond of float drift (job 110299, 25 Oct 2026)."""
import pytest
import schemas
import config


def test_one_centisecond_overrun_passes_despite_float_drift():
    # 7.47 + 0.01 == 7.4799999… in binary floats; 7.48 must still pass.
    assert schemas._check_span("texts[0]", 1.0, 7.48, 7.47, min_len=0.3) == 7.48


def test_real_overrun_still_raises():
    with pytest.raises(schemas.EDLValidationError):
        schemas._check_span("texts[0]", 1.0, 7.50, 7.47, min_len=0.3)


def test_preview_check_has_room_for_many_windows():
    # A 150 s program checked in ~25 windows needs more than 300 s on 2 vCPU.
    assert config.executor_timeout_for("preview_check") >= 900
    assert config.cloudflare_timeout_for("preview_check") >= 900


def test_subject_radius_in_pixels_is_converted():
    from fx_schemas.subject import SubjectFx
    fields = SubjectFx.model_fields
    assert "radius" in fields
    assert abs(SubjectFx._radius_in_pixels(30) - 30 / 1080) < 1e-9
    assert SubjectFx._radius_in_pixels(500) == 0.15
    assert SubjectFx._radius_in_pixels(0.05) == 0.05


def test_tool_commits_more_than_64_operations(monkeypatch):
    import edit_batch
    from tools_ext import _write
    calls = []

    def fake_apply(before, ops, duration, keys, notes=None):
        assert 1 <= len(ops) <= 64
        calls.append(len(ops))
        return {"n": (before.get("n", 0) + len(ops))}

    class Ctx:
        project_id, duration = 1, 10.0
        class db:
            @staticmethod
            def run(fn, *a):
                return set()
        def latest_edl(self):
            return {"json": {"n": 0}, "version": 3}
        def write_edl(self, updated, summary):
            return f"EDL v3 -> v4: {updated['n']}"

    monkeypatch.setattr(edit_batch, "apply_batch", fake_apply)
    out = _write.commit(Ctx(), [{"op": i} for i in range(150)], "many")
    assert calls == [64, 64, 22] and out.endswith(": 150")
