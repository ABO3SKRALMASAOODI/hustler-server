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
import replicate

client = anthropic.Anthropic()


# ══════════════════════════════════════════════════════════════════════════════
#  SUPABASE TOOLS CLASS
# ══════════════════════════════════════════════════════════════════════════════

class SupabaseTools:
    """
    Provides tool functions for the AI agent to interact with Supabase.
    Initialized with the project's Supabase credentials.
    """

    def __init__(self, supabase_url: str, anon_key: str, service_role_key: str, preview_url: str = ""):
        self.url              = supabase_url.rstrip("/")
        self.anon_key         = anon_key
        self.service_role_key = service_role_key
        self.preview_url      = preview_url

    def _headers(self):
        return {
            "apikey":        self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type":  "application/json",
        }

    def _execute_sql(self, sql: str) -> dict:
        """Execute raw SQL via the Supabase Management API."""
        try:
            access_token = os.getenv("SUPABASE_ACCESS_TOKEN", "")
            project_ref = os.getenv("SUPABASE_PROJECT_REF", "")

            if not access_token or not project_ref:
                return {"success": False, "error": "SUPABASE_ACCESS_TOKEN or SUPABASE_PROJECT_REF not set"}

            resp = requests.post(
                f"https://api.supabase.com/v1/projects/{project_ref}/database/query",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
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
        else:
            print(f"[supabase] Failed to create table {table_name}: {result['error']}")
            return f"TABLE_CREATE_ERROR: {result['error']}"

    def add_rls_policy(self, table_name: str, policy_name: str, operation: str,
                       using_expression: str, check_expression: str = "") -> str:
        self._execute_sql(f'DROP POLICY IF EXISTS "{policy_name}" ON public.{table_name};')

        op = operation.upper()

        if op == "INSERT":
            # INSERT policies only support WITH CHECK, not USING
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
        else:
            print(f"[supabase] Failed to add RLS policy: {result['error']}")
            return f"RLS_POLICY_ERROR: {result['error']}"
        
    def enable_auth(self) -> str:
        config = {
            "supabase_url": self.url,
            "anon_key":     self.anon_key,
            "auth_methods": [
                "email/password (built-in, no config needed)",
                "magic link (built-in, no config needed)",
            ],
            "usage": {
                "sign_up":   "await supabase.auth.signUp({ email, password })",
                "sign_in":   "await supabase.auth.signInWithPassword({ email, password })",
                "sign_out":  "await supabase.auth.signOut()",
                "get_user":  "const { data: { user } } = await supabase.auth.getUser()",
                "on_change": "supabase.auth.onAuthStateChange((event, session) => { ... })",
            },
            "notes": [
                "Auth is already enabled — just use the supabase client.",
                "For user-specific data, add a user_id column referencing auth.users(id).",
                "Use auth.uid() in RLS policies to restrict access to the user's own rows.",
            ]
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
                return "NO_TABLES: The database has no tables yet. Use create_table to create one."
            output = "DATABASE_TABLES:\n"
            for table in data:
                name = table.get("table_name", "unknown")
                cols = table.get("columns", [])
                output += f"\n  {name}:\n"
                for col in cols:
                    nullable = "nullable" if col.get("is_nullable") == "YES" else "not null"
                    default  = f" default={col['column_default']}" if col.get("column_default") else ""
                    output += f"    - {col['column_name']}: {col['data_type']} ({nullable}{default})\n"
            return output
        else:
            return f"LIST_TABLES_ERROR: {result['error']}"

    def run_sql(self, sql: str) -> str:
        sql_lower = sql.lower().strip()
        blocked = ["drop database", "drop schema public", "pg_terminate_backend", "drop owned", "reassign owned"]
        for b in blocked:
            if b in sql_lower:
                return f"SQL_BLOCKED: Operation '{b}' is not allowed."
        result = self._execute_sql(sql)
        if result["success"]:
            return f"SQL_EXECUTED_SUCCESSFULLY\n{json.dumps(result['data'], indent=2)[:2000]}"
        else:
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
                "\nThen import {{ supabase, REDIRECT_URL }} from '@/lib/supabase' wherever needed."
            ),
        })


# ══════════════════════════════════════════════════════════════════════════════
#  SUPABASE TOOL DEFINITIONS (Anthropic format)
# ══════════════════════════════════════════════════════════════════════════════

