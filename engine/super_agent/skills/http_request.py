"""
HTTP Request skill — lets the agent make HTTP requests to external APIs.
"""

import json
import requests as req
from engine.super_agent.skills.base_skill import BaseSkill


class HttpRequestSkill(BaseSkill):
    SKILL_TYPE = "http_request"
    DISPLAY_NAME = "HTTP Requests"
    DESCRIPTION = "Make HTTP GET/POST/PUT/DELETE requests to external APIs and websites."
    CATEGORY = "integrations"
    CONFIG_SCHEMA = {
        "allowed_domains": {
            "type": "array",
            "description": "Optional list of allowed domains. Leave empty to allow all.",
            "default": []
        }
    }

    @classmethod
    def get_tool_definitions(cls):
        return [
            {
                "name": "http_request",
                "description": (
                    "Make an HTTP request to an external URL. "
                    "Supports GET, POST, PUT, DELETE methods. "
                    "Use this to fetch data from APIs, check website status, "
                    "or send data to external services."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "method": {
                            "type": "string",
                            "enum": ["GET", "POST", "PUT", "DELETE"],
                            "description": "HTTP method"
                        },
                        "url": {
                            "type": "string",
                            "description": "Full URL to request (must start with https://)"
                        },
                        "headers": {
                            "type": "object",
                            "description": "Optional HTTP headers as key-value pairs",
                            "additionalProperties": {"type": "string"}
                        },
                        "body": {
                            "type": "string",
                            "description": "Optional request body (JSON string for POST/PUT)"
                        }
                    },
                    "required": ["method", "url"]
                }
            }
        ]

    @classmethod
    def create_handlers(cls, config, context=None):
        allowed_domains = config.get("allowed_domains", [])

        def http_request(method, url, headers=None, body=None):
            if not url.startswith("https://"):
                return "ERROR: Only HTTPS URLs are allowed for security."

            if allowed_domains:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
                if not any(domain.endswith(d) for d in allowed_domains):
                    return f"ERROR: Domain '{domain}' is not in the allowed list."

            try:
                kwargs = {
                    "method": method,
                    "url": url,
                    "headers": headers or {},
                    "timeout": 30,
                }
                if body and method in ("POST", "PUT"):
                    kwargs["headers"].setdefault("Content-Type", "application/json")
                    kwargs["data"] = body

                resp = req.request(**kwargs)

                # Truncate large responses
                content = resp.text
                if len(content) > 10000:
                    content = content[:10000] + "\n... [truncated, total length: {}]".format(len(resp.text))

                return json.dumps({
                    "status_code": resp.status_code,
                    "headers": dict(resp.headers),
                    "body": content
                })
            except req.exceptions.Timeout:
                return "ERROR: Request timed out after 30 seconds."
            except req.exceptions.ConnectionError:
                return "ERROR: Could not connect to the server."
            except Exception as e:
                return f"ERROR: {type(e).__name__}: {str(e)[:300]}"

        return {"http_request": http_request}
