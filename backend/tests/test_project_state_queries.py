"""Project-state recovery queries stay scoped to the active main upload."""

from video_services import project_state


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row


def test_index_job_state_is_scoped_to_the_active_asset():
    cur = FakeCursor({
        "id": 91, "state": "failed", "progress": 37,
        "error": "capacity", "updated_at": "then",
        "active": False, "recent_count": 1,
    })

    state = project_state.index_job_state(cur, 7, 44)

    assert state == {
        "id": 91, "state": "failed", "progress": 37,
        "error": "capacity", "updated_at": "then",
        "active": False, "recent_count": 1,
    }
    sql, params = cur.executed[0]
    assert "payload->>'asset_id' = %s" in sql
    assert params == (7, "44")


def test_missing_asset_has_no_index_recovery_state():
    cur = FakeCursor(None)

    state = project_state.index_job_state(cur, 7, None)

    assert state["id"] is None
    assert state["active"] is False
    assert state["recent_count"] == 0
    assert cur.executed == []


def test_empty_aggregate_is_normalized_for_recovery_logic():
    cur = FakeCursor({
        "id": None, "state": None, "progress": None,
        "error": None, "updated_at": None,
        "active": None, "recent_count": 0,
    })

    state = project_state.index_job_state(cur, 7, 44)

    assert state["id"] is None
    assert state["active"] is False
    assert state["recent_count"] == 0
