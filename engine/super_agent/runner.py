"""
SuperAgentRunner — executes a super agent for a single invocation.

Unlike the builder agent (AA.py) which writes files, this agent uses
skills/tools to interact with external services, remember facts, and
communicate via messaging platforms.

Reuses BaseAgent from engine/AAgent.py for the core agentic loop.
"""

import json
import os
import sys
import time
import anthropic
import psycopg2
from psycopg2.extras import RealDictCursor

# Add engine directory to path
ENGINE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

from AAgent import BaseAgent
from super_agent.memory_manager import MemoryManager
from super_agent.skills import SKILL_CATALOG, get_skill_class

# Model mapping (same as credits.py)
MODEL_MAP = {
    "V6":     "claude-haiku-4-5-20251001",
    "V6-pro": "claude-sonnet-4-6",
    "V7":     "claude-opus-4-6",
}

BASE_SYSTEM_PROMPT = """You are a Super Agent — an autonomous AI assistant that helps users with tasks, automations, and workflows.

You have access to various skills/tools that let you interact with external services, remember information, search the web, send emails, and more.

## Core Behaviors
- Be helpful, concise, and action-oriented.
- When the user asks you to do something, do it immediately using your available tools.
- Use the remember/recall tools to store and retrieve important information across conversations.
- If you don't have a tool for something, explain what you'd need and suggest alternatives.
- Always confirm actions that have external side effects (sending emails, making API calls) before executing them, unless the user has explicitly asked you to proceed.

## Your Identity
- You are a personal AI agent created and configured by your user.
- You run 24/7 and can be reached via web chat, WhatsApp, Telegram, or Slack.
- You remember things across conversations using your persistent memory.

"""