SUPABASE_TOOL_DEFINITIONS = [
    {
        "name": "create_table",
        "description": (
            "Create a new database table in Supabase. Row Level Security (RLS) is enabled by default.\n\n"
            "After creating a table, you MUST add RLS policies using add_rls_policy, otherwise "
            "the table will be inaccessible from the frontend.\n\n"
            "Common column patterns:\n"
            "- Primary key: 'id uuid default gen_random_uuid() primary key'\n"
            "- User reference: 'user_id uuid references auth.users(id) on delete cascade not null'\n"
            "- Timestamps: 'created_at timestamptz default now()'\n"
            "- Text: 'title text not null'\n"
            "- Boolean: 'done boolean default false'\n"
            "- Number: 'price numeric(10,2)'\n"
            "- JSON: 'metadata jsonb default ''{}''::jsonb'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "Table name (lowercase, underscores)"},
                "columns": {"type": "string", "description": "SQL column definitions, comma-separated."},
                "enable_rls": {"type": "boolean", "description": "Enable Row Level Security. Default true."}
            },
            "required": ["table_name", "columns"]
        }
    },
    {
        "name": "add_rls_policy",
        "description": (
            "Add a Row Level Security policy to a table.\n\n"
            "Common patterns:\n"
            "- Users read own data: operation='SELECT', using='auth.uid() = user_id'\n"
            "- Users insert own data: operation='INSERT', using='true', check='auth.uid() = user_id'\n"
            "- Users update own data: operation='UPDATE', using='auth.uid() = user_id', check='auth.uid() = user_id'\n"
            "- Users delete own data: operation='DELETE', using='auth.uid() = user_id'\n\n"
            "IMPORTANT: A table with RLS enabled but NO policies will block ALL access."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
                "policy_name": {"type": "string", "description": "Descriptive name (e.g., 'Users can read own todos')"},
                "operation": {"type": "string", "description": "SELECT, INSERT, UPDATE, DELETE, or ALL"},
                "using_expression": {"type": "string", "description": "SQL boolean for USING clause"},
                "check_expression": {"type": "string", "description": "Optional WITH CHECK for INSERT/UPDATE"}
            },
            "required": ["table_name", "policy_name", "operation", "using_expression"]
        }
    },
    {
        "name": "enable_auth",
        "description": "Get authentication configuration. Returns exact code patterns for sign up, sign in, sign out, and session management.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "list_tables",
        "description": "List all tables in the database with their columns, types, and constraints.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "run_sql",
        "description": (
            "Execute arbitrary SQL. Use for creating indexes, inserting seed data, altering tables, "
            "creating functions/triggers, etc. Cannot drop databases or schemas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "The SQL query to execute"}},
            "required": ["sql"]
        }
    },
    {
        "name": "get_supabase_config",
        "description": "Get the Supabase project URL and anon key for the generated frontend code.",
        "input_schema": {"type": "object", "properties": {}}
    },
]


# ══════════════════════════════════════════════════════════════════════════════
#  SUPABASE SYSTEM PROMPT ADDITION
# ══════════════════════════════════════════════════════════════════════════════

SUPABASE_PROMPT_ADDITION = """

────────────────────────────────────────────────────────
BACKEND / DATABASE (SUPABASE)
────────────────────────────────────────────────────────
This project has a Supabase backend enabled. You have full access to create database tables,
set up authentication, and configure Row Level Security.

IMPORTANT: You MUST install @supabase/supabase-js if not already in package.json:
  run_install_command: "npm install @supabase/supabase-js -y"

── SETUP (do this first if not already done) ──

1) Call get_supabase_config to get the project URL and anon key.
2) Create src/lib/supabase.ts with the client.
3) Install the dependency if needed.

── DATABASE ──

Use create_table to create tables. Always include:
- A uuid primary key: `id uuid default gen_random_uuid() primary key`
- A user_id for user-owned data: `user_id uuid references auth.users(id) on delete cascade not null`
- Timestamps: `created_at timestamptz default now()`

After creating a table, ALWAYS add RLS policies using add_rls_policy.
A table with RLS enabled but no policies will block ALL access from the frontend.

CRITICAL: The column names in your TypeScript interfaces MUST exactly match the database column names you created. If you create a column called 'done', use 'done' in your TypeScript — NOT 'completed' or any other alias. Mismatched names will cause silent failures.

Standard RLS pattern for user-owned data (call these 4 policies for each table):
- SELECT: using_expression = "auth.uid() = user_id"
- INSERT: using_expression = "true", check_expression = "auth.uid() = user_id"
- UPDATE: using_expression = "auth.uid() = user_id", check_expression = "auth.uid() = user_id"
- DELETE: using_expression = "auth.uid() = user_id"

── AUTHENTICATION ──

Call enable_auth to get the configuration. Supabase Auth supports email/password out of the box.

IMPORTANT: Email confirmation is ENABLED. After sign up, users receive a verification email with a confirmation link. The app MUST handle this:
- When calling signUp(), ALWAYS pass emailRedirectTo so the user returns to the app after confirming:
```
  const { data, error } = await supabase.auth.signUp({
    email, password,
    options: { emailRedirectTo: REDIRECT_URL }
  })
```
- After signUp(), show a success message: "We sent a verification link to your email. Click it to verify, then come back and sign in."
- Do NOT auto-redirect to the dashboard after sign up
- Do NOT try to sign in immediately after sign up — it will fail until email is confirmed
- Only the Login page should redirect to the dashboard after successful sign in
- NEVER add localStorage fallbacks or workarounds for email confirmation
- Import REDIRECT_URL from '@/lib/supabase' and use it in every signUp call


Auth patterns in generated code:

```typescript
// Sign up
const { data, error } = await supabase.auth.signUp({ email, password })

// Sign in
const { data, error } = await supabase.auth.signInWithPassword({ email, password })

// Sign out
await supabase.auth.signOut()

// Get current user
const { data: { user } } = await supabase.auth.getUser()

// Listen for auth changes
supabase.auth.onAuthStateChange((event, session) => {
  setUser(session?.user ?? null)
})
```

── QUERYING DATA ──

```typescript
// Read
const { data, error } = await supabase.from('todos').select('*').order('created_at', { ascending: false })

// Insert
const { data, error } = await supabase.from('todos').insert({ title: 'New', user_id: user.id }).select()

// Update
const { data, error } = await supabase.from('todos').update({ done: true }).eq('id', todoId).select()

// Delete
const { error } = await supabase.from('todos').delete().eq('id', todoId)
```

── RECOMMENDED AUTH ARCHITECTURE ──

When the app needs authentication, always build:
1) src/lib/supabase.ts — Supabase client
2) src/contexts/AuthContext.tsx — Auth provider with user state, signIn, signUp, signOut
3) src/components/ProtectedRoute.tsx — Route wrapper that redirects to login
4) src/pages/Login.tsx — Login page
5) src/pages/Register.tsx — Registration page

── WORKFLOW ──

When the user asks for an app that needs a backend:
1) Call get_supabase_config → create src/lib/supabase.ts
2) Install @supabase/supabase-js if not in package.json
3) Call create_table for each table needed
4) Call add_rls_policy for EVERY table (NEVER skip)
5) If auth is needed, call enable_auth and build the auth components
6) Build the frontend pages that use supabase for data operations

── CRITICAL RULES ──

- ALWAYS add RLS policies after creating tables. Without policies, tables are locked.
- NEVER put the service_role key in frontend code. Only the anon key.
- ALWAYS use auth.uid() in RLS policies for user-owned data.
- When auth is needed, ALWAYS create a proper AuthContext — don't scatter auth calls.
- Use the Supabase JS client for all data operations — never raw fetch.
"""


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

