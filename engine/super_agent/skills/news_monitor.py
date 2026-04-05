"""
News Monitor skill — track news, RSS feeds, and trending topics.

Fetches news from multiple sources, monitors specific topics,
and delivers curated summaries. Stay informed without the noise.
"""

import json
import os
import re
import requests
from datetime import datetime
from engine.super_agent.skills.base_skill import BaseSkill


class NewsMonitorSkill(BaseSkill):
    SKILL_TYPE = "news_monitor"
    DISPLAY_NAME = "News Monitor"
    DESCRIPTION = "Monitor news, track topics, read RSS feeds, and get real-time updates. Stay informed on anything that matters."
    CATEGORY = "information"
    CONFIG_SCHEMA = {
        "news_api_key": {"type": "string", "description": "NewsAPI.org API key (optional, uses env var fallback)"},
    }

    @classmethod
    def get_tool_definitions(cls):
        return [
            {
                "name": "get_top_news",
                "description": (
                    "Get top headlines from major news sources. "
                    "Filter by country or category (business, technology, sports, health, science, entertainment)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": ["general", "business", "technology", "sports", "health", "science", "entertainment"],
                            "description": "News category",
                        },
                        "country": {"type": "string", "description": "Country code (e.g., us, gb, ae, sa). Default: us"},
                        "count": {"type": "integer", "description": "Number of articles (1-20, default 10)"},
                    },
                },
            },
            {
                "name": "search_news",
                "description": (
                    "Search news articles by keyword or phrase. "
                    "Returns recent articles matching your query from thousands of sources. "
                    "Perfect for tracking specific topics, companies, or events."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keywords or phrase"},
                        "sort_by": {
                            "type": "string",
                            "enum": ["relevancy", "publishedAt", "popularity"],
                            "description": "Sort order (default: publishedAt)",
                        },
                        "count": {"type": "integer", "description": "Number of articles (1-20, default 10)"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "read_rss_feed",
                "description": (
                    "Read an RSS/Atom feed and return the latest entries. "
                    "Works with any RSS feed URL — blogs, podcasts, news sites, etc."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "feed_url": {"type": "string", "description": "RSS or Atom feed URL"},
                        "count": {"type": "integer", "description": "Number of entries to return (default 10)"},
                    },
                    "required": ["feed_url"],
                },
            },
        ]

    @classmethod
    def create_handlers(cls, config, context=None):
        api_key = config.get("news_api_key") or os.getenv("NEWS_API_KEY")

        def get_top_news(category="general", country="us", count=10):
            if not api_key:
                return "ERROR: News API key not configured. Set NEWS_API_KEY env var or configure in skill settings."

            count = min(max(count, 1), 20)
            try:
                resp = requests.get(
                    "https://newsapi.org/v2/top-headlines",
                    params={"country": country, "category": category, "pageSize": count, "apiKey": api_key},
                    timeout=15,
                )
                if resp.status_code != 200:
                    return f"ERROR: News API returned {resp.status_code}"

                articles = resp.json().get("articles", [])
                if not articles:
                    return f"No top headlines found for {category} in {country}."

                lines = [f"**Top {category.title()} Headlines ({country.upper()}):**\n"]
                for i, a in enumerate(articles, 1):
                    title = a.get("title", "No title")
                    source = a.get("source", {}).get("name", "Unknown")
                    desc = a.get("description", "")[:150]
                    url = a.get("url", "")
                    pub = a.get("publishedAt", "")[:10]

                    lines.append(f"{i}. **{title}**")
                    lines.append(f"   {source} | {pub}")
                    if desc:
                        lines.append(f"   {desc}")
                    if url:
                        lines.append(f"   {url}")
                    lines.append("")

                return "\n".join(lines)
            except Exception as e:
                return f"ERROR: {str(e)[:300]}"

        def search_news(query, sort_by="publishedAt", count=10):
            if not api_key:
                return "ERROR: News API key not configured."

            count = min(max(count, 1), 20)
            try:
                resp = requests.get(
                    "https://newsapi.org/v2/everything",
                    params={"q": query, "sortBy": sort_by, "pageSize": count, "apiKey": api_key, "language": "en"},
                    timeout=15,
                )
                if resp.status_code != 200:
                    return f"ERROR: News API returned {resp.status_code}"

                articles = resp.json().get("articles", [])
                if not articles:
                    return f"No news found for '{query}'."

                lines = [f"**News Search: '{query}'** ({len(articles)} results)\n"]
                for i, a in enumerate(articles, 1):
                    title = a.get("title", "No title")
                    source = a.get("source", {}).get("name", "Unknown")
                    desc = a.get("description", "")[:200]
                    url = a.get("url", "")
                    pub = a.get("publishedAt", "")[:10]

                    lines.append(f"{i}. **{title}**")
                    lines.append(f"   {source} | {pub}")
                    if desc:
                        lines.append(f"   {desc}")
                    if url:
                        lines.append(f"   {url}")
                    lines.append("")

                return "\n".join(lines)
            except Exception as e:
                return f"ERROR: {str(e)[:300]}"

        def read_rss_feed(feed_url, count=10):
            if not feed_url.startswith("https://"):
                return "ERROR: Only HTTPS feed URLs allowed."

            count = min(max(count, 1), 30)
            try:
                resp = requests.get(feed_url, timeout=15, headers={
                    "User-Agent": "ValmeraAgent/1.0"
                })
                resp.raise_for_status()
                content = resp.text

                # Simple RSS/Atom parser
                items = []

                # Try RSS <item> format
                item_pattern = re.compile(r'<item>(.*?)</item>', re.DOTALL)
                for match in item_pattern.finditer(content):
                    item_xml = match.group(1)
                    title = re.search(r'<title[^>]*>(.*?)</title>', item_xml, re.DOTALL)
                    link = re.search(r'<link[^>]*>(.*?)</link>', item_xml, re.DOTALL)
                    desc = re.search(r'<description[^>]*>(.*?)</description>', item_xml, re.DOTALL)
                    pub = re.search(r'<pubDate[^>]*>(.*?)</pubDate>', item_xml, re.DOTALL)

                    items.append({
                        "title": re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', title.group(1).strip()) if title else "No title",
                        "link": link.group(1).strip() if link else "",
                        "description": re.sub(r'<[^>]+>', '', re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', desc.group(1).strip())) if desc else "",
                        "published": pub.group(1).strip() if pub else "",
                    })

                # Try Atom <entry> format if no items found
                if not items:
                    entry_pattern = re.compile(r'<entry>(.*?)</entry>', re.DOTALL)
                    for match in entry_pattern.finditer(content):
                        entry_xml = match.group(1)
                        title = re.search(r'<title[^>]*>(.*?)</title>', entry_xml, re.DOTALL)
                        link = re.search(r'<link[^>]*href=["\']([^"\']+)["\']', entry_xml)
                        summary = re.search(r'<summary[^>]*>(.*?)</summary>', entry_xml, re.DOTALL)
                        updated = re.search(r'<updated[^>]*>(.*?)</updated>', entry_xml, re.DOTALL)

                        items.append({
                            "title": title.group(1).strip() if title else "No title",
                            "link": link.group(1).strip() if link else "",
                            "description": re.sub(r'<[^>]+>', '', summary.group(1).strip())[:200] if summary else "",
                            "published": updated.group(1).strip() if updated else "",
                        })

                if not items:
                    return "No entries found in the feed. Make sure it's a valid RSS or Atom feed."

                lines = [f"**RSS Feed** ({len(items)} entries)\n"]
                for i, item in enumerate(items[:count], 1):
                    lines.append(f"{i}. **{item['title']}**")
                    if item["published"]:
                        lines.append(f"   {item['published']}")
                    if item["description"]:
                        lines.append(f"   {item['description'][:150]}")
                    if item["link"]:
                        lines.append(f"   {item['link']}")
                    lines.append("")

                return "\n".join(lines)
            except Exception as e:
                return f"ERROR: {str(e)[:300]}"

        return {
            "get_top_news": get_top_news,
            "search_news": search_news,
            "read_rss_feed": read_rss_feed,
        }
