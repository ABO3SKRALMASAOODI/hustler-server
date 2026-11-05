"""The credit monitor must work through PostgreSQL transaction poolers."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from scripts import claude_credit_probe as probe


class ProbeConnectionTest(unittest.TestCase):
    def check_connection(self, dry_run):
        events = []
        conn = MagicMock()
        conn.set_session.side_effect = lambda **kw: events.append(('session', kw))
        conn.cursor.return_value.__enter__.return_value.execute.side_effect = (
            lambda sql: events.append(('sql', sql)))

        def connect(_dsn, **kwargs):
            if 'options' in kwargs:
                raise RuntimeError('unsupported startup parameter in options: statement_timeout')
            events.append(('connect', kwargs))
            return conn

        with patch.dict('os.environ', {'PRODUCTION_DATABASE_URL': 'postgresql://stub/stub',
                                      'ANTHROPIC_API_KEY': ''}), \
                patch('psycopg2.connect', side_effect=connect), \
                patch.object(probe, 'run', return_value={'result': 'nothing to do'}) as run, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main(['--dry-run'] if dry_run else []), 0)
        self.assertEqual(events[-1], ('sql', 'SET LOCAL statement_timeout = 30000'))
        if dry_run:
            self.assertEqual(events[1], ('session', {'readonly': True}))
        else:
            conn.set_session.assert_not_called()
        run.assert_called_once_with(conn, '', dry_run=dry_run)
        conn.close.assert_called_once()

    def test_readonly_probe_sets_timeout_after_readonly_session(self):
        self.check_connection(True)

    def test_scheduled_probe_uses_transaction_timeout_not_startup_options(self):
        self.check_connection(False)
