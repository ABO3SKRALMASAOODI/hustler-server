"""Paddle cents must remain visible when PostgreSQL returns NUMERIC sums."""
from decimal import Decimal

from routes import admin


def test_collected_revenue_accepts_postgres_numeric_and_matches_customer_scope():
    class Cursor:
        def __init__(self):
            self.sql = []
            self.rows = iter([{"t": "payments"},
                              {"cents": Decimal("21750"), "n": 15},
                              {"cents": Decimal("750"), "n": 1}])
        def execute(self, sql): self.sql.append(sql)
        def fetchone(self): return next(self.rows)
    cur = Cursor()
    result = admin._collected(cur, 7)
    assert result["collected_usd"] == 217.50
    assert result["collected_payments"] == 15
    assert result["failed_usd"] == 7.50
    for sql in cur.sql[1:]:
        assert admin._scope("u") in sql
        assert "p.currency = 'USD'" in sql
        assert "p.occurred_at >=" in sql
    assert "'paid'" in cur.sql[1]
    assert "'canceled'" not in cur.sql[2]
