"""P0 #780: a finished export reaches the customer wherever they are.

Customer 2918 (Plus) pressed Download on project 5592 at 08:11 on 10 Nov
2026; the 45-minute source's final took 43 minutes and meanwhile they opened
another project. The MP4 was announced only on 5592's own page, so it never
reached them. GET /exports/ready lists every finished, undownloaded export so
the Studio offers it on any page.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                   # noqa: E402

NOW = datetime(2026, 11, 10, 8, 54, tzinfo=timezone.utc)


class Cur:
    def __init__(self, events, finals):
        self.events, self.finals, self._rows = events, finals, []
        self.sql = []

    def execute(self, sql, args=()):
        self.sql.append(sql)
        if "export_render_done" in sql:
            self._rows = list(self.events)
        else:
            pid, v = args
            self._rows = [a for a in self.finals.get(pid, [])
                          if a["meta"]["edl_version"] == v]

    def fetchall(self):
        return self._rows


def _ev(pid, version, at, title="t"):
    return {"project_id": pid, "title": title, "created_at": at,
            "version": str(version)}


def _final(aid, version):
    return {"id": aid, "meta": {"variant": "final", "edl_version": str(version)}}


def test_finished_export_of_another_project_is_listed(monkeypatch):
    monkeypatch.setattr(video, "_final_gate",
                        lambda cur, pid, uid: (lambda aid, meta, v: True))
    cur = Cur([_ev(5592, 6, NOW, "DJI cut")],
              {5592: [_final(900, 6), _final(901, 6), _final(700, 5)]})
    out = video._ready_exports(cur, 2918)
    assert out == [{"project_id": 5592, "title": "DJI cut", "version": 6,
                    "final_asset_id": 900, "finished_at": NOW.isoformat()}]
    # A download already followed it: the SQL itself excludes the row.
    assert "download_triggered" in cur.sql[0]
    assert "mcp_export_delivered" in cur.sql[0]
    assert "p.user_id = ce.user_id" in cur.sql[0]


def test_stale_final_is_not_offered(monkeypatch):
    monkeypatch.setattr(video, "_final_gate",
                        lambda cur, pid, uid: (lambda aid, meta, v: aid != 900))
    cur = Cur([_ev(1, 2, NOW), _ev(2, 3, NOW - timedelta(minutes=5))],
              {1: [_final(900, 2)], 2: [_final(800, 3)]})
    out = video._ready_exports(cur, 7)
    assert [r["project_id"] for r in out] == [2]


def test_newest_first_and_bounded(monkeypatch):
    monkeypatch.setattr(video, "_final_gate",
                        lambda cur, pid, uid: (lambda aid, meta, v: True))
    evs = [_ev(p, 1, NOW - timedelta(minutes=p)) for p in range(1, 9)]
    cur = Cur(evs, {p: [_final(p * 10, 1)] for p in range(1, 9)})
    out = video._ready_exports(cur, 7)
    assert [r["project_id"] for r in out] == [1, 2, 3, 4, 5]



def test_route_is_registered():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(video.video_bp, url_prefix="/api/video")
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/video/exports/ready" in rules