class SuperAgentRunner:
    def __init__(self, agent_id, thread_id, db_url):
        self.agent_id = agent_id
        self.thread_id = thread_id
        self.db_url = db_url
        self.agent_config = None
        self.base_agent = None

        self._load_agent()

    def _conn(self):
        return psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)

    def _load_agent(self):
        """Load agent config, skills, and memory from DB. Build BaseAgent."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                # Load agent config
                cur.execute(
                    "SELECT * FROM super_agents WHERE agent_id = %s",
                    (self.agent_id,)
                )
                self.agent_config = cur.fetchone()
                if not self.agent_config:
                    raise ValueError(f"Agent {self.agent_id} not found")

                # Load enabled skills
                cur.execute(
                    "SELECT * FROM agent_skills WHERE agent_id = %s AND enabled = TRUE",
                    (self.agent_id,)
                )
                skill_rows = cur.fetchall()
        finally:
            conn.close()

        # Build memory manager
        self.memory_manager = MemoryManager(self.agent_id, self.db_url)

        # Build system prompt
        system_prompt = BASE_SYSTEM_PROMPT
        user_instructions = self.agent_config.get("system_prompt", "")
        if user_instructions:
            system_prompt += f"\n## User Instructions\n{user_instructions}\n"

        memory_context = self.memory_manager.build_context()
        if memory_context:
            system_prompt += f"\n{memory_context}\n"

        # Build tools and tool_map from skills
        all_tools = []
        tool_map = {}
        context = {
            "memory_manager": self.memory_manager,
            "db_url": self.db_url,
            "agent_id": self.agent_id,
        }

        # Always include memory skill
        memory_cls = get_skill_class("memory")
        if memory_cls:
            all_tools.extend(memory_cls.get_tool_definitions())
            tool_map.update(memory_cls.create_handlers({}, context))

        # Add user-enabled skills
        for row in skill_rows:
            skill_cls = get_skill_class(row["skill_type"])
            if not skill_cls or row["skill_type"] == "memory":
                continue
            skill_config = row.get("config") or {}
            if isinstance(skill_config, str):
                skill_config = json.loads(skill_config)
            all_tools.extend(skill_cls.get_tool_definitions())
            tool_map.update(skill_cls.create_handlers(skill_config, context))

        # Resolve model
        v_model = self.agent_config.get("model", "V6")
        anthropic_model = MODEL_MAP.get(v_model, MODEL_MAP["V6"])

        # Build BaseAgent
        client = anthropic.Anthropic()
        self.base_agent = BaseAgent(
            client=client,
            model=anthropic_model,
            system_prompt=system_prompt,
            tools=all_tools if all_tools else None,
            tool_map=tool_map if tool_map else None,
            temperature=1,
            max_tokens=16000,
        )

    def _load_thread_history(self):
        """Load message history for this thread from DB."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT role, content FROM agent_messages
                       WHERE thread_id = %s
                       ORDER BY created_at ASC""",
                    (self.thread_id,)
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        messages = []
        for row in rows:
            messages.append({"role": row["role"], "content": row["content"]})
        return messages

    def _save_message(self, role, content, metadata=None):
        """Save a message to the thread."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO agent_messages (thread_id, role, content, metadata)
                       VALUES (%s, %s, %s, %s)""",
                    (self.thread_id, role, content,
                     json.dumps(metadata or {}))
                )
                # Update thread timestamp
                cur.execute(
                    "UPDATE agent_threads SET updated_at = NOW() WHERE thread_id = %s",
                    (self.thread_id,)
                )
            conn.commit()
        finally:
            conn.close()

    def _create_log(self, trigger_type, trigger_source="", input_summary=""):
        """Create an execution log entry and return its ID."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO agent_logs
                       (agent_id, trigger_type, trigger_source, status, input_summary)
                       VALUES (%s, %s, %s, 'running', %s)
                       RETURNING id""",
                    (self.agent_id, trigger_type, trigger_source,
                     input_summary[:500] if input_summary else "")
                )
                log_id = cur.fetchone()["id"]
            conn.commit()
            return log_id
        finally:
            conn.close()

    def _complete_log(self, log_id, status, output_summary="",
                      tokens_used=0, credits_used=0, error=None, duration_ms=0):
        """Update the execution log with results."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE agent_logs
                       SET status = %s, output_summary = %s,
                           tokens_used = %s, credits_used = %s,
                           error = %s, completed_at = NOW(),
                           duration_ms = %s
                       WHERE id = %s""",
                    (status, output_summary[:1000] if output_summary else "",
                     tokens_used, credits_used, error, duration_ms, log_id)
                )
            conn.commit()
        finally:
            conn.close()

    def run(self, user_message, trigger_type="chat", trigger_source=""):
        """
        Execute the agent for a single user message.

        Returns dict: {"text": str, "tokens": dict, "credits_used": float, "log_id": int}
        """
        start_time = time.time()

        # Create execution log
        log_id = self._create_log(trigger_type, trigger_source, user_message)

        try:
            # Load thread history into the agent
            history = self._load_thread_history()
            self.base_agent.messages = history

            # Save user message to DB
            self._save_message("user", user_message)

            # Run the agentic loop
            text, token_totals, code_changed, stop_data = self.base_agent.chat(user_message)

            # Save assistant response
            self._save_message("assistant", text, metadata={"tokens": token_totals})

            # Calculate credits
            total_tokens = sum(token_totals.values())
            v_model = self.agent_config.get("model", "V6")

            # Deduct credits
            credits_used = self._deduct_credits(
                log_id, token_totals, total_tokens, v_model
            )

            duration_ms = int((time.time() - start_time) * 1000)

            # Update log
            self._complete_log(
                log_id,
                status="completed",
                output_summary=text[:1000] if text else "",
                tokens_used=total_tokens,
                credits_used=credits_used,
                duration_ms=duration_ms
            )

            return {
                "text": text,
                "tokens": token_totals,
                "credits_used": credits_used,
                "log_id": log_id,
            }

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self._complete_log(
                log_id,
                status="failed",
                error=str(e)[:1000],
                duration_ms=duration_ms
            )
            raise

    def _deduct_credits(self, log_id, token_totals, total_tokens, v_model):
        """Deduct credits using the same logic as credits.py."""
        # Import here to use the same pricing logic
        sys.path.insert(0, os.path.join(ENGINE_DIR, "..", "backend"))
        from credits import tokens_to_credits

        credits_used = tokens_to_credits(
            token_totals.get("input", 0),
            token_totals.get("output", 0),
            token_totals.get("cache_write", 0),
            token_totals.get("cache_read", 0),
            model=v_model,
        )

        conn = self._conn()
        try:
            with conn.cursor() as cur:
                user_id = self.agent_config["user_id"]

                # Deduct in order: daily -> bonus -> monthly (same as credits.py)
                cur.execute(
                    "SELECT credits_daily, credits_bonus, credits_monthly FROM users WHERE id = %s FOR UPDATE",
                    (user_id,)
                )
                row = cur.fetchone()
                daily = float(row["credits_daily"] or 0)
                bonus = float(row.get("credits_bonus") or 0)
                monthly = float(row["credits_monthly"] or 0)

                remaining = credits_used

                if daily >= remaining:
                    daily -= remaining
                    remaining = 0
                else:
                    remaining -= daily
                    daily = 0

                if remaining > 0:
                    if bonus >= remaining:
                        bonus -= remaining
                        remaining = 0
                    else:
                        remaining -= bonus
                        bonus = 0

                if remaining > 0:
                    monthly = max(0, monthly - remaining)

                cur.execute(
                    """UPDATE users
                       SET credits_daily = %s,
                           credits_bonus = %s,
                           credits_monthly = %s,
                           credits_balance = %s + %s + %s
                       WHERE id = %s""",
                    (daily, bonus, monthly, daily, bonus, monthly, user_id)
                )

                # Track in agent_credits
                cur.execute(
                    """INSERT INTO agent_credits
                       (agent_id, user_id, log_id, tokens_used, credits_used, model)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (self.agent_id, user_id, log_id,
                     total_tokens, credits_used, v_model)
                )

            conn.commit()
        finally:
            conn.close()

        return credits_used
