from openai import OpenAI
from dotenv import load_dotenv
from colorama import Fore, Style, Back
from AAgent import BaseAgent
from deployer import run_install_command
load_dotenv()
from pathlib import Path
import os
import subprocess
import shutil
import json
import anthropic
import requests
import fal_client
import time

# ── fal.ai authentication ──────────────────────────────────────────────────
# Set FAL_KEY in your .env file. That's all that's needed.
fal_client.api_key = os.getenv("FAL_KEY", "")

client = anthropic.Anthropic(
    api_key=os.getenv("MIMO_API_KEY"),          # ← was ANTHROPIC_API_KEY
    base_url="https://api.xiaomimimo.com/anthropic",   # ← was api.anthropic.com
)



# ══════════════════════════════════════════════════════════════════════════════
#  TASK TRACKER CLASS
# ══════════════════════════════════════════════════════════════════════════════
class TaskTracker:
    def __init__(self, workspace=None):
        self.tasks = {}
        self.task_notes = {}
        self._next_id = 1
        self.workspace = workspace

    def _flush(self):
        if not self.workspace:
            return
        try:
            tasks_list = [t for t in self.tasks.values()]
            with open(os.path.join(self.workspace, "tasks.json"), "w") as f:
                json.dump(tasks_list, f)
        except Exception as e:
            print(f"[task_tracker] Flush error: {e}")

    def create_task(self, title: str, description: str = "") -> str:
        task_id = str(self._next_id)
        self._next_id += 1
        self.tasks[task_id] = {
            "id": task_id,
            "title": title,
            "description": description,
            "status": "todo",
            "created_at": time.time()
        }
        self.task_notes[task_id] = []
        print(f"[task_tracker] Created task {task_id}: {title}")
        self._flush()
        return task_id

    def update_task_title(self, task_id: str, new_title: str) -> str:
        if task_id not in self.tasks:
            return f"TASK_NOT_FOUND: Task '{task_id}' does not exist."
        self.tasks[task_id]["title"] = new_title
        print(f"[task_tracker] Updated task {task_id} title: {new_title}")
        self._flush()
        return f"TASK_TITLE_UPDATED: '{new_title}'"

    def update_task_description(self, task_id: str, new_description: str) -> str:
        if task_id not in self.tasks:
            return f"TASK_NOT_FOUND: Task '{task_id}' does not exist."
        self.tasks[task_id]["description"] = new_description
        print(f"[task_tracker] Updated task {task_id} description")
        self._flush()
        return f"TASK_DESCRIPTION_UPDATED"

    def set_task_status(self, task_id: str, status: str) -> str:
        if task_id not in self.tasks:
            return f"TASK_NOT_FOUND: Task '{task_id}' does not exist."
        valid_statuses = ["todo", "in_progress", "done"]
        if status not in valid_statuses:
            return f"INVALID_STATUS: Must be one of {valid_statuses}"
        self.tasks[task_id]["status"] = status
        self.tasks[task_id]["updated_at"] = time.time()
        print(f"[task_tracker] Task {task_id} status: {status}")
        self._flush()
        return f"TASK_STATUS_UPDATED: {status}"

    def get_task(self, task_id: str) -> str:
        if task_id not in self.tasks:
            return f"TASK_NOT_FOUND: Task '{task_id}' does not exist."
        task = self.tasks[task_id].copy()
        notes = self.task_notes.get(task_id, [])
        return f"TASK_DETAILS:\nID: {task['id']}\nTitle: {task['title']}\nDescription: {task['description']}\nStatus: {task['status']}\nNotes: {len(notes)} note(s)"

    def get_task_list(self) -> str:
        if not self.tasks:
            return "TASK_LIST_EMPTY: No tasks created yet."
        output = "TASK_LIST:\n"
        for task_id, task in sorted(self.tasks.items(), key=lambda x: x[1].get("created_at", 0)):
            status_icon = {"todo": "[ ]", "in_progress": "[→]", "done": "[✓]"}[task["status"]]
            output += f"  {status_icon} [{task_id}] {task['title']}\n"
            if task.get("description"):
                output += f"      {task['description'][:80]}{'...' if len(task['description']) > 80 else ''}\n"
        return output.strip()

    def add_task_note(self, task_id: str, note: str) -> str:
        if task_id not in self.tasks:
            return f"TASK_NOT_FOUND: Task '{task_id}' does not exist."
        if task_id not in self.task_notes:
            self.task_notes[task_id] = []
        self.task_notes[task_id].append({
            "note": note,
            "timestamp": time.time()
        })
        print(f"[task_tracker] Added note to task {task_id}")
        self._flush()
        return f"TASK_NOTE_ADDED: {note[:50]}{'...' if len(note) > 50 else ''}"

# ══════════════════════════════════════════════════════════════════════════════
#  TASK TRACKER TOOL DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

TASK_TRACKER_TOOL_DEFINITIONS = [
    {
        "name": "create_task",
        "description": (
            "Create a new task with title and description for tracking implementation progress.\n"
            "Use for complex multi-step tasks that need to be tracked.\n"
            "- Title: Short verb-led task name (max 6 words)\n"
            "- Description: One sentence describing the work"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short task title (verb-led, max 6 words)"},
                "description": {"type": "string", "description": "One sentence describing the work"}
            },
            "required": ["title"]
        }
    },
    {
        "name": "update_task_title",
        "description": "Update a task's title when scope changes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Existing task ID"},
                "new_title": {"type": "string", "description": "Replacement title"}
            },
            "required": ["task_id", "new_title"]
        }
    },
    {
        "name": "update_task_description",
        "description": "Refine a task description with clearer guidance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Existing task ID"},
                "new_description": {"type": "string", "description": "Updated description text"}
            },
            "required": ["task_id", "new_description"]
        }
    },
    {
        "name": "set_task_status",
        "description": "Move a task between todo, in_progress, and done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Existing task ID"},
                "status": {"type": "string", "description": "todo, in_progress, or done"}
            },
            "required": ["task_id", "status"]
        }
    },
    {
        "name": "get_task",
        "description": "Review a single task with description, status, and notes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Existing task ID"}
            },
            "required": ["task_id"]
        }
    },
    {
        "name": "get_task_list",
        "description": "Display the current task list for planning. Shows all tasks with their statuses.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "add_task_note",
        "description": "Attach a note to a task describing findings, decisions, or blockers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Existing task ID"},
                "note": {"type": "string", "description": "Progress note or decision"}
            },
            "required": ["task_id", "note"]
        }
    },
]


# ══════════════════════════════════════════════════════════════════════════════
#  TASK TRACKING PROMPT ADDITION
# ══════════════════════════════════════════════════════════════════════════════

