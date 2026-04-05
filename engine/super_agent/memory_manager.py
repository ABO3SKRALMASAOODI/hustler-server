"""
Memory Manager — handles persistent key-value memory storage for super agents.

Backed by the agent_memory PostgreSQL table.
"""

import psycopg2
from psycopg2.extras import RealDictCursor


class MemoryManager:
    MAX_MEMORIES = 500

    def __init__(self, agent_id, db_url):
        self.agent_id = agent_id
        self.db_url = db_url

    def _conn(self):
        return psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)

    def store(self, key, value, category="general"):
        """Store or update a memory entry (upsert)."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                # Check total count
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM agent_memory WHERE agent_id = %s",
                    (self.agent_id,)
                )
                count = cur.fetchone()["cnt"]

                if count >= self.MAX_MEMORIES:
                    # Delete oldest entry to make room
                    cur.execute(
                        """DELETE FROM agent_memory
                           WHERE id = (
                               SELECT id FROM agent_memory
                               WHERE agent_id = %s
                               ORDER BY updated_at ASC
                               LIMIT 1
                           )""",
                        (self.agent_id,)
                    )

                cur.execute(
                    """INSERT INTO agent_memory (agent_id, key, value, category, updated_at)
                       VALUES (%s, %s, %s, %s, NOW())
                       ON CONFLICT (agent_id, key)
                       DO UPDATE SET value = EXCLUDED.value,
                                     category = EXCLUDED.category,
                                     updated_at = NOW()""",
                    (self.agent_id, key, value, category)
                )
            conn.commit()
        finally:
            conn.close()

    def search(self, query, limit=20):
        """Search memories by key or value substring."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                pattern = f"%{query}%"
                cur.execute(
                    """SELECT id, key, value, category, updated_at
                       FROM agent_memory
                       WHERE agent_id = %s
                         AND (key ILIKE %s OR value ILIKE %s)
                       ORDER BY updated_at DESC
                       LIMIT %s""",
                    (self.agent_id, pattern, pattern, limit)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def get_all(self, limit=50):
        """Get all memories for context injection into system prompt."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT key, value, category
                       FROM agent_memory
                       WHERE agent_id = %s
                       ORDER BY updated_at DESC
                       LIMIT %s""",
                    (self.agent_id, limit)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def build_context(self):
        """Build a text block of all memories to inject into the system prompt."""
        memories = self.get_all()
        if not memories:
            return ""

        lines = ["## Your Persistent Memory", ""]
        for m in memories:
            lines.append(f"- [{m['category']}] {m['key']}: {m['value']}")

        return "\n".join(lines)

    def delete(self, memory_id):
        """Delete a specific memory entry by ID."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM agent_memory WHERE id = %s AND agent_id = %s",
                    (memory_id, self.agent_id)
                )
            conn.commit()
        finally:
            conn.close()

    def list_all(self):
        """List all memories (for the management UI)."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, key, value, category, created_at, updated_at
                       FROM agent_memory
                       WHERE agent_id = %s
                       ORDER BY updated_at DESC""",
                    (self.agent_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()
