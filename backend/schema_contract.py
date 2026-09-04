"""Relations required before the backend can truthfully report readiness."""

DATABASE_REQUIRED_RELATIONS = (
    "users", "projects", "assets", "indexes", "edls", "video_jobs",
    "payments", "job_credits", "client_events", "page_visits",
    "onboarding_responses", "plan_intents", "remote_executions",
    "mcp_tokens", "mcp_oauth_clients", "mcp_oauth_tokens", "mcp_catalog",
)
