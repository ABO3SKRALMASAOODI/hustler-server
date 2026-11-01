from video_services.jobs import enqueue


class Cursor:
    def __init__(self, active=None):
        self.active = active
        self.result = None
        self.inserts = 0

    def execute(self, sql, params):
        if 'SELECT id FROM video_jobs' in sql:
            assert params == (5251, '18')
            self.result = self.active
        elif 'INSERT INTO video_jobs' in sql:
            self.inserts += 1
            self.result = {'id': 129718}
        else:
            self.result = None

    def fetchone(self):
        return self.result


def test_player_joins_the_editors_running_approval_preview():
    cur = Cursor({'id': 129717})
    got = enqueue(cur, 5251, 2647, 'preview', {'source': 'user_edit', 'edl_version': 18})
    assert got == 129717 and cur.inserts == 0


def test_explicit_force_still_makes_a_fresh_file():
    cur = Cursor({'id': 129717})
    got = enqueue(cur, 5251, 2647, 'preview',
                  {'source': 'user_edit', 'edl_version': 18, 'force': True})
    assert got == 129718 and cur.inserts == 1


def test_no_active_preview_still_enqueues_the_players_request():
    cur = Cursor()
    assert enqueue(cur, 5251, 2647, 'preview',
                   {'source': 'user_edit', 'edl_version': 18}) == 129718
    assert cur.inserts == 1