TASK_TRACKING_PROMPT_ADDITION = """

────────────────────────────────────────────────────────
TASK TRACKING
────────────────────────────────────────────────────────
You have a task tracker for managing implementation progress. Use it for complex multi-step tasks.

Tools: create_task, update_task_title, update_task_description, set_task_status, get_task, get_task_list, add_task_note

Statuses:
- todo: Task is planned but not started
- in_progress: Task is actively being worked on
- done: Task is completed satisfactorily

When to use task tracking:
1. Complex multi-step tasks (2+ distinct steps)
2. Non-trivial tasks requiring careful planning
3. User explicitly requests todo list
4. User provides multiple tasks

DO NOT create tasks for:
- Single-file trivial edits
- Pure Q&A with no code changes
- One-step operations

Rules:
- Keep at most one task in_progress at a time
- Add notes to capture discoveries and decisions
- Mark tasks done immediately after completion
- Tasks should be high-level, meaningful actions (not implementation details)

Example task: "Add user authentication with login and signup pages" (good)
Bad example: "Add useState hook in App.jsx" (too granular)
"""


# ══════════════════════════════════════════════════════════════════════════════
#  SUPABASE TOOLS CLASS
# ══════════════════════════════════════════════════════════════════════════════

class SupabaseTools:
    def __init__(self, supabase_url: str, anon_key: str, service_role_key: str, preview_url: str = "", project_ref: str = ""):
        self.url              = supabase_url.rstrip("/")
        self.anon_key         = anon_key
        self.service_role_key = service_role_key
        self.preview_url      = preview_url
        self.project_ref      = project_ref

    def _headers(self):
        return {
            "apikey":        self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type":  "application/json",
        }

    def _execute_sql(self, sql: str) -> dict:
        try:
            access_token = os.getenv("SUPABASE_ACCESS_TOKEN", "")
            project_ref  = self.project_ref or os.getenv("SUPABASE_PROJECT_REF", "")
            if not access_token or not project_ref:
                return {"success": False, "error": "SUPABASE_ACCESS_TOKEN or project_ref not available"}
            resp = requests.post(
                f"https://api.supabase.com/v1/projects/{project_ref}/database/query",
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json={"query": sql},
                timeout=30,
            )
            if resp.status_code < 400:
                return {"success": True, "data": resp.json()}
            else:
                return {"success": False, "error": resp.text[:500]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_table(self, table_name: str, columns: str, enable_rls: bool = True) -> str:
        sql = f"CREATE TABLE IF NOT EXISTS public.{table_name} ({columns});"
        if enable_rls:
            sql += f" ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY;"
        result = self._execute_sql(sql)
        if result["success"]:
            msg = f"TABLE_CREATED: {table_name}"
            if enable_rls:
                msg += " (RLS enabled — add policies to control access)"
            print(f"[supabase] Created table: {table_name}")
            return msg
        return f"TABLE_CREATE_ERROR: {result['error']}"

    def add_rls_policy(self, table_name: str, policy_name: str, operation: str,
                       using_expression: str, check_expression: str = "") -> str:
        self._execute_sql(f'DROP POLICY IF EXISTS "{policy_name}" ON public.{table_name};')
        op = operation.upper()
        if op == "INSERT":
            check_expr = check_expression if check_expression else using_expression
            sql = f"""CREATE POLICY "{policy_name}" ON public.{table_name}
                FOR INSERT TO authenticated WITH CHECK ({check_expr});"""
        else:
            sql = f"""CREATE POLICY "{policy_name}" ON public.{table_name}
                FOR {op} TO authenticated USING ({using_expression})"""
            if check_expression:
                sql += f" WITH CHECK ({check_expression})"
            sql += ";"
        result = self._execute_sql(sql)
        if result["success"]:
            print(f"[supabase] Added RLS policy '{policy_name}' on {table_name}")
            return f"RLS_POLICY_CREATED: '{policy_name}' on {table_name} for {op}"
        return f"RLS_POLICY_ERROR: {result['error']}"

    def enable_auth(self) -> str:
        config = {
            "supabase_url": self.url,
            "anon_key":     self.anon_key,
            "auth_methods": ["email/password (built-in)", "magic link (built-in)"],
            "usage": {
                "sign_up":   "await supabase.auth.signUp({ email, password })",
                "sign_in":   "await supabase.auth.signInWithPassword({ email, password })",
                "sign_out":  "await supabase.auth.signOut()",
                "get_user":  "const { data: { user } } = await supabase.auth.getUser()",
                "on_change": "supabase.auth.onAuthStateChange((event, session) => { ... })",
            },
        }
        return f"AUTH_ENABLED: Supabase Auth is ready.\n\nConfiguration:\n{json.dumps(config, indent=2)}"

    def list_tables(self) -> str:
        sql = """
            SELECT t.table_name,
                   json_agg(json_build_object(
                       'column_name', c.column_name,
                       'data_type', c.data_type,
                       'is_nullable', c.is_nullable,
                       'column_default', c.column_default
                   ) ORDER BY c.ordinal_position) as columns
            FROM information_schema.tables t
            JOIN information_schema.columns c
                ON c.table_name = t.table_name AND c.table_schema = t.table_schema
            WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE'
            GROUP BY t.table_name ORDER BY t.table_name;
        """
        result = self._execute_sql(sql)
        if result["success"]:
            data = result["data"]
            if not data:
                return "NO_TABLES: The database has no tables yet."
            output = "DATABASE_TABLES:\n"
            for table in data:
                name = table.get("table_name", "unknown")
                cols = table.get("columns", [])
                output += f"\n  {name}:\n"
                for col in cols:
                    nullable = "nullable" if col.get("is_nullable") == "YES" else "not null"
                    default  = f" default={col['column_default']}" if col.get("column_default") else ""
                    output  += f"    - {col['column_name']}: {col['data_type']} ({nullable}{default})\n"
            return output
        return f"LIST_TABLES_ERROR: {result['error']}"

    def run_sql(self, sql: str) -> str:
        sql_lower = sql.lower().strip()
        blocked   = ["drop database", "drop schema public", "pg_terminate_backend", "drop owned", "reassign owned"]
        for b in blocked:
            if b in sql_lower:
                return f"SQL_BLOCKED: Operation '{b}' is not allowed."
        result = self._execute_sql(sql)
        if result["success"]:
            return f"SQL_EXECUTED_SUCCESSFULLY\n{json.dumps(result['data'], indent=2)[:2000]}"
        return f"SQL_ERROR: {result['error']}"

    def get_supabase_config(self) -> str:
        return json.dumps({
            "supabase_url": self.url,
            "anon_key":     self.anon_key,
            "preview_url":  self.preview_url,
            "usage": (
                "Create a file src/lib/supabase.ts with:\n"
                "  import { createClient } from '@supabase/supabase-js'\n"
                f"  export const supabase = createClient('{self.url}', '{self.anon_key}')\n"
                f"  export const REDIRECT_URL = '{self.preview_url}'\n"
            ),
        })


# ══════════════════════════════════════════════════════════════════════════════
#  SUPABASE TOOL DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

SUPABASE_TOOL_DEFINITIONS = [
    {
        "name": "create_table",
        "description": (
            "Create a new database table in Supabase. RLS is enabled by default.\n"
            "After creating a table you MUST add RLS policies or the table will be inaccessible.\n"
            "Common patterns:\n"
            "- Primary key: 'id uuid default gen_random_uuid() primary key'\n"
            "- User ref: 'user_id uuid references auth.users(id) on delete cascade not null'\n"
            "- Timestamps: 'created_at timestamptz default now()'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
                "columns":    {"type": "string"},
                "enable_rls": {"type": "boolean"}
            },
            "required": ["table_name", "columns"]
        }
    },
    {
        "name": "add_rls_policy",
        "description": (
            "Add a Row Level Security policy.\n"
            "Common: SELECT using='auth.uid() = user_id', INSERT check='auth.uid() = user_id'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name":       {"type": "string"},
                "policy_name":      {"type": "string"},
                "operation":        {"type": "string", "description": "SELECT, INSERT, UPDATE, DELETE, or ALL"},
                "using_expression": {"type": "string"},
                "check_expression": {"type": "string"}
            },
            "required": ["table_name", "policy_name", "operation", "using_expression"]
        }
    },
    {
        "name": "enable_auth",
        "description": "Get Supabase auth configuration and code patterns.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "list_tables",
        "description": "List all tables with columns, types, and constraints.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "run_sql",
        "description": "Execute arbitrary SQL. Cannot drop databases or schemas.",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"]
        }
    },
    {
        "name": "get_supabase_config",
        "description": "Get the Supabase URL and anon key for frontend code.",
        "input_schema": {"type": "object", "properties": {}}
    },
]