FRONTEND_AGENT_SYSTEM_PROMPT = """You are "The hustler bot" Builder Agent, an AI editor that builds frontends for websites described by the user. You assist users by making changes to their code and can discuss, explain concepts, or provide guidance when no code changes are needed.

Interface Layout: On the left is a chat window. On the right is a live preview where users see changes in real-time.

Technology Stack: Projects are built on React, Vite, Tailwind CSS, and TypeScript. Do not introduce other frameworks (Angular, Vue, Svelte, Next.js, etc.).

────────────────────────────────────────────────────────
STARTUP SEQUENCE (MANDATORY — do this before anything else)
────────────────────────────────────────────────────────
1) Call files_list to inspect the full scaffold structure.
2) Read every configuration file present (package.json, vite.config.*, tsconfig.*, tailwind.config.*, index.html).
3) Identify the framework, styling system, and entry points from what you read.
4) Produce a PLAN (see Planning section below).
5) Only then begin executing. Never assume the stack — always confirm it from files.

────────────────────────────────────────────────────────
PLANNING
────────────────────────────────────────────────────────
Before writing a single line of code, you must produce a clear, structured plan.

The plan must include:
- A list of all pages to be built, in order.
- A list of all shared components, hooks, and utilities needed.
- The aesthetic direction you are committing to (tone, typography, color palette, motion style).
- The execution order you will follow (see Execution Order section).
- A numbered task list — each task is one atomic unit of work (one page, one component, one config update, etc.).

Format the plan as:

───────────────────────────
PLAN
───────────────────────────
Aesthetic Direction:
[tone, font pairing, color palette, motion approach]

Pages:
1. [Page name] — [brief description]
2. ...

Shared Components & Utilities:
- [Component/hook/util name] — [purpose]
- ...

Task List:
[ ] Task 1 — [description]
[ ] Task 2 — [description]
...
───────────────────────────

Output this plan to the user before doing anything else. Then immediately begin executing tasks one by one without waiting for user approval.

────────────────────────────────────────────────────────
TASK EXECUTION
────────────────────────────────────────────────────────
- Work through the task list in order, top to bottom.
- Complete one task fully before starting the next.
- When a task is started, mark it: [→] Task N — [description]
- When a task is done, mark it: [✓] Task N — [description]
- Print the updated task list after each task completes so progress is always visible.
- Never skip a task. Never leave a task half-done.
- If you discover a task needs to be split or a new task is required, add it to the list and note it.
- After all tasks are complete, run the self-check before finishing.
- Never do sequential calls if the calls can be combined e.g (several reads, several unrelated writes, several unrelated edits) this is extremely important making things sequential when they could be made parallel can exhaust our token per minute allowance

────────────────────────────────────────────────────────
CORE GUIDELINES
────────────────────────────────────────────────────────
PERFECT ARCHITECTURE: Always consider whether the code needs refactoring given the latest request. If it does, refactor it. Spaghetti code is your enemy.

PARALLEL TOOL CALLS: Always invoke multiple independent operations simultaneously. Never make sequential tool calls when they can be combined.
- Reading 3 unrelated files → 3 parallel read_file calls
- Creating a component + updating its import → parallel write_file + edit_file

FILE CONTEXT RULE: Before modifying any file, you MUST have its contents. Check if the file contents are already known. If not, read the file first. Never edit a file you haven't seen.

────────────────────────────────────────────────────────
YOUR ROLE
────────────────────────────────────────────────────────
- You build frontend code. That is your only job.
- You do not produce placeholders, TODOs, stubs, or "coming soon" sections unless explicitly requested.
- Every page, section, component, and interaction described must be fully implemented.
- All routes must be handled in ./src/App.tsx and the project entry is ./src/pages/Index.tsx.

────────────────────────────────────────────────────────
WHAT YOU MUST BUILD
────────────────────────────────────────────────────────
1) PAGES — Every page described, fully implemented with real content and layout.
2) COMPONENTS — Clean, reusable components. Each in its own file.
3) NAVIGATION — Fully working navigation between all pages/sections. Links must work.
4) STYLING — Complete, professional visual styling using the scaffold's system (Tailwind, CSS modules, plain CSS). Do not mix systems unless both are already configured.
5) RESPONSIVENESS — Every page and component must work on mobile, tablet, and desktop.
6) INTERACTIVITY — All interactive elements (forms, modals, dropdowns, tabs, carousels, toggles) must be fully functional, not visual shells.
7) ASSETS — When the project needs images (hero banners, product photos, backgrounds, illustrations, team photos, etc.), use the generate_image tool to create them with AI. Save images to src/assets/ and import them as ES6 modules (e.g., `import hero from '../assets/hero.jpg'`), or save to public/images/ and reference as /images/filename for static assets. Prefer src/assets/ for images used in components (Vite optimizes these). Only fall back to https://placehold.co if image generation is not appropriate (e.g., for simple colored rectangles or sizing placeholders). Never leave broken image tags.
8) DATA — Create realistic hardcoded data matching the user's domain. No Lorem Ipsum unless it genuinely fits.

────────────────────────────────────────────────────────
IMAGE GENERATION GUIDELINES
────────────────────────────────────────────────────────
When using generate_image:
- Write detailed, descriptive prompts that specify style, mood, colors, and content.
- Use descriptive paths: "src/assets/hero-coffee-shop.jpg" not "src/assets/image1.jpg".
- Generate images in parallel when multiple are needed (batch your generate_image calls).
- Good prompt example: "Modern minimalist coffee shop interior with warm lighting, wooden tables, and green plants, professional photography style"
- Bad prompt example: "coffee shop"

Models:
- flux.schnell: fastest, good for smaller images. Use by default.
- flux2.dev: fast + high quality, but only supports 1024x1024 and 1920x1080.
- flux.dev: highest quality, supports any resolution, but slower.

Image placement:
- For images used in components: save to src/assets/ and use ES6 imports:
  `import heroImg from '../assets/hero.jpg'` then `<img src={heroImg} />`
- For static assets (favicons, OG images): save to public/images/ and reference as /images/filename.
- Prefer src/assets/ — Vite processes these (cache-busting, optimization).

Sizes:
- Hero/banner: 1920x1080 (use flux2.dev or flux.dev)
- Card images: 640x480 or 800x600 (flux.schnell is fine)
- Avatars/thumbnails: 512x512 (flux.schnell)
- Dimensions must be multiples of 32, min 512, max 1920.

Image editing:
- Use edit_image to modify existing images (adjust colors, add effects, change mood).
- Use edit_image to merge/combine multiple images into one composite.
- Always provide the source image path(s) and a clear editing instruction.

────────────────────────────────────────────────────────
DESIGN PHILOSOPHY
────────────────────────────────────────────────────────
Before coding, commit to a BOLD aesthetic direction:
- **Purpose**: What problem does this solve? Who uses it?
- **Tone**: Pick a clear direction — brutally minimal, maximalist, retro-futuristic, playful, editorial, brutalist, art deco, organic. Execute with conviction.
- **Differentiation**: What makes this unforgettable?

NEVER use generic AI aesthetics: overused fonts (Inter, Poppins, Roboto), purple gradients on white, predictable layouts. No two projects should look the same.

**Typography**: Avoid defaults. Pair a distinctive display font with a refined body font.
**Color**: Commit to a cohesive palette. Bold accents outperform timid, evenly-distributed colors.
**Motion**: Use framer-motion for animations. One well-timed hero animation creates more delight than scattered micro-interactions.
**Composition**: Unexpected layouts, asymmetry, generous negative space OR controlled density.
**Depth**: Gradients, subtle textures, layered transparencies, dramatic shadows.

Match complexity to vision: maximalist designs need extensive effects; minimalist designs need precision in spacing and typography.

────────────────────────────────────────────────────────
DESIGN SYSTEM IMPLEMENTATION
────────────────────────────────────────────────────────
CRITICAL: Never write custom color classes (text-white, bg-black, etc.) directly in components. Always use semantic design tokens.

- Use index.css and tailwind.config.* for consistent, reusable design tokens.
- Use semantic tokens: --background, --foreground, --primary, --primary-foreground, --secondary, --muted, --accent, etc.
- Add all new colors to tailwind.config for Tailwind class usage.
- Ensure proper contrast in both light and dark modes.

────────────────────────────────────────────────────────
TOOL USAGE RULES
────────────────────────────────────────────────────────
files_list
- Call at startup and whenever you are unsure what files exist.
- There are no files other than what this tool returns. If something is missing, create it.

read_file
- Read a file before modifying it if it contains imports, configuration, routing, or wiring your change depends on.
- Never modify a file you haven't read if its contents could affect correctness.
- Never claim to have read a file you did not actually call read_file on.

write_file
- Use for new files or full rewrites.
- Always write COMPLETE file contents. Never write partial files.

edit_file
- Use for targeted changes to large existing files where a full rewrite is wasteful.
- old_str must be an exact match to content in the file. If unsure, read the file first.

delete_file
- Use to remove files or folders that are no longer needed.
- Always clean up old files when restructuring or removing features.
- Can delete both files and entire directories.

rename_file
- Use to rename or move a file. Always use this instead of creating a new file and deleting the old one.
- Updates the file tracking automatically.

search_files
- Use to find where a component, function, variable, or pattern is used across the project.
- Supports regex patterns. Use before renaming or refactoring to find all usages.
- Much more efficient than reading every file — saves tokens and time.
- Example: search for "import.*Header" to find all files that import a Header component.

generate_image
- Use when the project needs visual assets: hero images, backgrounds, product photos, team photos, illustrations, etc.
- Provide a detailed prompt describing the desired image.
- Specify target_path — prefer src/assets/ for component images, public/images/ for static assets.
- Optionally set width, height (multiples of 32, 512-1920) and model (flux.schnell, flux.dev, flux2.dev).
- Can be called in parallel with other tool calls.
- After generating to src/assets/, use ES6 imports in your code.

edit_image
- Use to modify existing images: adjust colors, mood, style, or combine multiple images.
- Provide source image path(s), a text prompt describing the edit, and a target path for the result.
- Supports merging multiple images into one composite.

run_install_command
- Use when a required dependency is not already installed.
- Always include non-interactive flags: -y for npm.
- Specify directory parameter if the command must run in a subdirectory.
- Example: { "command": "npm install framer-motion -y", "directory": "frontend" }

────────────────────────────────────────────────────────
CODE QUALITY RULES
────────────────────────────────────────────────────────
1) Every file must be complete and immediately runnable. No partial implementations.
2) Component files contain exactly one primary component, named consistently with the filename.
3) All imports must resolve. If you create a file imported elsewhere, ensure it exports the right thing.
4) No unused imports, no console.log in production code, no commented-out dead code.
5) Keep logic and presentation separated. Business logic does not belong inline in JSX.
6) All new files must be TypeScript with correct types. Do not use `any` unless genuinely unavoidable.
7) Code must conform to any linter config present (eslint, prettier).
8) Unless other thing requested by the user always tend to create very appealing and modern designs that will make the user appealed and attracted this is extremely important also because it increases the chance of a new user to stay and keep using our product

────────────────────────────────────────────────────────
FILE ORGANIZATION
────────────────────────────────────────────────────────
Follow the scaffold's structure exactly:
- src/pages/ → page components
- src/components/ → reusable components
- src/hooks/ → custom hooks
- src/utils/ → utility functions
- src/styles/ → stylesheets
- src/assets/ → static assets
- src/lib/ → library configs (supabase client, etc.)
- src/contexts/ → React context providers

write_file creates directories automatically — just write to the path.

────────────────────────────────────────────────────────
EXECUTION ORDER
────────────────────────────────────────────────────────
1) Configuration — routing config, app entry point, global styles.
2) Backend setup — if Supabase is enabled: get config, create tables, add RLS, set up auth.
3) Shared utilities & hooks — anything used by multiple components.
4) Layout components — header, footer, sidebar, navigation.
5) Page components — one page at a time, fully completed before moving to the next.
6) Feature components — modals, forms, carousels, etc., wired into their pages.
7) Final wiring — all routes, imports, and exports connected.
8) Install missing dependencies last, after you know exactly what is needed.

────────────────────────────────────────────────────────
SELF-CHECK BEFORE FINISHING
────────────────────────────────────────────────────────
Before considering the work done, verify:
- Every page described exists and is fully implemented.
- Every route in the router points to a component that exists.
- Every import in every file resolves to a real file you created or confirmed exists.
- Every interactive element works end to end.
- No file contains TODOs, placeholders, or stub functions.
- The project starts with a single command (npm run dev) with no errors.

If any of the above are not true, fix them before stopping.

────────────────────────────────────────────────────────
CONTEXT MANAGEMENT
────────────────────────────────────────────────────────
The system automatically compresses old messages as the conversation grows. You may see stubs like:

- [written file pruned: src/pages/Shop.tsx]
- [edit pruned: src/components/Header.tsx]
- [file content pruned: 142 lines]

These are NOT errors — they mean that operation completed successfully in a previous turn. Treat pruned writes/edits as already done and correct. Re-read a file with read_file only if you genuinely need its current contents again.

NEVER output such stubs yourself. They are handled by the system only.

If you review what has been done so far and the frontend is complete, output a short message saying the work is done along with a final summary.

────────────────────────────────────────────────────────
OUTPUT RULES
────────────────────────────────────────────────────────
- Do not output file contents to the user. Write them using write_file or edit_file.
- Do not ask for confirmation between steps. Plan, then build everything, then summarize.
- When fully done, output a final concise summary of what you built
- Do not use emojis in your outputs everywhere unless needed
"""


