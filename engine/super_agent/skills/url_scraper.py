"""
URL Scraper skill — extract, summarize, and analyze web page content.

Fetches web pages, strips HTML to readable text, and provides
structured summaries. The agent's eyes on the internet.
"""

import json
import re
import requests
from engine.super_agent.skills.base_skill import BaseSkill


class UrlScraperSkill(BaseSkill):
    SKILL_TYPE = "url_scraper"
    DISPLAY_NAME = "Web Page Reader"
    DESCRIPTION = "Read and extract content from any web page. Get summaries, extract data, monitor pages for changes."
    CATEGORY = "information"
    CONFIG_SCHEMA = {}

    @classmethod
    def get_tool_definitions(cls):
        return [
            {
                "name": "read_webpage",
                "description": (
                    "Fetch a web page and extract its readable text content. "
                    "Strips HTML, scripts, and styles to return clean text. "
                    "Use this to read articles, documentation, product pages, etc. "
                    "Maximum 15KB of text returned."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Full URL to fetch (must be https://)"},
                        "extract": {
                            "type": "string",
                            "enum": ["full_text", "links", "headings", "metadata"],
                            "description": "What to extract: full_text (default), links (all URLs), headings (h1-h6), metadata (title, description, og tags)",
                        },
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "compare_pages",
                "description": (
                    "Fetch two web pages and compare their content. "
                    "Useful for monitoring changes, comparing products, or tracking updates."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url1": {"type": "string", "description": "First URL to compare"},
                        "url2": {"type": "string", "description": "Second URL to compare"},
                    },
                    "required": ["url1", "url2"],
                },
            },
        ]

    @classmethod
    def create_handlers(cls, config, context=None):

        def _fetch(url):
            if not url.startswith("https://"):
                return None, "ERROR: Only HTTPS URLs allowed."
            try:
                resp = requests.get(url, timeout=20, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ValmeraAgent/1.0)"
                })
                resp.raise_for_status()
                return resp.text, None
            except requests.exceptions.Timeout:
                return None, "ERROR: Request timed out."
            except Exception as e:
                return None, f"ERROR: {str(e)[:300]}"

        def _html_to_text(html):
            """Strip HTML to readable text."""
            # Remove script and style tags
            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            # Convert common elements
            text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
            text = re.sub(r'</(p|div|h[1-6]|li|tr)>', '\n', text, flags=re.IGNORECASE)
            text = re.sub(r'<(h[1-6])[^>]*>', '\n## ', text, flags=re.IGNORECASE)
            text = re.sub(r'<li[^>]*>', '- ', text, flags=re.IGNORECASE)
            # Remove remaining tags
            text = re.sub(r'<[^>]+>', '', text)
            # Clean up whitespace
            text = re.sub(r'\n{3,}', '\n\n', text)
            text = re.sub(r' {2,}', ' ', text)
            # Decode entities
            text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
            return text.strip()

        def _extract_links(html):
            links = re.findall(r'href=["\']([^"\']+)["\']', html)
            return [l for l in links if l.startswith("http")]

        def _extract_headings(html):
            headings = re.findall(r'<h([1-6])[^>]*>(.*?)</h\1>', html, re.DOTALL | re.IGNORECASE)
            return [{"level": int(h[0]), "text": re.sub(r'<[^>]+>', '', h[1]).strip()} for h in headings]

        def _extract_metadata(html):
            title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else ""

            meta = {}
            for m in re.finditer(r'<meta\s+([^>]+)>', html, re.IGNORECASE):
                attrs = m.group(1)
                name_match = re.search(r'(?:name|property)=["\']([^"\']+)["\']', attrs)
                content_match = re.search(r'content=["\']([^"\']+)["\']', attrs)
                if name_match and content_match:
                    meta[name_match.group(1)] = content_match.group(1)

            return {"title": title, "meta": meta}

        def read_webpage(url, extract="full_text"):
            html, error = _fetch(url)
            if error:
                return error

            if extract == "links":
                links = _extract_links(html)
                if not links:
                    return "No links found on the page."
                return f"Found {len(links)} links:\n" + "\n".join(f"- {l}" for l in links[:100])

            elif extract == "headings":
                headings = _extract_headings(html)
                if not headings:
                    return "No headings found."
                return "Page structure:\n" + "\n".join(
                    f"{'  ' * (h['level'] - 1)}{'#' * h['level']} {h['text']}" for h in headings
                )

            elif extract == "metadata":
                meta = _extract_metadata(html)
                lines = [f"**Title:** {meta['title']}"]
                for k, v in meta["meta"].items():
                    lines.append(f"**{k}:** {v}")
                return "\n".join(lines)

            else:  # full_text
                text = _html_to_text(html)
                if len(text) > 15000:
                    text = text[:15000] + "\n\n... [content truncated at 15KB]"
                return text if text else "Page appears to have no readable text content."

        def compare_pages(url1, url2):
            html1, err1 = _fetch(url1)
            if err1:
                return f"Failed to fetch URL 1: {err1}"
            html2, err2 = _fetch(url2)
            if err2:
                return f"Failed to fetch URL 2: {err2}"

            text1 = _html_to_text(html1)[:5000]
            text2 = _html_to_text(html2)[:5000]

            meta1 = _extract_metadata(html1)
            meta2 = _extract_metadata(html2)

            result = f"**Page 1:** {meta1['title']}\n"
            result += f"Content length: {len(text1)} chars\n\n"
            result += f"**Page 2:** {meta2['title']}\n"
            result += f"Content length: {len(text2)} chars\n\n"

            # Simple comparison
            words1 = set(text1.lower().split())
            words2 = set(text2.lower().split())
            common = words1 & words2
            only1 = words1 - words2
            only2 = words2 - words1

            result += f"**Comparison:**\n"
            result += f"- Common words: {len(common)}\n"
            result += f"- Unique to page 1: {len(only1)}\n"
            result += f"- Unique to page 2: {len(only2)}\n\n"

            result += f"**Page 1 preview:**\n{text1[:2000]}\n\n"
            result += f"**Page 2 preview:**\n{text2[:2000]}"

            return result

        return {"read_webpage": read_webpage, "compare_pages": compare_pages}