# ══════════════════════════════════════════════════════════════════════════════
#  PROMPT ADDITIONS
# ══════════════════════════════════════════════════════════════════════════════

SUPABASE_PROMPT_ADDITION = """

────────────────────────────────────────────────────────
BACKEND / DATABASE (SUPABASE)
────────────────────────────────────────────────────────
This project has a Supabase backend enabled. You have full access to create database tables,
set up authentication, and configure Row Level Security.

IMPORTANT: You MUST install @supabase/supabase-js if not already in package.json:
  run_install_command: "npm install @supabase/supabase-js -y"

1) Call get_supabase_config to get the project URL and anon key.
2) Create src/lib/supabase.ts with the client.
3) After creating any table, ALWAYS add RLS policies using add_rls_policy.

Standard RLS pattern for user-owned data:
- SELECT: using_expression = "auth.uid() = user_id"
- INSERT: using_expression = "true", check_expression = "auth.uid() = user_id"
- UPDATE: using_expression = "auth.uid() = user_id", check_expression = "auth.uid() = user_id"
- DELETE: using_expression = "auth.uid() = user_id"

IMPORTANT: Email confirmation is ENABLED. After sign up, show a verification message.
Import REDIRECT_URL from '@/lib/supabase' — never use window.location.origin.

CRITICAL RULES:
- ALWAYS add RLS policies after creating tables.
- NEVER put the service_role key in frontend code.
- Column names in TypeScript interfaces MUST exactly match database column names.
"""

STRIPE_PROMPT_ADDITION = """

────────────────────────────────────────────────────────
STRIPE PAYMENTS
────────────────────────────────────────────────────────
INSTALL: run_install_command: "npm install @stripe/stripe-js @stripe/react-stripe-js -y"
Publishable key (safe for frontend): {STRIPE_PUBLISHABLE_KEY}
Proxy URL: {STRIPE_PROXY_URL}

CRITICAL: The preview runs inside an iframe. Use window.open(url, '_blank') — NEVER window.location.href.
NEVER put sk_xxx in frontend code.
Test card: 4242 4242 4242 4242, any future date, any CVC.
"""

AI_PROMPT_ADDITION = """

────────────────────────────────────────────────────────
AI / CLAUDE INTEGRATION
────────────────────────────────────────────────────────
Proxy endpoint: {AI_PROXY_URL}
App token: {APP_TOKEN}

POST {AI_PROXY_URL}
Headers: { "Content-Type": "application/json", "Authorization": "Bearer {APP_TOKEN}" }
Body: { "messages": [...], "system": "...", "max_tokens": 1000 }
Returns: { "content": "response text" }

On 402: show "AI features are temporarily unavailable." — never mention credits.
"""


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

