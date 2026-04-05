"""
Skill registry — discovers and loads all available skills.
"""

from engine.super_agent.skills.base_skill import BaseSkill
from engine.super_agent.skills.memory_skill import MemorySkill
from engine.super_agent.skills.http_request import HttpRequestSkill
from engine.super_agent.skills.send_email import SendEmailSkill
from engine.super_agent.skills.web_search import WebSearchSkill
from engine.super_agent.skills.send_whatsapp import SendWhatsAppSkill
from engine.super_agent.skills.send_telegram import SendTelegramSkill
from engine.super_agent.skills.send_slack import SendSlackSkill
from engine.super_agent.skills.google_calendar import GoogleCalendarSkill
from engine.super_agent.skills.gmail_skill import GmailSkill
from engine.super_agent.skills.url_scraper import UrlScraperSkill
from engine.super_agent.skills.data_analyzer import DataAnalyzerSkill
from engine.super_agent.skills.news_monitor import NewsMonitorSkill
from engine.super_agent.skills.workflow_engine import WorkflowEngineSkill
from engine.super_agent.skills.notification_hub import NotificationHubSkill

# All available skills keyed by skill_type
SKILL_CATALOG = {
    skill.SKILL_TYPE: skill
    for skill in [
        MemorySkill,
        HttpRequestSkill,
        SendEmailSkill,
        WebSearchSkill,
        SendWhatsAppSkill,
        SendTelegramSkill,
        SendSlackSkill,
        GoogleCalendarSkill,
        GmailSkill,
        UrlScraperSkill,
        DataAnalyzerSkill,
        NewsMonitorSkill,
        WorkflowEngineSkill,
        NotificationHubSkill,
    ]
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
