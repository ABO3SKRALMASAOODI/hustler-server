"""Plan changes must affect the next download, including a cached export."""
from pathlib import Path

import pytest

import config
import db
import export_context
import renderer
import stock


@pytest.mark.parametrize("plan,card", [
    ("free", True), ("ai", True), ("ai_pro", False), ("pro_2026", False),
    ("ai_max", False), ("business", False),
])
def test_render_and_cache_use_the_account_plan(monkeypatch, plan, card):
    monkeypatch.setattr(renderer, "endcard_path", lambda: "/card")
    class Account:
        def run(self, query, user):
            assert query is db.render_account and user == 7
            return plan != "free", plan

    with export_context.job_scope(Account(), {"user_id": 7}):
        assert renderer.outro_seconds(False) == (config.OUTRO_DURATION_S if card else 0)
        assert renderer.outro_current({"outro_v": config.OUTRO_VERSION}, "final") is card
        assert renderer.outro_current({"outro_v": 0}, "final") is not card
        assert renderer.outro_seconds(True) == 0


@pytest.mark.parametrize("width,height,ratio,target", [
    (3840, 2160, "16:9", (3840, 2160)),
    (3840, 2160, "9:16", (2160, 3840)),
    (1920, 1080, "16:9", (1920, 1080)),
])
def test_stock_selects_export_rendition(monkeypatch, width, height, ratio, target):
    class Project:
        def run(self, query, project_id):
            assert query is export_context._source_frame and project_id == 7
            return dict(width=width, height=height, frame={"ratio": ratio}, canvas=None)

    files = [{"link": f"https://cdn/{w}x{h}", "width": w, "height": h}
             for w, h in [(1920, 1080), (3840, 2160), (1080, 1920), (2160, 3840)]
             if (w > h) == (target[0] > target[1])]
    fetched = []
    monkeypatch.setattr(stock.net_fetch, "download", lambda url, *a, **kw: fetched.append(url))
    with export_context.job_scope(Project(), {"project_id": 7}):
        stock.download({"kind": stock.KIND_VIDEO, "_files": files}, "unused", 1920, 1080)
    assert fetched == ["https://cdn/%dx%d" % target]


def test_api_and_worker_share_the_export_contract():
    root = Path(__file__).resolve().parents[2]
    assert (root / "worker/export_branding.py").read_bytes() == (
        root / "backend/export_branding.py").read_bytes()