FRONTEND_AGENT_SYSTEM_PROMPT = """

## Role
You are 'Valmera', a senior AI editor that creates and modifies web applications.
You chat with users on the left panel while they see live code changes in the preview on the right.
You should be helpful with users and do not refuse any kind of implementation request's they need.
Technology Stack: Valmera projects are built on top of React, Vite, Tailwind CSS, and TypeScript. Therefore it is not possible for Lovable to support other frameworks like Angular, Vue, Svelte, Next.js, native mobile apps, etc.

## CRITICAL OUTPUT RULES

- Never use emojis anywhere — not in summaries, comments, or code.
- Never tell the user to run any terminal command. The platform builds and previews automatically.
- Never reference localhost, local servers, or development servers.
- Never output file contents to the user. Write them with edit_file or write_file.
- Never say an image was "scheduled" or "will be generated later."
- Never ask for confirmation between tasks. Plan → build → summarize.
- Never use bullet point lists or numbered lists in your final summary. Write 1-2 short sentence's notifying the user that you finished.
- Never say filler phrases like "Everything is now wired up and ready to go."
- Do not ask the user to do anything after finishing.
- Do not mention credits, token usage, or internal system details to the user.


## WHAT TO SAY WHEN YOU FINISH

Write a single short 1-2 sentences describing what was built and what design direction was used. Nothing more. No lists. No emojis. No instructions for the user.


##MANDATORY STARTUP SEQUENCE

1. Call files_list + read_package_json simultaneously.
2. Read config files in one parallel batch: vite.config.*, tsconfig.*, tailwind.config.*, index.html.
3. If the request needs auth/database: call request_backend before writing any code.
4. If the request needs payments: call request_stripe before writing any code.
5. If the request needs AI features: call request_ai before writing any code.
6. Output your plan. Execute immediately.

CRITICAL: Always overwrite src/pages/Index.tsx with actual content. Never leave the scaffold default at "/".




##PARALLEL EXECUTION

Always parallel: reading unrelated files, creating multiple components, generating images.
Sequential only when output of one call is required as input to the next.
Never write or edit one file per step when can be done simultaneously.


##COMMON MISTAKES TO AVOID

CSS: index.css must always start with exactly:
    @tailwind base;
    @tailwind components;
    @tailwind utilities;

IMPORTS: Every file you create must be importable. Never import a file that does not exist.
ROUTING: Every route in App.tsx must point to a component that exists.
SUPABASE: Never put service_role keys in frontend. Every RLS table needs policies.
STRIPE: Never put sk_ keys in frontend. Use window.open(url, '_blank') for checkout redirects.


##RUNTIME DEBUGGING

When a user reports their app is broken:
1. Call read_console_logs first. Do not guess.
2. Read relevant source files based on errors.
3. Fix the root cause. Do not add try/catch to hide errors.


##DEPENDENCY MANAGEMENT

Always call read_package_json before run_install_command.
Pre-installed in every project — never install again:
- @supabase/supabase-js, @stripe/stripe-js, @stripe/react-stripe-js, framer-motion


##IMAGE GENERATION

Images are not optional. Every e-commerce store, landing page, or content site must have real generated images.

Models (fal.ai):
- flux-schnell: fastest, $0.003/MP — use for ALL images except the hero
- flux-pro:     high quality, $0.03/MP — complex product shots only
- flux-ultra:   hero banners only, $0.06/image — ONE per project maximum

MANDATORY per commerce project:
- One hero image (1920x1080) using flux-ultra
- All other images using flux-schnell

Generate max 4 images at a time. Generate ALL images before writing any page components.
Write detailed prompts — subject, audience, style, lighting, background.
Import as ES6 modules: import heroImg from '../assets/hero.jpg'
Never use string paths in JSX. Never import a path that returned IMAGE_GENERATION_FAILED.
On failure: use a CSS gradient, never a flat color block.

AUDIENCE CONSISTENCY: If the app is for women's clothing, every prompt must say "women's".
Never leave the audience ambiguous in a prompt.


---

## Design Guidelines

### Design Philosophy

Before coding, commit to a BOLD aesthetic direction:
- **Purpose**: What problem does this solve? Who uses it?
- **Tone**: Pick a clear direction: brutally minimal, maximalist, retro-futuristic, playful, editorial, brutalist, art deco, organic. Execute with conviction.
- **Differentiation**: What makes this unforgettable?

NEVER use generic AI aesthetics: overused fonts (Inter, Poppins), purple gradients on white, predictable layouts. No two projects should look the same.

### Visual Execution

- **Typography**: Avoid defaults. Pair a distinctive display font with a refined body font.
- **Color**: Commit to a cohesive palette. Bold accents outperform timid, evenly-distributed colors.
- **Motion**: Use framer-motion for animations. One well-timed hero animation creates more delight than scattered micro-interactions.
- **Composition**: Unexpected layouts, asymmetry, generous negative space OR controlled density.
- **Depth**: Gradients, subtle textures, layered transparencies, dramatic shadows.

Match complexity to vision: maximalist designs need extensive effects; minimalist designs need precision in spacing and typography.

### Design System Implementation

**CRITICAL**: Never write custom color classes (text-white, bg-black, etc.) in components. Always use semantic design tokens.

- Leverage index.css and tailwind.config.ts for consistent, reusable design tokens
- Customize shadcn components with proper variants
- Use semantic tokens: `--background`, `--foreground`, `--primary`, `--primary-foreground`, `--secondary`, `--muted`, `--accent`, etc.
- Add all new colors to tailwind.config.js for Tailwind class usage
- Ensure proper contrast in both light and dark modes

Example approach:
```css
/* index.css - Define rich tokens */
:root {
   --primary: [hsl values];
   --gradient-primary: linear-gradient(135deg, hsl(var(--primary)), hsl(var(--primary-glow)));
   --shadow-elegant: 0 10px 30px -10px hsl(var(--primary) / 0.3);
}
```

```tsx
// Create component variants using design system
const buttonVariants = cva("...", {
  variants: {
    variant: {
      premium: "bg-gradient-to-r from-primary to-primary-glow...",
    }
  }
})
```

**IMPORTANT**: Check CSS variable format before using in color functions. Always use HSL in index.css and tailwind.config.ts.

---

##WHAT YOU MUST BUILD

1. Every described page, fully implemented. No stubs, no TODOs.
2. All routes working. Mobile menu if needed.
3. Every layout works at 320px, 768px, 1440px.
4. Forms submit, modals open/close, dropdowns work.
5. Realistic domain-appropriate content. Zero Lorem Ipsum.
6. Loading states, empty states, error states for all data-dependent UI.
7. All buttons functional — no dead buttons.


##SUMMARY
-After you finish building output a very short 1-2 line telling the user that you finished
"""


# ══════════════════════════════════════════════════════════════════════════════
#  TOOL DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

REQUEST_BACKEND_TOOL = {
    "name": "request_backend",
    "description": (
        "Request a Supabase backend. Call EARLY — before writing any auth or database code. "
        "If approved you get create_table, add_rls_policy, etc. "
        "If denied, build a frontend-only version using localStorage."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"]
    }
}

anthropic_tools = [
    {
        "name": "read_file",
        "description": "Read the content of an existing file from disk.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    },
    {
        "name": "edit_file",
        "description": "Replace an exact string in an existing file. Default for modifying existing files. Always prefer this over the write_file because its cheaper unless you want to add a new file or overwrite a file then you should use write_file",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string"},
                "old_str": {"type": "string", "description": "Exact block to replace. Must exist in the file."},
                "new_str": {"type": "string"}
            },
            "required": ["path", "old_str", "new_str"]
        }
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file. Always write the full file content when used.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]
        }
    },
    
    {
        "name": "delete_file",
        "description": "Delete a file or folder.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    },
    {
        "name": "rename_file",
        "description": "Rename or move a file.",
        "input_schema": {
            "type": "object",
            "properties": {"original_path": {"type": "string"}, "new_path": {"type": "string"}},
            "required": ["original_path", "new_path"]
        }
    },
    {
        "name": "search_files",
        "description": "Regex search across project files. Use before renaming or refactoring.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":            {"type": "string"},
                "search_dir":       {"type": "string"},
                "include_patterns": {"type": "string"},
                "case_sensitive":   {"type": "boolean"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "files_list",
        "description": "Get the list of all project files. Call at startup.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "run_install_command",
        "description": "Install npm packages. Always call read_package_json first. Always use -y flag.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command":   {"type": "string"},
                "directory": {"type": "string"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "generate_image",
        "description": (
            "Generate an AI image using fal.ai and save it to the specified path.\n\n"
            "Models:\n"
            "- flux-schnell: fastest, cheapest ($0.003/MP). Use for ALL images except the hero.\n"
            "- flux-pro:     high quality ($0.03/MP). Only for complex product shots.\n"
            "- flux-ultra:   hero banners only ($0.06). One per project maximum.\n\n"
            "Generate max 4 images at a time. Always use flux-schnell unless hero or complex shot.\n"
            "On IMAGE_GENERATION_FAILED: use CSS gradient, never import that path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt":      {"type": "string"},
                "target_path": {"type": "string"},
                "width":       {"type": "number", "description": "Min 512, max 1920, multiple of 32. Default 1024."},
                "height":      {"type": "number", "description": "Min 512, max 1920, multiple of 32. Default 768."},
                "model":       {"type": "string", "description": "flux-schnell | flux-pro | flux-ultra"}
            },
            "required": ["prompt", "target_path"]
        }
    },
    {
        "name": "edit_image",
        "description": "Edit existing images based on a text prompt.",
        "input_schema": {
            "type": "object",
            "properties": {
                "image_paths":  {"type": "array", "items": {"type": "string"}},
                "prompt":       {"type": "string"},
                "target_path":  {"type": "string"},
                "aspect_ratio": {"type": "string"}
            },
            "required": ["image_paths", "prompt", "target_path"]
        }
    },
    {
        "name": "read_console_logs",
        "description": (
            "Read runtime console errors. ONLY call when the user says the app is broken. "
            "NEVER call proactively or before the build is complete."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "read_package_json",
        "description": "Read package.json. ALWAYS call before run_install_command.",
        "input_schema": {"type": "object", "properties": {}}
    },
    REQUEST_BACKEND_TOOL,
    {
        "name": "request_stripe",
        "description": "Request Stripe integration. Call EARLY — before writing any payment code.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"]
        }
    },
    {
        "name": "request_ai",
        "description": "Request Claude AI integration. Call EARLY — before writing any AI code.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"]
        }
    },
]


