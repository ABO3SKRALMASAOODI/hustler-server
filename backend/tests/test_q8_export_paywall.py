"""Q8 (30 Oct): the export paywall says what the trial user is about to keep.

It used to answer a trial account's Download with the free plan's line,
"You're on the free plan. Upgrade to start editing." right after the account
had been editing for minutes: a confusing state at the moment that decides
the subscription. The line is now built from the version's EDL alone, and
the plans still come from the server's offer.
"""
import datetime
import inspect
import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video  # noqa: E402
from tests.test_plans_message import Chat  # noqa: E402

FIRST_CUT = {
    "keep": [[2.0, 14.5], [14.9, 26.2], [26.6, 38.1]],
    "frame": {"ratio": "9:16", "mode": "crop"},
    "captions": {"mode": "from_transcript", "style": {"preset": "reels"}},
    "motion": [{"id": "mg1", "template": "hook_title", "params": {"text": "It will solve everything"}}],
    "fx": [{"id": "br1", "type": "broll"}, {"id": "br2", "type": "broll"}],
}


def test_the_value_line_names_only_what_the_edit_holds():
    line = video._export_value_line(FIRST_CUT)
    assert line == ("Your 35-second vertical edit is ready to download: a hook "
                    "title, captions, 2 B-roll cutaways and the pauses cut. "
                    "Choose a plan and the full-quality export starts right away.")
    bare = video._export_value_line({"keep": [[0.0, 12.0]]})
    assert bare == ("Your 12-second edit is ready to download. Choose a plan "
                    "and the full-quality export starts right away.")
    assert video._export_value_line({"keep": []}) is None
    assert video._export_value_line(None) is None
    off = dict(FIRST_CUT, captions={"mode": "off"}, fx=[], motion=[],
               keep=[[0.0, 20.0]])
    assert "captions" not in video._export_value_line(off)


def test_the_export_reply_carries_the_line_and_a_repeat_updates_it():
    chat = Chat()
    first = video._record_plans_message(chat, 3, "export",
                                        blocked_prompt={"export_version": 7, "text": ""},
                                        content="Your 35-second vertical edit is ready to download.")
    assert first["content"].startswith("Your 35-second vertical edit")
    assert first["meta"]["kind"] == "subscription_plans"
    assert first["meta"]["blocked_prompt"]["export_version"] == 7
    assert first["meta"]["subscription_offer"]["plans"]    # server plans

    class Chat2(Chat):
        def execute(self, sql, params=None):
            command = " ".join(sql.split())
            if command.startswith("UPDATE chat_messages SET content"):
                for row in self.rows:
                    if row["id"] == params[1]:
                        row["content"] = params[0]
                self._result = None
                return
            return super().execute(sql, params)
    chat2 = Chat2(chat.rows)
    again = video._record_plans_message(chat2, 3, "export",
                                        blocked_prompt={"export_version": 8, "text": ""},
                                        content="Your 31-second vertical edit is ready to download.")
    assert chat2.inserts == 0 and again["id"] == first["id"]
    assert again["content"].startswith("Your 31-second")
    assert chat2.rows[-1]["meta"]["blocked_prompt"]["export_version"] == 8


def test_a_request_reply_keeps_the_free_plan_line():
    chat = Chat()
    message = video._record_plans_message(chat, 3, "chat")
    assert message["content"] == "You’re on the free plan. Upgrade to start editing."


def test_the_trial_export_gate_builds_its_line_from_the_version():
    gate = inspect.getsource(video._request_final)
    assert "_export_value_line" in gate and '"export"' in gate
    assert "SELECT json FROM edls WHERE project_id = %s" in " ".join(gate.split()) \
        or "SELECT json FROM edls" in gate


def test_a_stock_cutaway_the_edit_does_not_use_leaves_the_media_list():
    used = {"storage_key": "fetched/5/broll/a.jpg", "meta": {"broll": True, "stock": True}}
    dropped = {"storage_key": "fetched/5/broll/b.jpg", "meta": {"broll": True, "stock": True}}
    upload = {"storage_key": "media/5/mine.mp4", "meta": {"filename": "mine.mp4"}}
    edl = {"version": 4, "json": {"fx": [{"id": "br1", "type": "broll",
                                          "source": {"kind": "asset", "asset_key": "fetched/5/broll/a.jpg"}}]}}
    assert video._unused_broll(used, edl) is False
    assert video._unused_broll(dropped, edl) is True
    assert video._unused_broll(upload, edl) is False
    assert video._unused_broll(dropped, None) is False
