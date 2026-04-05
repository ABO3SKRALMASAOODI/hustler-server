"""
Built-in memory skill — lets the agent remember and recall facts.
Always enabled for every super agent.
"""

import json
from engine.super_agent.skills.base_skill import BaseSkill


class MemorySkill(BaseSkill):
    SKILL_TYPE = "memory"
    DISPLAY_NAME = "Memory"
    DESCRIPTION = "Remember and recall facts, preferences, and instructions across conversations."
    CATEGORY = "core"
    CONFIG_SCHEMA = {}

    @classmethod
    def get_tool_definitions(cls):
        return [
            {
                "name": "remember",
                "description": (
                    "Store a fact, preference, or instruction for long-term memory. "
                    "Use a clear, descriptive key (e.g. 'user_timezone', 'preferred_language'). "
                    "Category can be: general, preference, fact, or instruction."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Semantic key for the memory (e.g. 'user_name', 'company_address')"
                        },
                        "value": {
                            "type": "string",
                            "description": "The value to remember"
                        },
                        "category": {
                            "type": "string",
                            "enum": ["general", "preference", "fact", "instruction"],
                            "description": "Category of the memory"
                        }
                    },
                    "required": ["key", "value"]
                }
            },
            {
                "name": "recall",
                "description": (
                    "Search your long-term memory for stored facts. "
                    "Provide a search query to find relevant memories by key or value."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query to find relevant memories"
                        }
                    },
                    "required": ["query"]
                }
            }
        ]

    @classmethod
    def create_handlers(cls, config, context=None):
        ctx = context or {}
        memory_manager = ctx.get("memory_manager")

        def remember(key, value, category="general"):
            if not memory_manager:
                return "ERROR: Memory manager not available."
            memory_manager.store(key, value, category)
            return f"Remembered: {key} = {value}"

        def recall(query):
            if not memory_manager:
                return "ERROR: Memory manager not available."
            results = memory_manager.search(query)
            if not results:
                return "No memories found matching that query."
            lines = [f"- {r['key']}: {r['value']} [{r['category']}]" for r in results]
            return "Found memories:\n" + "\n".join(lines)

        return {"remember": remember, "recall": recall}