# ══════════════════════════════════════════════════════════════════════════════
#  FAL.AI IMAGE MODEL MAP
#
#  fal.ai endpoint strings — these are what get passed to fal_client.subscribe()
#  Response shape: result["images"][0]["url"] — always a plain HTTPS URL.
#  No FileOutput objects, no byte extraction, no .read() — just download the URL.
# ══════════════════════════════════════════════════════════════════════════════

_FAL_MODEL_MAP: dict[str, str] = {
    # Primary names — what the agent uses
    "flux-schnell": "fal-ai/flux/schnell",           # $0.003/MP — default for ALL non-hero images
    "flux-pro":     "fal-ai/flux-pro/v1.1",          # $0.03/MP  — complex shots only
    "flux-ultra":   "fal-ai/flux-pro/v1.1-ultra",    # $0.06/image — hero only, fully on fal.ai infra

    # Legacy aliases — kept so old prompts don't hard-fail
    "flux.schnell": "fal-ai/flux/schnell",
    "flux.dev":     "fal-ai/flux/dev",
    "flux-dev":     "fal-ai/flux/dev",
    "flux2.dev":    "fal-ai/flux-pro/v1.1-ultra",    # retired Replicate model — remapped to ultra
}

# NOTE: fal-ai/flux-pro/v1.1-ultra is fully hosted on fal.ai infrastructure.
# The earlier SSL error (api.us2.bfl.ai) was from the old Replicate/BFL direct routing.
# fal.ai proxies all requests through their own servers — no BFL SSL exposure.

# Models that use aspect_ratio instead of explicit width/height (no image_size param)
_ULTRA_MODELS = {"fal-ai/flux-pro/v1.1-ultra"}

# Minimum bytes for a real image
_MIN_IMAGE_BYTES = 10 * 1024  # 10 KB


def _aspect_ratio_from_dims(width: int, height: int) -> str:
    ratio = width / height
    if ratio > 1.6:   return "16:9"
    elif ratio > 1.2: return "4:3"
    elif ratio < 0.7: return "9:16"
    else:             return "1:1"