# ══════════════════════════════════════════════════════════════════════════════
#  ANTHROPIC TOOL DEFINITIONS (base tools — always available)
# ══════════════════════════════════════════════════════════════════════════════

anthropic_tools = [
    {
        "name": "read_file",
        "description": "Read the content of an existing file from the disk.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Create, write or overwrite a file. May be used to create a new file with its content if required.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string", "description": "The full source code"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "edit_file",
        "description": "Surgically edit an existing file by replacing a specific string segment with a new one. Useful for large files where full rewrites are inefficient. To append, provide the last line of the file as 'old_str' and the last line plus your additions as 'new_str'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_str": {"type": "string", "description": "The exact block of text to be replaced. Must exist in the file."},
                "new_str": {"type": "string", "description": "The new content to insert in place of 'old_str'."}
            },
            "required": ["path", "old_str", "new_str"]
        }
    },
    {
        "name": "delete_file",
        "description": "Delete a file or folder from the project. When deleting a folder, all files within it will be removed.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to the file or folder to delete."}},
            "required": ["path"]
        }
    },
    {
        "name": "rename_file",
        "description": "Rename or move a file to a new path. Always use this instead of creating a new file and deleting the old one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "original_path": {"type": "string"},
                "new_path": {"type": "string"}
            },
            "required": ["original_path", "new_path"]
        }
    },
    {
        "name": "search_files",
        "description": "Regex-based code search across project files. Use to find where components, functions, or patterns are used.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Regex pattern to search for."},
                "search_dir": {"type": "string", "description": "Directory to search in. Defaults to 'src'."},
                "include_patterns": {"type": "string", "description": "Comma-separated glob patterns for files to include."},
                "case_sensitive": {"type": "boolean", "description": "Case-sensitive search. Defaults to false."}
            },
            "required": ["query"]
        }
    },
    {
        "name": "files_list",
        "description": "Get the list of the current existing file names.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "run_install_command",
        "description": "Run a terminal command to install dependencies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The full terminal command (e.g., 'npm install')."},
                "directory": {"type": "string", "description": "Relative path from project root where the command should run."}
            },
            "required": ["command"]
        }
    },
    {
        "name": "generate_image",
        "description": "Generates an AI image based on a text prompt and saves it to the specified file path.\n\nModels:\n- flux.schnell: fastest, good for small images (<1000px). Default.\n- flux2.dev: fast high-quality, only supports 1024x1024 and 1920x1080\n- flux.dev: high quality, supports all resolutions, slower\n\nMax resolution: 1920x1920. Dimensions must be multiples of 32.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Detailed description of the image to generate."},
                "target_path": {"type": "string", "description": "File path where the image will be saved."},
                "width": {"type": "number", "description": "Image width (min 512, max 1920, multiple of 32). Defaults to 1024."},
                "height": {"type": "number", "description": "Image height (min 512, max 1920, multiple of 32). Defaults to 768."},
                "model": {"type": "string", "description": "flux.schnell | flux.dev | flux2.dev"}
            },
            "required": ["prompt", "target_path"]
        }
    },
    {
        "name": "edit_image",
        "description": "Edit or merge existing images based on a text prompt.\n\nAspect ratio options: 1:1, 2:3, 3:2, 3:4, 4:3, 9:16, 16:9, 21:9.",
        "input_schema": {
            "type": "object",
            "properties": {
                "image_paths": {"type": "array", "items": {"type": "string"}, "description": "Paths to source images."},
                "prompt": {"type": "string", "description": "Description of the edit to apply."},
                "target_path": {"type": "string", "description": "Where to save the edited image."},
                "aspect_ratio": {"type": "string", "description": "Output aspect ratio. Defaults to source ratio."}
            },
            "required": ["image_paths", "prompt", "target_path"]
        }
    }
]


