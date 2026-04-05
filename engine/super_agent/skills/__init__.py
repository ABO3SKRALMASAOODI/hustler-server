"""
Skill registry — discovers and loads all available skills.
"""

from engine.super_agent.skills.base_skill import BaseSkill
from engine.super_agent.skills.memory_skill import MemorySkill
from engine.super_agent.skills.http_request import HttpRequestSkill
from engine.super_agent.skills.send_email import SendEmailSkill
from engine.super_agent.skills.web_search import WebSearchSkill

# All available skills keyed by skill_type
SKILL_CATALOG = {
    skill.SKILL_TYPE: skill
    for skill in [MemorySkill, HttpRequestSkill, SendEmailSkill, WebSearchSkill]
}


def get_skill_class(skill_type: str):
    return SKILL_CATALOG.get(skill_type)


def get_catalog_info():
    """Return list of skill metadata for the catalog API."""
    return [
        {
            "skill_type": cls.SKILL_TYPE,
            "name": cls.DISPLAY_NAME,
            "description": cls.DESCRIPTION,
            "config_schema": cls.CONFIG_SCHEMA,
            "category": cls.CATEGORY,
        }
        for cls in SKILL_CATALOG.values()
    ]