def _download_image(url: str) -> bytes | None:
    """Download image from a fal.ai CDN URL. Retries up to 3 times."""
    print(f"[image_gen] Downloading from: {url[:100]}")
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=120, stream=True, allow_redirects=True)
            print(f"[image_gen] Download HTTP {resp.status_code} on attempt {attempt + 1}")
            if resp.status_code == 200:
                data = b"".join(resp.iter_content(8192))
                print(f"[image_gen] Downloaded {len(data)} bytes")
                if len(data) >= _MIN_IMAGE_BYTES:
                    return data
                print(f"[image_gen] Too small ({len(data)} bytes) — retrying")
            else:
                print(f"[image_gen] Bad status {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"[image_gen] Download attempt {attempt + 1} exception: {type(e).__name__}: {e}")
        if attempt < 2:
            time.sleep(2)
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  CREATE GENERATOR — main factory function
# ══════════════════════════════════════════════════════════════════════════════

def create_generator(files_list_state, reviewer=None, model=None, supabase_config=None, workspace=None, stripe_config=None, ai_config=None):
    if model is None:
        model = 'claude-haiku-4-5-20251001'

    print(f"[Agent5] Creating generator with model: {model}")

    system_prompt = FRONTEND_AGENT_SYSTEM_PROMPT
    if supabase_config:
        system_prompt += SUPABASE_PROMPT_ADDITION
        print(f"[Agent5] Supabase enabled")
    if stripe_config:
        system_prompt += STRIPE_PROMPT_ADDITION.replace(
            "{STRIPE_PUBLISHABLE_KEY}", stripe_config.get("publishable_key", "")
        ).replace("{STRIPE_PROXY_URL}", stripe_config.get("proxy_url", ""))
        print(f"[Agent5] Stripe enabled")
    if ai_config:
        system_prompt += AI_PROMPT_ADDITION.replace(
            "{AI_PROXY_URL}", ai_config.get("proxy_url", "")
        ).replace("{APP_TOKEN}", ai_config.get("app_token", ""))
        print(f"[Agent5] AI proxy enabled")

    # Always enable task tracking
    system_prompt += TASK_TRACKING_PROMPT_ADDITION
    print(f"[Agent5] Task tracking enabled")

    all_tools = list(anthropic_tools)
    all_tools.extend(TASK_TRACKER_TOOL_DEFINITIONS)
    if supabase_config:
        all_tools.extend(SUPABASE_TOOL_DEFINITIONS)

    agent6 = BaseAgent(
        client        = client,
        model         = model,
        system_prompt = system_prompt,
        tools         = all_tools,
        temperature   = 1,
        workspace     = workspace,
    )

    add_file          = files_list_state.add_file
    remove_file       = files_list_state.remove_file
    rename_file_state = files_list_state.rename_file
    files_list        = files_list_state.files_list

    def write_file(path: str, content: str) -> str:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        existed     = os.path.exists(path)
        old_content = None
        if existed:
            with open(path, "r", encoding="utf-8") as f:
                old_content = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        if not existed:
            add_file(path)
        print(f"""{Back.WHITE}agent6: FILE_WRITE {path}{Style.RESET_ALL}""")
        agent6.notify_reviewer({"type": "FILE_WRITE", "path": path, "existed": existed, "old_content": old_content, "new_content": content})
        return f"WRITE_COMPLETED PATH:{path}"

    def edit_file(path, old_str, new_str):
        if not os.path.exists(path):
            return "ERROR: File does not exist, use write_file for new files."
        with open(path, 'r', encoding='utf-8') as f:
            full_content = f.read()
        if old_str not in full_content:
            return f"ERROR: The segment you want to replace was not found in {path}"
        updated_content = full_content.replace(old_str, new_str, 1)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(updated_content)
        print(f"""{Back.WHITE}agent6: EDIT {path}{Style.RESET_ALL}""")
        agent6.notify_reviewer({"type": "FILE_WRITE", "path": path, "old_string": old_str, "new_string": new_str, "new_content": updated_content})
        return f"EDIT_COMPLETED PATH: {path}"

    def read_file(path, **kwargs):
        p = Path(path)
        if not p.exists():
            return f"[READ_FILE_ERROR] FILE NOT FOUND {path}"
        if p.is_dir():
            return f"[READ_FILE_ERROR] '{path}' is a directory, not a file."
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    def delete_file(path: str) -> str:
        try:
            p = Path(path)
            if not p.exists():
                return f"DELETE_ERROR: Path not found: {path}"
            if p.is_dir():
                dir_prefix = os.path.normpath(path)
                for f in list(files_list_state.files):
                    if os.path.normpath(f).startswith(dir_prefix):
                        remove_file(f)
                shutil.rmtree(path)
            else:
                os.remove(path)
                remove_file(path)
            agent6.notify_reviewer({"type": "FILE_DELETE", "path": path})
            return f"DELETE_COMPLETED PATH:{path}"
        except Exception as e:
            return f"DELETE_ERROR: {str(e)}"

    def rename_file(original_path: str, new_path: str) -> str:
        try:
            if not os.path.exists(original_path):
                return f"RENAME_ERROR: Source not found: {original_path}"
            parent = os.path.dirname(new_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            os.rename(original_path, new_path)
            rename_file_state(original_path, new_path)
            agent6.notify_reviewer({"type": "FILE_RENAME", "old_path": original_path, "new_path": new_path})
            return f"RENAME_COMPLETED: {original_path} → {new_path}"
        except Exception as e:
            return f"RENAME_ERROR: {str(e)}"

    def search_files(query: str, search_dir: str = "src", include_patterns: str = "", case_sensitive: bool = False) -> str:
        try:
            cmd = ["grep", "-r", "-n", "--include=*.ts", "--include=*.tsx", "--include=*.js", "--include=*.jsx", "--include=*.css", "--include=*.html", "--include=*.json"]
            if include_patterns:
                cmd = ["grep", "-r", "-n"]
                for pattern in include_patterns.split(","):
                    p = pattern.strip()
                    if p:
                        cmd.append(f"--include={p}")
            if not case_sensitive:
                cmd.append("-i")
            cmd.extend(["--exclude-dir=node_modules", "--exclude-dir=dist", "--exclude-dir=.git"])
            cmd.extend([query, search_dir if os.path.isdir(search_dir) else "."])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            output = result.stdout.strip()
            if not output:
                return f"SEARCH_NO_RESULTS: No matches for '{query}' in {search_dir}"
            lines = output.split("\n")
            if len(lines) > 50:
                output = "\n".join(lines[:50]) + f"\n... and {len(lines) - 50} more matches"
            return output
        except subprocess.TimeoutExpired:
            return "SEARCH_ERROR: Search timed out"
        except Exception as e:
            return f"SEARCH_ERROR: {str(e)}"

    # ══════════════════════════════════════════════════════════════════════
    #  generate_image — fal.ai implementation
    #
    #  fal.ai always returns a plain HTTPS URL in result["images"][0]["url"].
    #  There are no FileOutput objects, no byte extraction complexity.
    #  The full flow is: call fal_client.subscribe() → get URL → download → save.
    # ══════════════════════════════════════════════════════════════════════
    def generate_image(prompt: str, target_path: str, width: int = 1024, height: int = 768, model: str = "flux-schnell") -> str:
        try:
            # Clamp and align dimensions
            width  = max(512, min(1920, int(width)))
            height = max(512, min(1920, int(height)))
            width  = (width  // 32) * 32
            height = (height // 32) * 32

            parent = os.path.dirname(target_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            # Resolve model — unknown names fall back to flux-schnell
            fal_endpoint = _FAL_MODEL_MAP.get(model, _FAL_MODEL_MAP["flux-schnell"])
            if model not in _FAL_MODEL_MAP:
                print(f"[image_gen] Unknown model '{model}' — falling back to flux-schnell")

            ext           = target_path.rsplit(".", 1)[-1].lower() if "." in target_path else "jpeg"
            output_format = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "jpeg")

            # Build arguments — ultra uses aspect_ratio, others use image_size dict
            if fal_endpoint in _ULTRA_MODELS:
                arguments = {
                    "prompt":        prompt,
                    "aspect_ratio":  _aspect_ratio_from_dims(width, height),
                    "output_format": output_format,
                }
                print(f"[image_gen] flux-ultra → aspect_ratio={arguments['aspect_ratio']}")
            else:
                arguments = {
                    "prompt":      prompt,
                    "image_size":  {"width": width, "height": height},
                    "output_format": output_format,
                    "num_images":  1,
                }

            print(f"[image_gen] ── START ──────────────────────────────────────")
            print(f"[image_gen] target_path : {target_path}")
            print(f"[image_gen] model       : {model} → {fal_endpoint}")
            print(f"[image_gen] dimensions  : {width}x{height}")
            print(f"[image_gen] arguments   : {json.dumps({k: v for k, v in arguments.items() if k != 'prompt'})}")
            print(f"[image_gen] prompt      : {prompt[:120]}...")
            print(f"[image_gen] fal_key set : {'yes' if fal_client.api_key else 'NO — FAL_KEY missing!'}")

            # ── Call fal.ai with retry + exponential backoff ──────────────
            result = None
            for attempt in range(3):
                try:
                    print(f"[image_gen] Calling fal_client.subscribe() attempt {attempt + 1}...")
                    result = fal_client.subscribe(fal_endpoint, arguments=arguments)
                    print(f"[image_gen] fal_client.subscribe() succeeded")
                    print(f"[image_gen] result type : {type(result).__name__}")
                    print(f"[image_gen] result keys : {list(result.keys()) if isinstance(result, dict) else 'not a dict'}")
                    break
                except Exception as e:
                    err_str = str(e).lower()
                    print(f"[image_gen] attempt {attempt + 1} exception: {type(e).__name__}: {e}")
                    is_rate_limit = "429" in err_str or "rate" in err_str or "throttl" in err_str or "queue" in err_str
                    if is_rate_limit and attempt < 2:
                        delay = 5 * (2 ** attempt)  # 5s, 10s, 20s
                        print(f"[image_gen] Rate limited — retrying in {delay}s...")
                        time.sleep(delay)
                        continue
                    print(f"[image_gen] FAILED after {attempt + 1} attempts: {e}")
                    return f"IMAGE_GENERATION_FAILED: {str(e)[:200]} — use a CSS gradient placeholder instead. Do NOT import this path."

            if result is None:
                return "IMAGE_GENERATION_FAILED: No result returned — use a CSS gradient placeholder instead. Do NOT import this path."

            # ── Extract the image URL ──────────────────────────────────────
            # fal.ai always returns: result["images"][0]["url"]
            image_url = None
            try:
                image_url = result["images"][0]["url"]
                print(f"[image_gen] Extracted URL from result['images'][0]['url']")
            except (KeyError, IndexError, TypeError) as e:
                print(f"[image_gen] Could not extract via result['images'][0]['url']: {e}")

            if not image_url:
                if isinstance(result, dict):
                    image_url = result.get("image", {}).get("url") or result.get("url")
                    print(f"[image_gen] Fallback extraction got: {image_url}")
                if not image_url:
                    print(f"[image_gen] Full result dump: {str(result)[:500]}")
                    return "IMAGE_GENERATION_FAILED: Could not find image URL in result — use a CSS gradient placeholder instead. Do NOT import this path."

            print(f"[image_gen] Got URL: {image_url[:120]}")

            # ── Download the image ────────────────────────────────────────
            image_data = _download_image(image_url)

            if not image_data:
                return "IMAGE_GENERATION_FAILED: Download failed after retries — use a CSS gradient placeholder instead. Do NOT import this path."

            # ── Save to disk ──────────────────────────────────────────────
            with open(target_path, "wb") as f:
                f.write(image_data)

            size_kb = len(image_data) / 1024
            print(f"[image_gen] Saved: {target_path} ({size_kb:.1f} KB)")

            add_file(target_path)
            agent6.notify_reviewer({"type": "IMAGE_GENERATED", "path": target_path, "prompt": prompt})

            if target_path.startswith("src/"):
                return f"IMAGE_GENERATED PATH:{target_path} SIZE:{size_kb:.1f}KB — Import as ES6 module: import img from './{target_path}'"
            else:
                public_ref = target_path.replace("public/", "/", 1) if target_path.startswith("public/") else f"/{target_path}"
                return f"IMAGE_GENERATED PATH:{target_path} SIZE:{size_kb:.1f}KB — Reference in code as {public_ref}"

        except Exception as e:
            print(f"[image_gen] Exception: {e}")
            return f"IMAGE_GENERATION_FAILED: {str(e)[:200]} — use a CSS gradient placeholder instead. Do NOT import this path."

    def edit_image(image_paths: list, prompt: str, target_path: str, aspect_ratio: str = "16:9") -> str:
        try:
            parent = os.path.dirname(target_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            import base64
            image_uris = []
            for img_path in image_paths:
                if img_path.startswith("http"):
                    image_uris.append(img_path)
                elif os.path.exists(img_path):
                    with open(img_path, "rb") as f:
                        data = f.read()
                    ext  = img_path.rsplit(".", 1)[-1].lower()
                    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
                    image_uris.append(f"data:{mime};base64,{base64.b64encode(data).decode()}")
                else:
                    return f"IMAGE_EDIT_FAILED: Source image not found: {img_path}"
            if not image_uris:
                return "IMAGE_EDIT_FAILED: No valid source images provided"

            # fal.ai Kontext endpoint for image editing
            result = fal_client.subscribe(
                "fal-ai/flux-pro/kontext",
                arguments={
                    "prompt":        prompt,
                    "image_url":     image_uris[0],
                    "aspect_ratio":  aspect_ratio,
                    "output_format": "jpeg",
                }
            )
            image_url = result["images"][0]["url"] if result and "images" in result else None
            if not image_url:
                return "IMAGE_EDIT_FAILED: No output URL received"

            image_data = _download_image(image_url)
            if not image_data:
                return "IMAGE_EDIT_FAILED: Download failed"

            with open(target_path, "wb") as f:
                f.write(image_data)
            add_file(target_path)
            agent6.notify_reviewer({"type": "IMAGE_EDITED", "source_paths": image_paths, "target_path": target_path})

            if target_path.startswith("src/"):
                return f"IMAGE_EDITED PATH:{target_path} — Import as ES6 module: import img from './{target_path}'"
            public_ref = target_path.replace("public/", "/", 1) if target_path.startswith("public/") else f"/{target_path}"
            return f"IMAGE_EDITED PATH:{target_path} — Reference in code as {public_ref}"
        except Exception as e:
            return f"IMAGE_EDIT_FAILED: {str(e)}"

    def read_console_logs() -> str:
        _ws = workspace
        if not _ws:
            return "CONSOLE_LOGS_ERROR: No workspace configured."
        log_path = os.path.join(_ws, "console_logs.json")
        if not os.path.exists(log_path):
            return "CONSOLE_LOGS_EMPTY: No runtime errors captured yet."
        try:
            with open(log_path) as f:
                logs = json.load(f)
            if not logs:
                return "CONSOLE_LOGS_EMPTY: No errors captured — app appears clean."
            output = f"CONSOLE_LOGS ({len(logs)} entries, showing last 30):\n"
            for entry in logs[-30:]:
                level = entry.get("level", "log").upper()
                msg   = entry.get("msg", "")
                output += f"[{level}] {msg}\n"
            return output.strip()
        except Exception as e:
            return f"CONSOLE_LOGS_READ_ERROR: {str(e)}"

    def read_package_json() -> str:
        _ws = workspace
        if not _ws:
            return "PACKAGE_JSON_ERROR: No workspace configured."
        path = os.path.join(_ws, "package.json")
        if not os.path.exists(path):
            return "PACKAGE_JSON_NOT_FOUND"
        try:
            with open(path) as f:
                return f.read()
        except Exception as e:
            return f"PACKAGE_JSON_READ_ERROR: {str(e)}"

    def request_backend(reason: str = "") -> str:
        import time as _time
        _workspace = workspace
        if not _workspace:
            return "BACKEND_ERROR: No workspace configured"
        meta_path = os.path.join(_workspace, "meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                if meta.get("supabase_enabled"):
                    return "BACKEND_ALREADY_ENABLED: Supabase is already active. Use get_supabase_config."
            except Exception:
                pass
        print(f"[Agent5] Backend requested — reason: {reason}")
        req_path = os.path.join(_workspace, "backend_requested.json")
        with open(req_path, "w") as f:
            json.dump({"reason": reason, "ts": _time.time()}, f)
        approved_path = os.path.join(_workspace, "backend_approved.json")
        denied_path   = os.path.join(_workspace, "backend_denied.json")
        max_wait = 300
        elapsed  = 0
        while elapsed < max_wait:
            _time.sleep(3)
            elapsed += 3
            if os.path.exists(approved_path):
                try: os.remove(approved_path)
                except: pass
                try: os.remove(req_path)
                except: pass
                supabase_url = ""
                anon_key     = ""
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)
                    supabase_url = meta.get("supabase_url", "")
                    anon_key     = meta.get("supabase_anon_key", "")
                except: pass
                if supabase_url and anon_key:
                    sb = SupabaseTools(
                        supabase_url     = supabase_url,
                        anon_key         = anon_key,
                        service_role_key = meta.get("supabase_service_role", ""),
                        preview_url      = f"https://entrepreneur-bot-backend.onrender.com/auth/preview-raw/{os.path.basename(_workspace)}/",
                        project_ref      = meta.get("supabase_project_ref", ""),
                    )
                    agent6.tool_map["create_table"]        = sb.create_table
                    agent6.tool_map["add_rls_policy"]      = sb.add_rls_policy
                    agent6.tool_map["enable_auth"]         = sb.enable_auth
                    agent6.tool_map["list_tables"]         = sb.list_tables
                    agent6.tool_map["run_sql"]             = sb.run_sql
                    agent6.tool_map["get_supabase_config"] = sb.get_supabase_config
                    for tool_def in SUPABASE_TOOL_DEFINITIONS:
                        if not any(t["name"] == tool_def["name"] for t in agent6.tools):
                            agent6.tools.append(tool_def)
                    if "BACKEND / DATABASE (SUPABASE)" not in agent6.system_prompt:
                        agent6.system_prompt += SUPABASE_PROMPT_ADDITION
                return (
                    f"BACKEND_APPROVED: Supabase is now active!\n"
                    f"URL: {supabase_url}\nAnon Key: {anon_key}\n\n"
                    f"NEXT: call get_supabase_config, create src/lib/supabase.ts, then create tables with RLS policies."
                )
            if os.path.exists(denied_path):
                try: os.remove(denied_path)
                except: pass
                try: os.remove(req_path)
                except: pass
                return "BACKEND_DENIED: Build a frontend-only version using localStorage."
        try: os.remove(req_path)
        except: pass
        return "BACKEND_TIMEOUT: Build a frontend-only version using localStorage."

    def request_stripe(reason: str = "") -> str:
        import time as _time
        _workspace = workspace
        if not _workspace:
            return "STRIPE_ERROR: No workspace configured"
        meta_path = os.path.join(_workspace, "meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                if meta.get("stripe_enabled"):
                    pk  = meta.get("stripe_publishable_key", "")
                    job = os.path.basename(_workspace)
                    return f"STRIPE_ALREADY_ENABLED: pk={pk}, proxy=https://entrepreneur-bot-backend.onrender.com/stripe/job/{job}"
            except Exception:
                pass
        req_path = os.path.join(_workspace, "stripe_requested.json")
        with open(req_path, "w") as f:
            json.dump({"reason": reason, "ts": _time.time()}, f)
        approved_path = os.path.join(_workspace, "stripe_approved.json")
        denied_path   = os.path.join(_workspace, "stripe_denied.json")
        max_wait = 300
        elapsed  = 0
        while elapsed < max_wait:
            _time.sleep(3)
            elapsed += 3
            if os.path.exists(approved_path):
                try: os.remove(approved_path)
                except: pass
                try: os.remove(req_path)
                except: pass
                pk  = ""
                job = os.path.basename(_workspace)
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)
                    pk = meta.get("stripe_publishable_key", "")
                except Exception:
                    pass
                proxy_url = f"https://entrepreneur-bot-backend.onrender.com/stripe/job/{job}"
                if "STRIPE PAYMENTS" not in agent6.system_prompt:
                    agent6.system_prompt += STRIPE_PROMPT_ADDITION.replace(
                        "{STRIPE_PUBLISHABLE_KEY}", pk
                    ).replace("{STRIPE_PROXY_URL}", proxy_url)
                return f"STRIPE_APPROVED: pk={pk}, proxy={proxy_url}"
            if os.path.exists(denied_path):
                try: os.remove(denied_path)
                except: pass
                try: os.remove(req_path)
                except: pass
                return "STRIPE_DENIED: Build a payment UI mockup with 'Coming soon' buttons."
        try: os.remove(req_path)
        except: pass
        return "STRIPE_TIMEOUT: Build a payment UI mockup."

    def request_ai(reason: str = "") -> str:
        _workspace = workspace
        if not _workspace:
            return "AI_ERROR: No workspace configured"
        proxy_url = "https://entrepreneur-bot-backend.onrender.com/auth/ai/proxy"
        app_token = ai_config.get("app_token", "") if ai_config else ""
        if "AI / CLAUDE INTEGRATION" not in agent6.system_prompt:
            agent6.system_prompt += AI_PROMPT_ADDITION.replace(
                "{AI_PROXY_URL}", proxy_url
            ).replace("{APP_TOKEN}", app_token)
        return f"AI_APPROVED: proxy={proxy_url}, token={app_token}"

    tool_map = {
        'write_file':          write_file,
        'edit_file':           edit_file,
        'read_file':           read_file,
        'delete_file':         delete_file,
        'rename_file':         rename_file,
        'search_files':        search_files,
        'add_file':            add_file,
        'files_list':          files_list,
        'run_install_command': run_install_command,
        'generate_image':      generate_image,
        'edit_image':          edit_image,
        'request_backend':     request_backend,
        'request_stripe':      request_stripe,
        'request_ai':          request_ai,
        'read_console_logs':   read_console_logs,
        'read_package_json':   read_package_json,
    }

    if supabase_config:
        sb = SupabaseTools(
            supabase_url     = supabase_config["url"],
            anon_key         = supabase_config["anon_key"],
            service_role_key = supabase_config.get("service_role_key", ""),
            preview_url      = supabase_config.get("preview_url", ""),
            project_ref      = supabase_config.get("project_ref", ""),
        )
        tool_map["create_table"]        = sb.create_table
        tool_map["add_rls_policy"]      = sb.add_rls_policy
        tool_map["enable_auth"]         = sb.enable_auth
        tool_map["list_tables"]         = sb.list_tables
        tool_map["run_sql"]             = sb.run_sql
        tool_map["get_supabase_config"] = sb.get_supabase_config
        print(f"[Agent5] Registered 6 Supabase tools")

    # Initialize task tracker
    task_tracker = TaskTracker(workspace=workspace)

    # Register task tracker functions
    tool_map["create_task"] = task_tracker.create_task
    tool_map["update_task_title"] = task_tracker.update_task_title
    tool_map["update_task_description"] = task_tracker.update_task_description
    tool_map["set_task_status"] = task_tracker.set_task_status
    tool_map["get_task"] = task_tracker.get_task
    tool_map["get_task_list"] = task_tracker.get_task_list
    tool_map["add_task_note"] = task_tracker.add_task_note
    print(f"[Agent5] Registered 7 task tracking tools")

    agent6.tool_map = tool_map
    agent6.reviewer = reviewer

    # Store task tracker reference for frontend access
    agent6.task_tracker = task_tracker

    return agent6