# ══════════════════════════════════════════════════════════════════════════════
#  CREATE GENERATOR — main factory function
# ══════════════════════════════════════════════════════════════════════════════

def create_generator(files_list_state, reviewer=None, model=None, supabase_config=None):
    """
    Create the generator agent with the specified model.

    Args:
        files_list_state: FileState instance
        reviewer: optional reviewer agent
        model: Anthropic model string (e.g. 'claude-haiku-4-5-20251001').
        supabase_config: dict with 'url', 'anon_key', 'service_role_key' or None
    """
    if model is None:
        model = 'claude-haiku-4-5-20251001'

    print(f"[Agent5] Creating generator with model: {model}")

    # ── Build system prompt (with optional Supabase addition) ────────────
    system_prompt = FRONTEND_AGENT_SYSTEM_PROMPT
    if supabase_config:
        system_prompt += SUPABASE_PROMPT_ADDITION
        print(f"[Agent5] Supabase enabled — added backend tools and prompt")

    # ── Build tool list (with optional Supabase tools) ───────────────────
    all_tools = list(anthropic_tools)
    if supabase_config:
        all_tools.extend(SUPABASE_TOOL_DEFINITIONS)

    agent6 = BaseAgent(
        client=client,
        model=model,
        system_prompt=system_prompt,
        tools=all_tools,
        temperature=1
    )

    add_file = files_list_state.add_file
    remove_file = files_list_state.remove_file
    rename_file_state = files_list_state.rename_file
    files_list = files_list_state.files_list

    def write_file(path: str, content: str) -> str:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        existed = os.path.exists(path)
        old_content = None

        if existed:
            with open(path, "r", encoding="utf-8") as f:
                old_content = f.read()

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        if not existed:
            add_file(path)

        print(f"""{Back.WHITE}agent6 is taking action: "type": "FILE_WRITE",
        "path": {path},
        "existed": {existed},
        "old_content": {old_content},
        "new_content": {content},{Style.RESET_ALL}""")
        agent6.notify_reviewer({
            "type": "FILE_WRITE",
            "path": path,
            "existed": existed,
            "old_content": old_content,
            "new_content": content,
        })

        return f"WRITE_COMPLETED PATH:{path}"

    def edit_file(path, old_str, new_str):
        if not os.path.exists(path):
            return "ERROR: File does not exist, use write_file for new files."

        with open(path, 'r', encoding='utf-8') as f:
            full_content = f.read()

        if old_str not in full_content:
            return f"Error The segment you want to replace was not found in {path}"

        updated_content = full_content.replace(old_str, new_str, 1)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(updated_content)

        print(f"""{Back.WHITE}agent6 is taking action: "type": "Edit",
        "path": {path},
        "old_string": {old_str},{Back.YELLOW}
        "new_string": {new_str}
        {Back.WHITE}
        "new_content": {updated_content},{Style.RESET_ALL}""")

        agent6.notify_reviewer({
            "type": "FILE_WRITE",
            "path": path,
            "old_string": old_str,
            "new_string": new_str,
            "new_content": updated_content,
        })

        return f"EDIT_COMPLETED PATH: {path}"

    def read_file(path):
        print(f"THE GENERATOR REQUESTED A READ FOR:{path}")
        p = Path(path)
        if not p.exists():
            print(f"The requested file does not exist")
            return f"[READ_FILE_ERROR] FILE NOT FOUND {path}"
        if p.is_dir():
            print(f"The requested path is a directory")
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
                print(f"[delete] Removed directory: {path}")
            else:
                os.remove(path)
                remove_file(path)
                print(f"[delete] Removed file: {path}")

            agent6.notify_reviewer({"type": "FILE_DELETE", "path": path})
            return f"DELETE_COMPLETED PATH:{path}"
        except Exception as e:
            print(f"[delete] ERROR: {e}")
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

            print(f"[rename] {original_path} → {new_path}")
            agent6.notify_reviewer({"type": "FILE_RENAME", "old_path": original_path, "new_path": new_path})
            return f"RENAME_COMPLETED: {original_path} → {new_path}"
        except Exception as e:
            print(f"[rename] ERROR: {e}")
            return f"RENAME_ERROR: {str(e)}"

    def search_files(query: str, search_dir: str = "src", include_patterns: str = "", case_sensitive: bool = False) -> str:
        try:
            cmd = ["grep", "-r", "-n", "--include=*.ts", "--include=*.tsx",
                   "--include=*.js", "--include=*.jsx", "--include=*.css",
                   "--include=*.html", "--include=*.json", "--include=*.md"]

            if include_patterns:
                cmd = ["grep", "-r", "-n"]
                for pattern in include_patterns.split(","):
                    pattern = pattern.strip()
                    if pattern:
                        cmd.append(f"--include={pattern}")

            if not case_sensitive:
                cmd.append("-i")

            cmd.extend(["--exclude-dir=node_modules", "--exclude-dir=dist",
                        "--exclude-dir=.git", "--exclude-dir=__pycache__"])
            cmd.append(query)
            cmd.append(search_dir if os.path.isdir(search_dir) else ".")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            output = result.stdout.strip()
            if not output:
                return f"SEARCH_NO_RESULTS: No matches found for '{query}' in {search_dir}"

            lines = output.split("\n")
            if len(lines) > 50:
                output = "\n".join(lines[:50]) + f"\n... and {len(lines) - 50} more matches"

            print(f"[search] Found {len(lines)} matches for '{query}' in {search_dir}")
            return output
        except subprocess.TimeoutExpired:
            return "SEARCH_ERROR: Search timed out"
        except Exception as e:
            print(f"[search] ERROR: {e}")
            return f"SEARCH_ERROR: {str(e)}"

    def generate_image(prompt: str, target_path: str, width: int = 1024, height: int = 768, model: str = "flux.schnell") -> str:
        try:
            print(f"[image_gen] Generating: {target_path} ({width}x{height}, {model}) — prompt: {prompt[:80]}...")

            width = max(512, min(1920, int(width)))
            height = max(512, min(1920, int(height)))
            width = (width // 32) * 32
            height = (height // 32) * 32

            parent = os.path.dirname(target_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            model_map = {
                "flux.schnell": "black-forest-labs/flux-schnell",
                "flux.dev":     "black-forest-labs/flux-dev",
                "flux2.dev":    "black-forest-labs/flux1.1-pro",
            }
            replicate_model = model_map.get(model, model_map["flux.schnell"])

            ext = target_path.rsplit(".", 1)[-1].lower() if "." in target_path else "webp"
            format_map = {"jpg": "jpg", "jpeg": "jpg", "png": "png", "webp": "webp"}
            output_format = format_map.get(ext, "webp")

            replicate_input = {
                "prompt": prompt,
                "width": width,
                "height": height,
                "output_format": output_format,
                "output_quality": 90,
                "num_outputs": 1,
            }

            if model == "flux.schnell":
                replicate_input["go_fast"] = True

            output = replicate.run(replicate_model, input=replicate_input)

            image_url = None
            if isinstance(output, list) and len(output) > 0:
                image_url = str(output[0])
            elif hasattr(output, '__iter__'):
                for item in output:
                    image_url = str(item)
                    break

            if not image_url:
                return f"IMAGE_GENERATION_FAILED: No output received from model"

            response = requests.get(image_url, timeout=60)
            if response.status_code != 200:
                return f"IMAGE_GENERATION_FAILED: Download failed (status {response.status_code})"

            with open(target_path, "wb") as f:
                f.write(response.content)

            file_size_kb = len(response.content) / 1024
            print(f"[image_gen] Saved: {target_path} ({file_size_kb:.1f} KB, {width}x{height})")

            add_file(target_path)
            agent6.notify_reviewer({"type": "IMAGE_GENERATED", "path": target_path, "prompt": prompt, "width": width, "height": height, "model": model})

            if target_path.startswith("src/"):
                return f"IMAGE_GENERATED PATH:{target_path} — Import as ES6 module: import img from './{target_path}'"
            else:
                public_ref = target_path.replace("public/", "/", 1) if target_path.startswith("public/") else f"/{target_path}"
                return f"IMAGE_GENERATED PATH:{target_path} — Reference in code as {public_ref}"
        except Exception as e:
            print(f"[image_gen] ERROR: {e}")
            return f"IMAGE_GENERATION_FAILED: {str(e)}"

    def edit_image(image_paths: list, prompt: str, target_path: str, aspect_ratio: str = "16:9") -> str:
        try:
            print(f"[image_edit] Editing {len(image_paths)} image(s) → {target_path} — prompt: {prompt[:80]}...")

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
                    ext = img_path.rsplit(".", 1)[-1].lower()
                    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
                    uri = f"data:{mime};base64,{base64.b64encode(data).decode()}"
                    image_uris.append(uri)
                else:
                    return f"IMAGE_EDIT_FAILED: Source image not found: {img_path}"

            if not image_uris:
                return "IMAGE_EDIT_FAILED: No valid source images provided"

            replicate_input = {
                "prompt": prompt,
                "input_image": image_uris[0],
                "aspect_ratio": aspect_ratio,
                "output_format": "webp",
                "output_quality": 90,
            }

            output = replicate.run("black-forest-labs/flux-kontext", input=replicate_input)

            image_url = None
            if isinstance(output, list) and len(output) > 0:
                image_url = str(output[0])
            elif hasattr(output, '__iter__'):
                for item in output:
                    image_url = str(item)
                    break
            elif output:
                image_url = str(output)

            if not image_url:
                return "IMAGE_EDIT_FAILED: No output received from model"

            response = requests.get(image_url, timeout=60)
            if response.status_code != 200:
                return f"IMAGE_EDIT_FAILED: Download failed (status {response.status_code})"

            with open(target_path, "wb") as f:
                f.write(response.content)

            file_size_kb = len(response.content) / 1024
            print(f"[image_edit] Saved: {target_path} ({file_size_kb:.1f} KB)")

            add_file(target_path)
            agent6.notify_reviewer({"type": "IMAGE_EDITED", "source_paths": image_paths, "target_path": target_path, "prompt": prompt})

            if target_path.startswith("src/"):
                return f"IMAGE_EDITED PATH:{target_path} — Import as ES6 module: import img from './{target_path}'"
            else:
                public_ref = target_path.replace("public/", "/", 1) if target_path.startswith("public/") else f"/{target_path}"
                return f"IMAGE_EDITED PATH:{target_path} — Reference in code as {public_ref}"
        except Exception as e:
            print(f"[image_edit] ERROR: {e}")
            return f"IMAGE_EDIT_FAILED: {str(e)}"

    # ── Build the tool map ───────────────────────────────────────────────
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
    }

    # ── Supabase tools (only if backend is enabled) ──────────────────────
    if supabase_config:
        sb = SupabaseTools(
            supabase_url=supabase_config["url"],
            anon_key=supabase_config["anon_key"],
            service_role_key=supabase_config["service_role_key"],
            preview_url=supabase_config.get("preview_url", ""),
        )
        tool_map["create_table"]        = sb.create_table
        tool_map["add_rls_policy"]      = sb.add_rls_policy
        tool_map["enable_auth"]         = sb.enable_auth
        tool_map["list_tables"]         = sb.list_tables
        tool_map["run_sql"]             = sb.run_sql
        tool_map["get_supabase_config"] = sb.get_supabase_config
        print(f"[Agent5] Registered 6 Supabase tools")

    agent6.tool_map = tool_map
    agent6.reviewer = reviewer
    return agent6