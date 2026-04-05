"""
Web Search skill — search the web using a search API.
"""

import json
import os
import requests
from engine.super_agent.skills.base_skill import BaseSkill


class WebSearchSkill(BaseSkill):
    SKILL_TYPE = "web_search"
    DISPLAY_NAME = "Web Search"
    DESCRIPTION = "Search the web for current information, news, and data."
    CATEGORY = "information"
    CONFIG_SCHEMA = {
        "max_results": {
            "type": "integer",
            "description": "Maximum number of search results to return",
            "default": 5
        }
    }

    @classmethod
    def get_tool_definitions(cls):
        return [
            {
                "name": "web_search",
                "description": (
                    "Search the web for current information. "
                    "Returns titles, URLs, and snippets from search results. "
                    "Use this to find up-to-date information, news, or research topics."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query"
                        },
                        "num_results": {
                            "type": "integer",
                            "description": "Number of results (1-10, default 5)"
                        }
                    },
                    "required": ["query"]
                }
            }
        ]

    @classmethod
    def create_handlers(cls, config, context=None):
        max_results = config.get("max_results", 5)

        def web_search(query, num_results=None):
            api_key = os.getenv("SERP_API_KEY")
            if not api_key:
                return "ERROR: Web search not configured (missing SERP_API_KEY). Please ask the admin to set up the search API."

            count = min(num_results or max_results, 10)

            try:
                resp = requests.get(
                    "https://serpapi.com/search",
                    params={
                        "q": query,
                        "api_key": api_key,
                        "num": count,
                        "engine": "google",
                    },
                    timeout=15,
                )

                if resp.status_code != 200:
                    return f"ERROR: Search API returned status {resp.status_code}"

                data = resp.json()
                results = data.get("organic_results", [])

                if not results:
                    return "No search results found."

                lines = []
                for i, r in enumerate(results[:count], 1):
                    title = r.get("title", "No title")
                    link = r.get("link", "")
                    snippet = r.get("snippet", "No description")
                    lines.append(f"{i}. **{title}**\n   {link}\n   {snippet}")

                return "\n\n".join(lines)
            except Exception as e:
                return f"ERROR: Search failed: {type(e).__name__}: {str(e)[:300]}"

        return {"web_search": web_search}
