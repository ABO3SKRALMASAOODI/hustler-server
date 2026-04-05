"""
Abstract base class for all super agent skills.

Each skill provides:
  - SKILL_TYPE: unique identifier string
  - DISPLAY_NAME: human-readable name
  - DESCRIPTION: what the skill does
  - CATEGORY: grouping for the UI catalog
  - CONFIG_SCHEMA: JSON-serializable schema describing config fields
  - get_tool_definitions(): list of Anthropic tool schemas
  - create_handlers(config): dict of {tool_name: callable}
"""


class BaseSkill:
    SKILL_TYPE = "base"
    DISPLAY_NAME = "Base Skill"
    DESCRIPTION = "Abstract base — do not use directly."
    CATEGORY = "general"
    CONFIG_SCHEMA = {}

    @classmethod
    def get_tool_definitions(cls):
        raise NotImplementedError

    @classmethod
    def create_handlers(cls, config, context=None):
        """
        Return {tool_name: handler_function}.
        config: the skill's persisted config dict from the DB.
        context: optional dict with runtime helpers (db_url, agent_id, etc.)
        """
        raise NotImplementedError
