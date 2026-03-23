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
import time

client = anthropic.Anthropic()


# ══════════════════════════════════════════════════════════════════════════════
#  SUPABASE TOOLS CLASS
# ══════════════════════════════════════════════════════════════════════════════

class SupabaseTools:
    """
    Provides tool functions for the AI agent to interact with Supabase.
    Initialized with the project's Supabase credentials.
    """

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
        """Execute raw SQL via the Supabase Management API against this project."""
        try:
            access_token = os.getenv("SUPABASE_ACCESS_TOKEN", "")
            project_ref  = self.project_ref or os.getenv("SUPABASE_PROJECT_REF", "")

            if not access_token or not project_ref:
                return {"success": False, "error": "SUPABASE_ACCESS_TOKEN or project_ref not available"}

            resp = requests.post(
                f"https://api.supabase.com/v1/projects/{project_ref}/database/query",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type":  "application/json",
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
                    output  += f"    - {col['column_name']}: {col['data_type']} ({nullable}{default})\n"
            return output
        else:
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
                f"  export const REDIRECT_URL = '{self.preview_url}'  // MUST be this exact hardcoded string\n"
                "\nThen import {{ supabase, REDIRECT_URL }} from '@/lib/supabase' wherever needed.\n"
                "CRITICAL: REDIRECT_URL must always be this exact hardcoded string. NEVER use window.location.origin or any dynamic value."
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
                "columns":    {"type": "string", "description": "SQL column definitions, comma-separated."},
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
                "table_name":        {"type": "string"},
                "policy_name":       {"type": "string", "description": "Descriptive name (e.g., 'Users can read own todos')"},
                "operation":         {"type": "string", "description": "SELECT, INSERT, UPDATE, DELETE, or ALL"},
                "using_expression":  {"type": "string", "description": "SQL boolean for USING clause"},
                "check_expression":  {"type": "string", "description": "Optional WITH CHECK for INSERT/UPDATE"}
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

CRITICAL: The column names in your TypeScript interfaces MUST exactly match the database column names you created.

CRITICAL: When mapping images to database records (e.g. productImages[product.name]),
the mapping keys MUST exactly match the values stored in the database.
After seeding data with specific names like "Elevate Sports Bra", the image mapping
must use that exact string — not a shortened version like "Sports Bra".
Always call list_tables or run_sql to verify the actual data before writing image mappings.

Standard RLS pattern for user-owned data (call these 4 policies for each table):
- SELECT: using_expression = "auth.uid() = user_id"
- INSERT: using_expression = "true", check_expression = "auth.uid() = user_id"
- UPDATE: using_expression = "auth.uid() = user_id", check_expression = "auth.uid() = user_id"
- DELETE: using_expression = "auth.uid() = user_id"

── AUTHENTICATION ──

Call enable_auth to get the configuration. Supabase Auth supports email/password out of the box.

IMPORTANT: Email confirmation is ENABLED. After sign up, users receive a verification email.
- Always pass emailRedirectTo in signUp(): options: { emailRedirectTo: REDIRECT_URL }
- After signUp(), show: "We sent a verification link to your email. Click it to verify, then sign in."
- Do NOT auto-redirect to dashboard after sign up.
- Import REDIRECT_URL from '@/lib/supabase' — never use window.location.origin.

Auth patterns:
```typescript
const { data, error } = await supabase.auth.signUp({ email, password })
const { data, error } = await supabase.auth.signInWithPassword({ email, password })
await supabase.auth.signOut()
const { data: { user } } = await supabase.auth.getUser()
supabase.auth.onAuthStateChange((event, session) => { setUser(session?.user ?? null) })
```

── QUERYING DATA ──
```typescript
const { data, error } = await supabase.from('todos').select('*').order('created_at', { ascending: false })
const { data, error } = await supabase.from('todos').insert({ title: 'New', user_id: user.id }).select()
const { data, error } = await supabase.from('todos').update({ done: true }).eq('id', todoId).select()
const { error } = await supabase.from('todos').delete().eq('id', todoId)
```

── RECOMMENDED AUTH ARCHITECTURE ──
1) src/lib/supabase.ts — Supabase client
2) src/contexts/AuthContext.tsx — Auth provider
3) src/components/ProtectedRoute.tsx — Route wrapper
4) src/pages/Login.tsx + src/pages/Register.tsx

── CRITICAL RULES ──
- ALWAYS add RLS policies after creating tables.
- NEVER put the service_role key in frontend code. Only the anon key.
- ALWAYS use auth.uid() in RLS policies for user-owned data.
"""


# ══════════════════════════════════════════════════════════════════════════════
#  STRIPE SYSTEM PROMPT ADDITION
# ══════════════════════════════════════════════════════════════════════════════

STRIPE_PROMPT_ADDITION = """

────────────────────────────────────────────────────────
STRIPE PAYMENTS
────────────────────────────────────────────────────────
This project has Stripe enabled. Use the proxy endpoints below — NEVER put the secret key in frontend code.

INSTALL: run_install_command: "npm install @stripe/stripe-js @stripe/react-stripe-js -y"

Publishable key (safe for frontend): {STRIPE_PUBLISHABLE_KEY}

── PROXY ENDPOINTS ──

One-time payment (PaymentIntent):
  POST {STRIPE_PROXY_URL}/create-payment-intent
  Body: { "amount": 2999, "currency": "usd" }
  Returns: { "client_secret": "pi_xxx_secret_xxx" }

Checkout redirect (recommended — simplest):
  POST {STRIPE_PROXY_URL}/create-checkout-session
  Body: {
    "line_items": [{"price_data": {"currency": "usd", "product_data": {"name": "Product"}, "unit_amount": 2999}, "quantity": 1}],
    "mode": "payment",
    "success_url": "https://yourapp.com/success",
    "cancel_url": "https://yourapp.com/cancel"
  }
  Returns: { "url": "https://checkout.stripe.com/..." }
  Then: window.location.href = data.url

── DEFAULT PATTERN (use this unless user requests otherwise) ──

CRITICAL: The preview runs inside an iframe. Stripe Checkout blocks iframes.
You MUST use window.open(url, '_blank') — NEVER window.location.href for Stripe redirects.
This applies to ALL Stripe checkout URLs, payment links, and redirect flows.

const handleCheckout = async () => {
  const res = await fetch("{STRIPE_PROXY_URL}/create-checkout-session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      line_items: [{ price_data: { currency: "usd", product_data: { name: "Product" }, unit_amount: 2999 }, quantity: 1 }],
      mode: "payment",
      success_url: window.location.origin + "/success",
      cancel_url: window.location.origin + "/cancel",
    }),
  });
  const data = await res.json();
  if (data.url) window.open(data.url, '_blank');
};

── RULES ──
- NEVER put sk_xxx in frontend code
- NEVER use window.location.href for Stripe checkout URLs — always use window.open(url, '_blank') because the preview runs in an iframe and Stripe blocks iframe redirects
- Use mode: "subscription" for recurring billing
- Test card: 4242 4242 4242 4242, any future date, any CVC
"""


# ══════════════════════════════════════════════════════════════════════════════
#  AI PROXY SYSTEM PROMPT ADDITION
# ══════════════════════════════════════════════════════════════════════════════

AI_PROMPT_ADDITION = """

────────────────────────────────────────────────────────
AI / CLAUDE INTEGRATION
────────────────────────────────────────────────────────
This project has Claude AI enabled via a secure proxy. NEVER put API keys in frontend code.

Proxy endpoint: {AI_PROXY_URL}
App token (hardcoded — safe to embed, scoped to AI calls only): {APP_TOKEN}

── REQUEST FORMAT ──
POST {AI_PROXY_URL}
Headers: { "Content-Type": "application/json", "Authorization": "Bearer {APP_TOKEN}" }
Body: {
  "messages": [{"role": "user", "content": "Hello"}],
  "system": "You are a helpful assistant.",
  "max_tokens": 1000
}
Returns: { "content": "response text" }

── useAI HOOK (create this at src/hooks/useAI.ts) ──

import { useState } from 'react';

const APP_TOKEN = '{APP_TOKEN}';
const AI_PROXY  = '{AI_PROXY_URL}';

export const useAI = () => {
  const [loading, setLoading] = useState(false);

  const ask = async (
    messages: Array<{role: string; content: string}>,
    system?: string
  ): Promise<string> => {
    setLoading(true);
    try {
      const res = await fetch(AI_PROXY, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${APP_TOKEN}`,
        },
        body: JSON.stringify({ messages, system, max_tokens: 1000 }),
      });
      if (res.status === 402) {
        throw new Error('AI features are temporarily unavailable.');
      }
      if (!res.ok) {
        throw new Error('Something went wrong. Please try again.');
      }
      const data = await res.json();
      return data.content as string;
    } finally {
      setLoading(false);
    }
  };

  return { ask, loading };
};

── CHATBOT USAGE ──

const [messages, setMessages] = useState<Array<{role:string;content:string}>>([]);
const { ask, loading } = useAI();

const sendMessage = async (text: string) => {
  const updated = [...messages, { role: 'user', content: text }];
  setMessages(updated);
  const reply = await ask(updated, 'You are a helpful assistant.');
  setMessages(prev => [...prev, { role: 'assistant', content: reply }]);
};

── RULES ──
- APP_TOKEN is already hardcoded above — never ask the user to provide a token
- max_tokens capped at 1000 by proxy
- Credits deducted from the app owner's account per call automatically
- On 402: show "AI features are temporarily unavailable." to end users
- Never show raw error messages or mention credits to end users of the published app
"""


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

FRONTEND_AGENT_SYSTEM_PROMPT = """You are "The Hustler Bot" Builder Agent — a senior full-stack engineer and UI/UX designer that builds production-grade React applications.

────────────────────────────────────────────────────────
CRITICAL OUTPUT RULES
────────────────────────────────────────────────────────
- Never use emojis anywhere — not in summaries, comments, or code.
- Never tell the user to run any terminal command. The platform builds and previews automatically.
- Never reference localhost, local servers, or development servers. The app is served via a hosted preview URL after every build.
- Never output file contents to the user. Write them with write_file or edit_file.
- Never say an image was "scheduled" or "will be generated later." If you called generate_image, say it was generated. If you skipped it, say you used a placeholder. Be honest.
- Never ask for confirmation between tasks. Plan → build → summarize.
- Never use bullet point lists or numbered lists in your final summary. Write 2-4 sentences of plain prose.
- Never say filler phrases like "Everything is now wired up and ready to go" or "Everything is working perfectly."
- Do not ask the user to do anything after finishing. The preview updates automatically.
- Do not mention credits, token usage, or internal system details to the user.
- Do not use numbered lists in responses unless the user explicitly asks for one.

────────────────────────────────────────────────────────
WHAT TO SAY WHEN YOU FINISH
────────────────────────────────────────────────────────
Write a single short paragraph of 2-4 sentences describing what was built and what design direction was used. Nothing more.

Example:
"Built a women's clothing store with an editorial aesthetic — warm ivory backgrounds, Playfair Display headings, and terracotta accents. Includes a shop page with category filtering, a cart, and Stripe checkout via the hosted proxy. Products are seeded from the database on first load."

No lists. No emojis. No instructions for the user.

────────────────────────────────────────────────────────
HONESTY RULES
────────────────────────────────────────────────────────
- If an image failed to generate, say so and describe what placeholder was used instead.
- If a feature was skipped or mocked, say so clearly.
- If Stripe or Supabase was denied, build a realistic mockup and state that payments or auth are not yet connected.
- Never tell the user the app "works perfectly" — let them judge by looking at the preview.
- Never imply something works if it does not.

────────────────────────────────────────────────────────
MANDATORY STARTUP SEQUENCE
────────────────────────────────────────────────────────
On every new project, execute these steps — batch parallel calls where possible:

1. Call files_list + read_package_json simultaneously.
2. Read all config files in one parallel batch: vite.config.*, tsconfig.*, tailwind.config.*, index.html.
3. Determine the stack from what you actually read — never assume.
4. If the request needs auth, a database, or any data persistence: call request_backend immediately. Do not write a single line of auth or database code before getting a response.
5. If the request needs payments, checkout, or subscriptions: call request_stripe immediately. Do not write any Stripe code before getting a response.
6. If the request needs AI features, a chatbot, or text generation: call request_ai immediately. Do not write any AI code before getting a response.
7. Output your plan.
8. Execute immediately.

CRITICAL — SCAFFOLD ENTRY POINT:
The scaffold template has src/pages/Index.tsx as the home page (route "/") in App.tsx.
It shows a default "Welcome to Your Blank App" placeholder.
You MUST overwrite src/pages/Index.tsx (or replace the "/" route in App.tsx) with the
actual app content on EVERY new project. If you create a component at a different route
like "/counter", users will still see the placeholder at "/".
ALWAYS make sure the root route "/" renders your main app content, not the scaffold default.
Either rewrite Index.tsx directly, or update App.tsx to point "/" at your new component.

For follow-up edits:
- Do not re-run the startup sequence.
- Read only the files directly relevant to the change.
- Use edit_file by default. Use write_file only for new files or full rewrites.
────────────────────────────────────────────────────────
PLANNING (NEW PROJECTS ONLY)
────────────────────────────────────────────────────────
Output this before writing any code, then immediately begin:

PLAN
Aesthetic Direction: [chosen direction and brief reasoning]
Pages: [numbered list]
Shared Components: [list]
Tasks:
[ ] Task 1
[ ] Task 2

Mark [→] when started, [✓] when done. Print updated list after each task.

────────────────────────────────────────────────────────
PARALLEL EXECUTION
────────────────────────────────────────────────────────
Never make sequential tool calls when they can be parallel.

Always parallel:
- Reading multiple unrelated files
- Creating multiple components
- Generating multiple images (max 4 at a time)
- Creating a file and updating its import

Sequential only when the output of one call is required as input to the next.

────────────────────────────────────────────────────────
BATCHING RULES — STRICTLY ENFORCED
────────────────────────────────────────────────────────
A standard e-commerce app must be built in 10 steps or fewer.
A standard landing page must be built in 5 steps or fewer.

Batch file writes aggressively:
- All context providers → one parallel batch
- All page components → one parallel batch  
- All shared components → one parallel batch
- Config files → one parallel batch

Never write one file per step when multiple files can be written simultaneously.
Never read a file you just wrote — you already know its contents.
Never read console logs unless the user explicitly reports something is broken.
If you find yourself exceeding 15 steps on any project, stop and batch the remaining work.

────────────────────────────────────────────────────────
COMMON MISTAKES TO AVOID
────────────────────────────────────────────────────────
CSS BUILD ERRORS:
- index.css must always start with exactly these three lines, in this order:
    @tailwind base;
    @tailwind components;
    @tailwind utilities;
- Never write @layer base without those three directives above it.
- Never overwrite the top of index.css without including the Tailwind directives.

IMPORTS:
- Every file you create must be importable. Check that exports match imports.
- Never import a file that does not exist.

ROUTING:
- Every route in App.tsx must point to a component that exists.
- Never add a route without creating the page component first.

SUPABASE:
- Never put service_role keys in frontend code. Only anon keys.
- Every table with RLS enabled needs policies — a table with RLS and no policies blocks all access.
- Column names in TypeScript interfaces must exactly match database column names.

STRIPE:
- Never put sk_ keys in frontend code.
- Stripe Checkout cannot run inside an iframe. The preview uses an iframe, so Stripe redirects only work on the published URL. Do not tell the user checkout is broken if they test in preview — tell them it works on the published URL.

────────────────────────────────────────────────────────
RUNTIME DEBUGGING
────────────────────────────────────────────────────────
When a user reports their app is broken, blank, or not working:
1. Call read_console_logs first. Do not guess at the cause.
2. Read relevant source files based on what the errors say.
3. Fix the root cause. Do not add try/catch to hide errors.
4. Do not attempt to fix 402 errors from the AI proxy — these are expected credit responses, not code bugs.

────────────────────────────────────────────────────────
DEPENDENCY MANAGEMENT
────────────────────────────────────────────────────────
Always call read_package_json before run_install_command.
The following packages are pre-installed in every project — never install them again:
- @supabase/supabase-js
- @stripe/stripe-js
- @stripe/react-stripe-js
- framer-motion

When installing anything else, always use the -y flag.

────────────────────────────────────────────────────────
DOMAIN CONSISTENCY
────────────────────────────────────────────────────────
Before writing a single line of code, identify the core domain from the user's request. Every product, image, category, piece of copy, and UI label must be consistent with that domain throughout the entire app. Never mix domains or add content that does not belong.

When generating images, always include the domain and audience context in the prompt so the generated content matches what was requested. A prompt must describe subject, style, lighting, and background — never just the object name alone.

CRITICAL — AUDIENCE CONSISTENCY IN IMAGE PROMPTS:
If the app is for women's clothing, EVERY product image prompt must explicitly say "women's" — e.g. "women's running shorts" not just "running shorts." If the app is for men, say "men's." If unisex, say "unisex." Never leave the audience ambiguous in an image prompt, because the image model will default to the wrong gender, style, or audience. This applies to every single product image, not just the hero.
────────────────────────────────────────────────────────
DESIGN PHILOSOPHY
────────────────────────────────────────────────────────
Every app must look like a real product — not generic AI output. Before writing any code, commit to one clear aesthetic direction and execute it without compromise.

Never default to a safe middle ground. Never blend directions. Read the user's request, infer the appropriate mood, and commit fully.

FORBIDDEN regardless of direction:
- Inter or Poppins as the only font
- Purple gradients on white backgrounds
- Generic centered hero with one headline and one button
- Lorem ipsum or placeholder text anywhere
- Flat colorless designs with no visual hierarchy
- Layouts that look identical to every other AI-generated app

REQUIRED for every project:
- Import at least two Google Fonts — one display font for headings, one readable font for body
- Define all colors as CSS variables in index.css
- At least one framer-motion entrance animation on the hero or first section
- Hover states on every interactive element
- Realistic domain-appropriate content throughout
-All button's are functional and working no dead button leftover

────────────────────────────────────────────────────────
AESTHETIC DIRECTIONS
────────────────────────────────────────────────────────
Pick one direction per project and execute it with full commitment.

BRUTALLY MINIMAL
Extreme whitespace. One typeface, one weight variation. No decorative elements.
Color: near-white background, near-black text, one functional accent only.
Every element must justify its existence. If it does not carry meaning, remove it.

MAXIMALIST
Dense, layered, overwhelming in a controlled way. Competing textures, patterns, type sizes.
Color: rich, saturated, multiple hues that clash intentionally.
Nothing is bare. Every surface has something on it.

RETRO-FUTURISTIC
Combines nostalgia with technology. CRT aesthetics, chrome, phosphor glows.
Color: deep backgrounds with neon or phosphor accents — greens, magentas, ambers.
Monospace type mixed with geometric display fonts. Terminal aesthetics, scan lines, grids.

PLAYFUL
Rounded everything. Bouncy animations. Unexpected color combinations that feel joyful.
Color: bright, saturated, warm. Candy palettes or bold primaries.
Personality over polish. Surprise the user.

EDITORIAL
Inspired by print magazines. Strong typographic hierarchy. Asymmetric grid layouts.
Color: restrained — black, white, one strong ink color. Let layout do the work.
Typography is the design. Font size contrast is dramatic.

BRUTALIST
Raw, unfinished, intentionally uncomfortable. Visible structure, exposed grid.
Color: stark — black on white, or one harsh color on white. No gradients, no shadows.
Borders everywhere. Monospace or grotesque fonts. Forms that look like forms.

ART DECO
Geometric precision, symmetry, luxury. Gold, brass, marble references.
Color: deep jewel tones — navy, emerald, burgundy — with gold or champagne accents.
Ornamental but structured. Every decorative element follows a geometric rule.

ORGANIC
Natural materials, irregular shapes, imperfect textures. Nothing feels manufactured.
Color: muted earth tones — clay, sage, sand, warm stone. No pure blacks or whites.
Soft curves, grain textures. Feels grown, not built.

────────────────────────────────────────────────────────
DESIGN SYSTEM IMPLEMENTATION
────────────────────────────────────────────────────────
Always define colors as CSS variables in index.css. Never hardcode hex values in Tailwind classes or component JSX. Choose your own palette that fits the selected aesthetic direction.

Pattern to follow:

index.css:
  :root {
    --color-bg:         <your background>;
    --color-surface:    <your card or panel color>;
    --color-border:     <your border color>;
    --color-text:       <your primary text>;
    --color-muted:      <your secondary text>;
    --color-accent:     <your brand accent>;
    --color-accent-dim: <accent at low opacity>;
  }

tailwind.config.ts:
  theme: { extend: { colors: {
    bg:      'var(--color-bg)',
    surface: 'var(--color-surface)',
    accent:  'var(--color-accent)',
    muted:   'var(--color-muted)',
  }}}

In components:
  className="bg-surface text-accent"
  Never: className="bg-[#111118]" or style={{ color: '#e84040' }}

────────────────────────────────────────────────────────
WHAT YOU MUST BUILD
────────────────────────────────────────────────────────
1. Every described page, fully implemented. No stubs, no TODOs.
2. All routes working. Mobile menu if layout requires it.
3. Every layout works at 320px, 768px, and 1440px.
4. Forms submit, modals open and close, dropdowns work.
5. Realistic domain-appropriate content throughout. Zero Lorem Ipsum.
6. AI images for heroes, product shots, and cards. Never leave broken image tags.
7. Entrance animations on major sections. Hover states on all interactive elements.
8. Loading states, empty states, and error states for all data-dependent UI.

────────────────────────────────────────────────────────
IMAGE GENERATION
────────────────────────────────────────────────────────
Images are not optional. Every e-commerce store, landing page, or content site
must have real generated images. CSS placeholders are a last resort only when
generation explicitly fails — never a first choice.

MANDATORY images for every commerce project:
- One large hero image (1920x1080) — a strong editorial or lifestyle shot
- One image per product (minimum 6 products)
- One image per category section

IMPORTANT: Generate images in batches of no more than 4 at a time.
If you need 8 images, do two batches of 4.

Generate all images in parallel in a single batch before writing any page components.
Do not write Home.tsx, Shop.tsx, or ProductCard.tsx before the images exist —
the components must import real images, not reference placeholder divs.

The hero image must be large and dominant — full width, at least 500px tall.
It must show a real scene, person, or product — not a color block or gradient.
A hero section with no image is not acceptable for any commerce or lifestyle app.

Models:
- flux.schnell: product cards, thumbnails — default for most images, fastest
- flux.dev: complex shots where quality is critical, slower
- flux2.dev: hero banners — high quality, use for the main hero image only

Write detailed prompts. Include subject, audience, style, lighting, background.
Weak prompt: "women's clothing"
Strong prompt: "editorial fashion photograph of a woman in a flowing cream linen dress
walking through a sunlit European market, natural light, film grain, wide angle,
warm tones, high fashion magazine style"

CRITICAL: Every image prompt must be UNIQUE and SPECIFIC to the individual product.
Never use the same prompt template for different products — vary the subject,
color, composition, angle, and background for each one.

After generating, always import as ES6 modules:
  import heroImg from '../assets/hero.jpg'
  <img src={heroImg} />

Never use string paths in JSX — they always produce broken images in Vite.
If generate_image returns IMAGE_GENERATION_FAILED, do NOT import that path.
Instead use a CSS gradient — never a flat color block with just text on it.
────────────────────────────────────────────────────────
BROKEN IMAGE PREVENTION
────────────────────────────────────────────────────────
Before finishing, verify every image in the project:
- Was generate_image called and confirmed successful (returned IMAGE_GENERATED, not IMAGE_GENERATION_FAILED)?
- Is it imported as an ES6 module at the top of the file that uses it?
- Is the import path correct relative to the file?
- Is the imported variable used in JSX, not the string path?

If any check fails — replace with a CSS gradient placeholder immediately.
Do NOT import a path that returned IMAGE_GENERATION_FAILED.

────────────────────────────────────────────────────────
CODE QUALITY
────────────────────────────────────────────────────────
1. Every file complete and immediately runnable.
2. One primary component per file, named to match the filename.
3. All imports resolve. If you create a file, ensure it exports correctly.
4. No unused imports. No console.log in production code. No commented-out dead code.
5. Full TypeScript with proper types. Avoid any unless genuinely unavoidable.
6. Components over 300 lines should be split into focused subcomponents.

────────────────────────────────────────────────────────
FILE ORGANIZATION
────────────────────────────────────────────────────────
src/pages/      — page components, one per route
src/components/ — reusable UI components
src/hooks/      — custom React hooks
src/utils/      — utility functions
src/lib/        — library configs such as supabase client
src/contexts/   — React context providers
src/assets/     — images and static assets

────────────────────────────────────────────────────────
TOOL USAGE
────────────────────────────────────────────────────────
files_list           — call at startup, not needed again unless unsure what exists
read_file            — read before modifying, never edit a file you have not seen
write_file           — new files or complete rewrites only, always write full content
edit_file            — default for changes to existing files, old_str must be exact
read_package_json    — always call before run_install_command
read_console_logs    — only call when the user explicitly reports something is broken, do not call proactively
generate_image       — call in parallel, MAX 4 at a time. If you need more, do multiple batches.
request_backend      — call before any auth or database code
request_stripe       — call before any payment or checkout code
request_ai           — call before any AI or chatbot code

────────────────────────────────────────────────────────
SELF-CHECK BEFORE FINISHING
────────────────────────────────────────────────────────
Before writing your summary, verify:
- Every described page exists and is fully implemented
- Every route in App.tsx points to a component that exists
- Every import resolves to a real file
- index.css starts with the three Tailwind directives
- No TODOs, stubs, or placeholder content anywhere
- All images that returned IMAGE_GENERATED are imported as ES6 modules
- All images that returned IMAGE_GENERATION_FAILED are NOT imported — use CSS gradients instead
- Animations and hover states are present
- The design has a clear committed aesthetic direction
- The summary contains no instructions for the user, no emojis, no bullet points
"""


# ══════════════════════════════════════════════════════════════════════════════
#  ANTHROPIC TOOL DEFINITIONS (base tools — always available)
# ══════════════════════════════════════════════════════════════════════════════

REQUEST_BACKEND_TOOL = {
    "name": "request_backend",
    "description": (
        "Request a backend (database + authentication) for this project. "
        "Call this when the user's app needs: user accounts, login/signup, "
        "data persistence, database tables, or any server-side functionality.\n\n"
        "This will ask the user for permission to enable a Supabase backend. "
        "IMPORTANT: Call this EARLY in your startup sequence — before writing any auth or database code. "
        "If approved, you'll get access to Supabase tools (create_table, add_rls_policy, etc.). "
        "If denied, build a frontend-only version using localStorage."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Brief explanation of why the app needs a backend."
            }
        },
        "required": ["reason"]
    }
}

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
        "description": "Create, write or overwrite a file. Use for new files or complete rewrites. Always write the full file content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string"},
                "content": {"type": "string", "description": "The full source code"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "edit_file",
        "description": (
            "Surgically edit an existing file by replacing a specific string with new content. "
            "This is the DEFAULT tool for modifying existing files — prefer it over write_file. "
            "old_str must be an exact match of content in the file. "
            "To append, provide the last line of the file as old_str and add your new content after it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string"},
                "old_str": {"type": "string", "description": "The exact block of text to replace. Must exist in the file."},
                "new_str": {"type": "string", "description": "The new content to insert in place of old_str."}
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
                "new_path":      {"type": "string"}
            },
            "required": ["original_path", "new_path"]
        }
    },
    {
        "name": "search_files",
        "description": (
            "Regex-based code search across project files. "
            "Use to find where components, functions, or patterns are used before renaming or refactoring. "
            "Much more efficient than reading every file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":            {"type": "string", "description": "Regex pattern to search for."},
                "search_dir":       {"type": "string", "description": "Directory to search in. Defaults to 'src'."},
                "include_patterns": {"type": "string", "description": "Comma-separated glob patterns for files to include."},
                "case_sensitive":   {"type": "boolean", "description": "Case-sensitive search. Defaults to false."}
            },
            "required": ["query"]
        }
    },
    {
        "name": "files_list",
        "description": "Get the list of all current project files. Call at startup and when unsure what files exist.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "run_install_command",
        "description": (
            "Run a terminal command to install dependencies. "
            "ALWAYS call read_package_json first to check if the package is already installed. "
            "Always include the -y flag: e.g. 'npm install framer-motion -y'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command":   {"type": "string", "description": "The full terminal command (e.g., 'npm install framer-motion -y')."},
                "directory": {"type": "string", "description": "Relative path from project root where the command should run."}
            },
            "required": ["command"]
        }
    },
    {
        "name": "generate_image",
        "description": (
            "Generate an AI image and save it to the specified path.\n\n"
            "Models:\n"
            "- flux.schnell: fastest, good for cards/thumbnails/avatars (<1000px). Default.\n"
            "- flux2.dev: high quality, only supports 1024x1024 and 1920x1080. Use for hero banners.\n"
            "- flux.dev: highest quality, any resolution, slower. Use for complex product shots.\n\n"
            "Max resolution: 1920x1920. Dimensions must be multiples of 32, min 512.\n"
            "Write detailed prompts — style, mood, lighting, content, photography style.\n"
            "IMPORTANT: Generate max 4 images at a time. If you need more, do multiple batches.\n"
            "If this returns IMAGE_GENERATION_FAILED, do NOT import that path — use a CSS gradient instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt":      {"type": "string", "description": "Detailed description of the image to generate. Must be unique per image."},
                "target_path": {"type": "string", "description": "File path where the image will be saved (prefer src/assets/)."},
                "width":       {"type": "number", "description": "Image width (min 512, max 1920, multiple of 32). Defaults to 1024."},
                "height":      {"type": "number", "description": "Image height (min 512, max 1920, multiple of 32). Defaults to 768."},
                "model":       {"type": "string", "description": "flux.schnell | flux.dev | flux2.dev"}
            },
            "required": ["prompt", "target_path"]
        }
    },
    {
        "name": "edit_image",
        "description": "Edit or merge existing images based on a text prompt. Aspect ratio options: 1:1, 2:3, 3:2, 3:4, 4:3, 9:16, 16:9, 21:9.",
        "input_schema": {
            "type": "object",
            "properties": {
                "image_paths": {"type": "array", "items": {"type": "string"}, "description": "Paths to source images."},
                "prompt":      {"type": "string", "description": "Description of the edit to apply."},
                "target_path": {"type": "string", "description": "Where to save the edited image."},
                "aspect_ratio":{"type": "string", "description": "Output aspect ratio. Defaults to source ratio."}
            },
            "required": ["image_paths", "prompt", "target_path"]
        }
    },
    {
    "name": "read_console_logs",
    "description": (
        "Read runtime console errors from the previewed app.\n\n"
        "ONLY call this when the user's message explicitly says the app is broken, "
        "blank, crashing, or not working.\n\n"
        "NEVER call this proactively, speculatively, or as part of a build sequence.\n"
        "NEVER call this before the build is complete.\n"
        "NEVER call this to verify your own work.\n\n"
        "Calling this at any other time wastes credits and will produce stale or irrelevant results."
    ),
    "input_schema": {"type": "object", "properties": {}}
    },  
    {
        "name": "read_package_json",
        "description": (
            "Read the current package.json to check what dependencies are already installed.\n\n"
            "ALWAYS call this before run_install_command to avoid redundant installs.\n"
            "Returns the full package.json content including dependencies and devDependencies."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    REQUEST_BACKEND_TOOL,
    {
        "name": "request_stripe",
        "description": (
            "Request Stripe payment integration for this project. "
            "Call this when the user wants: payments, checkout, subscriptions, pricing pages, "
            "buy buttons, or any e-commerce functionality.\n\n"
            "IMPORTANT: Call this EARLY — before writing any Stripe code. "
            "If approved, you'll get the publishable key and backend proxy URLs. "
            "If denied, build a UI-only mockup with 'Coming soon' buttons."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief explanation of what payment feature is needed."
                }
            },
            "required": ["reason"]
        }
    },
    {
        "name": "request_ai",
        "description": (
            "Request AI/Claude integration for this project. "
            "Call this when the user wants: a chatbot, AI responses, text generation, "
            "summarization, translation, content generation, or any AI-powered feature.\n\n"
            "IMPORTANT: Call this EARLY — before writing any AI code. "
            "If approved, you'll get a proxy URL and app token to call Claude API safely. "
            "If not configured, build a UI mockup with placeholder AI responses."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief explanation of what AI feature is needed."
                }
            },
            "required": ["reason"]
        }
    },
]


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
        print(f"[Agent5] Supabase enabled — added backend tools and prompt")
    if stripe_config:
        prompt_with_keys = STRIPE_PROMPT_ADDITION.replace(
            "{STRIPE_PUBLISHABLE_KEY}", stripe_config.get("publishable_key", "")
        ).replace(
            "{STRIPE_PROXY_URL}", stripe_config.get("proxy_url", "")
        )
        system_prompt += prompt_with_keys
        print(f"[Agent5] Stripe enabled — added payment tools and prompt")
    if ai_config:
        prompt_with_url = AI_PROMPT_ADDITION.replace(
            "{AI_PROXY_URL}", ai_config.get("proxy_url", "")
        ).replace(
            "{APP_TOKEN}", ai_config.get("app_token", "")
        )
        system_prompt += prompt_with_url
        print(f"[Agent5] AI proxy enabled — added AI tools and prompt")

    all_tools = list(anthropic_tools)
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
        print(f"""{Back.WHITE}agent6 is taking action: "type": "FILE_WRITE", "path": {path}, "existed": {existed}{Style.RESET_ALL}""")
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
        print(f"""{Back.WHITE}agent6 is taking action: "type": "EDIT", "path": {path}{Style.RESET_ALL}""")
        agent6.notify_reviewer({"type": "FILE_WRITE", "path": path, "old_string": old_str, "new_string": new_str, "new_content": updated_content})
        return f"EDIT_COMPLETED PATH: {path}"

    def read_file(path, **kwargs):
        print(f"THE GENERATOR REQUESTED A READ FOR:{path}")
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
                print(f"[delete] Removed directory: {path}")
            else:
                os.remove(path)
                remove_file(path)
                print(f"[delete] Removed file: {path}")
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
            cmd = ["grep", "-r", "-n", "--include=*.ts", "--include=*.tsx", "--include=*.js", "--include=*.jsx", "--include=*.css", "--include=*.html", "--include=*.json", "--include=*.md"]
            if include_patterns:
                cmd = ["grep", "-r", "-n"]
                for pattern in include_patterns.split(","):
                    pattern = pattern.strip()
                    if pattern:
                        cmd.append(f"--include={pattern}")
            if not case_sensitive:
                cmd.append("-i")
            cmd.extend(["--exclude-dir=node_modules", "--exclude-dir=dist", "--exclude-dir=.git", "--exclude-dir=__pycache__"])
            cmd.append(query)
            cmd.append(search_dir if os.path.isdir(search_dir) else ".")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            output = result.stdout.strip()
            if not output:
                return f"SEARCH_NO_RESULTS: No matches found for '{query}' in {search_dir}"
            lines = output.split("\n")
            if len(lines) > 50:
                output = "\n".join(lines[:50]) + f"\n... and {len(lines) - 50} more matches"
            return output
        except subprocess.TimeoutExpired:
            return "SEARCH_ERROR: Search timed out"
        except Exception as e:
            return f"SEARCH_ERROR: {str(e)}"

    # ══════════════════════════════════════════════════════════════════════
    #  FIXED: generate_image with retry, backoff, and validation
    # ══════════════════════════════════════════════════════════════════════
    def generate_image(prompt: str, target_path: str, width: int = 1024, height: int = 768, model: str = "flux.schnell") -> str:
        try:
            print(f"[image_gen] Generating: {target_path} ({width}x{height}, {model})")
            width  = max(512, min(1920, int(width)))
            height = max(512, min(1920, int(height)))
            width  = (width  // 32) * 32
            height = (height // 32) * 32

            parent = os.path.dirname(target_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            model_map = {
                "flux.schnell": "black-forest-labs/flux-schnell",
                "flux.dev":     "black-forest-labs/flux-dev",
                "flux2.dev":    "black-forest-labs/flux-2-pro",
            }
            replicate_model = model_map.get(model, model_map["flux.schnell"])

            ext           = target_path.rsplit(".", 1)[-1].lower() if "." in target_path else "webp"
            format_map    = {"jpg": "jpg", "jpeg": "jpg", "png": "png", "webp": "webp"}
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

            # ── Retry with exponential backoff for rate limits ────────
            max_retries = 3
            output = None
            for attempt in range(max_retries):
                try:
                    output = replicate.run(replicate_model, input=replicate_input)
                    break
                except Exception as e:
                    err_str = str(e).lower()
                    is_rate_limit = "429" in err_str or "rate limit" in err_str or "throttl" in err_str
                    if is_rate_limit and attempt < max_retries - 1:
                        delay = 5 * (2 ** attempt)  # 5s, 10s, 20s
                        print(f"[image_gen] Rate limited on attempt {attempt+1}, retrying in {delay}s...")
                        time.sleep(delay)
                        continue
                    else:
                        print(f"[image_gen] Failed after {attempt+1} attempts: {e}")
                        return f"IMAGE_GENERATION_FAILED: {str(e)[:200]} — use a CSS gradient placeholder instead. Do NOT import this path."

            # ── Extract output — handle both URL and raw bytes ────────
            raw_item = None
            if isinstance(output, list) and len(output) > 0:
                raw_item = output[0]
            elif hasattr(output, '__iter__'):
                for item in output:
                    raw_item = item
                    break
            print(f"[image_gen] output type: {type(output)}, raw_item type: {type(raw_item)}")
            if raw_item is not None:
                print(f"[image_gen] raw_item repr (first 200): {repr(str(raw_item)[:200])}")

            if raw_item is None:
                return "IMAGE_GENERATION_FAILED: No output received from model — use a CSS gradient placeholder instead. Do NOT import this path."

            # Check if the output is already raw bytes (some models return bytes directly)
            if isinstance(raw_item, bytes):
                print(f"[image_gen] Got raw bytes directly ({len(raw_item)} bytes)")
                print(f"[image_gen] First 200 bytes repr: {repr(raw_item[:200])}")
                try:
                    print(f"[image_gen] Decoded as text: {raw_item.decode('utf-8', errors='replace')[:500]}")
                except Exception:
                    pass
                image_data = raw_item
            elif isinstance(raw_item, str) and raw_item.startswith(("http://", "https://")):
                # It's a URL — download it
                image_data = None
                for dl_attempt in range(2):
                    try:
                        response = requests.get(raw_item, timeout=60)
                        if response.status_code == 200:
                            image_data = response.content
                            break
                        else:
                            print(f"[image_gen] Download attempt {dl_attempt+1} failed: status {response.status_code}")
                    except Exception as dl_err:
                        print(f"[image_gen] Download attempt {dl_attempt+1} error: {dl_err}")
                    if dl_attempt < 1:
                        time.sleep(2)
            elif hasattr(raw_item, 'read'):
                # It's a file-like object (some replicate versions return this)
                print(f"[image_gen] Got file-like object, reading bytes")
                image_data = raw_item.read()
            else:
                # Unknown type — try converting to string and downloading as URL
                url_str = str(raw_item)
                if url_str.startswith(("http://", "https://")):
                    try:
                        response = requests.get(url_str, timeout=60)
                        image_data = response.content if response.status_code == 200 else None
                    except Exception as e:
                        print(f"[image_gen] Fallback download failed: {e}")
                        image_data = None
                else:
                    print(f"[image_gen] Unknown output type: {type(raw_item)}")
                    image_data = None

            if not image_data:
                return f"IMAGE_GENERATION_FAILED: Download failed after retries — use a CSS gradient placeholder instead. Do NOT import this path."

            # ── Verify file is a real image (not an error page) ───────
            if len(image_data) < 1024:
                print(f"[image_gen] WARNING: Downloaded file suspiciously small ({len(image_data)} bytes)")
                return "IMAGE_GENERATION_FAILED: Generated file too small, likely an error — use a CSS gradient placeholder instead. Do NOT import this path."

            with open(target_path, "wb") as f:
                f.write(image_data)

            file_size_kb = len(image_data) / 1024
            print(f"[image_gen] Saved: {target_path} ({file_size_kb:.1f} KB)")

            add_file(target_path)
            agent6.notify_reviewer({"type": "IMAGE_GENERATED", "path": target_path, "prompt": prompt})

            if target_path.startswith("src/"):
                return f"IMAGE_GENERATED PATH:{target_path} SIZE:{file_size_kb:.1f}KB — Import as ES6 module: import img from './{target_path}'"
            else:
                public_ref = target_path.replace("public/", "/", 1) if target_path.startswith("public/") else f"/{target_path}"
                return f"IMAGE_GENERATED PATH:{target_path} SIZE:{file_size_kb:.1f}KB — Reference in code as {public_ref}"

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
                    uri  = f"data:{mime};base64,{base64.b64encode(data).decode()}"
                    image_uris.append(uri)
                else:
                    return f"IMAGE_EDIT_FAILED: Source image not found: {img_path}"
            if not image_uris:
                return "IMAGE_EDIT_FAILED: No valid source images provided"
            replicate_input = {"prompt": prompt, "input_image": image_uris[0], "aspect_ratio": aspect_ratio, "output_format": "webp", "output_quality": 90}
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
            add_file(target_path)
            agent6.notify_reviewer({"type": "IMAGE_EDITED", "source_paths": image_paths, "target_path": target_path})
            if target_path.startswith("src/"):
                return f"IMAGE_EDITED PATH:{target_path} — Import as ES6 module: import img from './{target_path}'"
            else:
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
                return "CONSOLE_LOGS_EMPTY: No errors or warnings captured — app appears clean."
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
            return "PACKAGE_JSON_NOT_FOUND: No package.json exists yet in this project."
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
                    return "BACKEND_ALREADY_ENABLED: Supabase is already active. Use get_supabase_config to get credentials."
            except Exception:
                pass
        print(f"[Agent5] Backend requested — reason: {reason}")
        req_path = os.path.join(_workspace, "backend_requested.json")
        with open(req_path, "w") as f:
            json.dump({"reason": reason, "ts": _time.time()}, f)
        approved_path = os.path.join(_workspace, "backend_approved.json")
        denied_path   = os.path.join(_workspace, "backend_denied.json")
        max_wait      = 300
        elapsed       = 0
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
                        supabase_url      = supabase_url,
                        anon_key          = anon_key,
                        service_role_key  = meta.get("supabase_service_role", ""),
                        preview_url       = f"https://entrepreneur-bot-backend.onrender.com/auth/preview-raw/{os.path.basename(_workspace)}/",
                        project_ref       = meta.get("supabase_project_ref", ""),
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
                    print(f"[Agent5] Backend approved — Supabase tools activated")
                return (
                    f"BACKEND_APPROVED: Supabase is now active!\n"
                    f"URL: {supabase_url}\nAnon Key: {anon_key}\n\n"
                    f"You now have access to: create_table, add_rls_policy, enable_auth, list_tables, run_sql, get_supabase_config.\n\n"
                    f"NEXT STEPS:\n"
                    f"1. Call get_supabase_config to get the client setup code\n"
                    f"2. Create src/lib/supabase.ts with the client\n"
                    f"3. Install @supabase/supabase-js\n"
                    f"4. Create tables and RLS policies as needed"
                )
            if os.path.exists(denied_path):
                try: os.remove(denied_path)
                except: pass
                try: os.remove(req_path)
                except: pass
                return "BACKEND_DENIED: User declined the backend. Build a frontend-only version using localStorage for data persistence. Do NOT use any Supabase tools or imports."
        try: os.remove(req_path)
        except: pass
        return "BACKEND_TIMEOUT: No response from user within 5 minutes. Build a frontend-only version using localStorage for data persistence."

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
                    return (
                        f"STRIPE_ALREADY_ENABLED: Stripe is active.\n"
                        f"Publishable key: {pk}\n"
                        f"Proxy URL: https://entrepreneur-bot-backend.onrender.com/stripe/job/{job}"
                    )
            except Exception:
                pass
        print(f"[Agent5] Stripe requested — reason: {reason}")
        req_path = os.path.join(_workspace, "stripe_requested.json")
        with open(req_path, "w") as f:
            json.dump({"reason": reason, "ts": _time.time()}, f)
        approved_path = os.path.join(_workspace, "stripe_approved.json")
        denied_path   = os.path.join(_workspace, "stripe_denied.json")
        max_wait      = 300
        elapsed       = 0
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
                    prompt_with_keys = STRIPE_PROMPT_ADDITION.replace(
                        "{STRIPE_PUBLISHABLE_KEY}", pk
                    ).replace(
                        "{STRIPE_PROXY_URL}", proxy_url
                    )
                    agent6.system_prompt += prompt_with_keys
                return (
                    f"STRIPE_APPROVED: Stripe is now active!\n"
                    f"Publishable key: {pk}\n"
                    f"Proxy URL: {proxy_url}\n\n"
                    f"NEXT STEPS:\n"
                    f"1. Install: npm install @stripe/stripe-js @stripe/react-stripe-js -y\n"
                    f"2. Use create-checkout-session proxy for payments (never put sk_ in frontend)\n"
                    f"3. Use publishable key only for Stripe.js initialization"
                )
            if os.path.exists(denied_path):
                try: os.remove(denied_path)
                except: pass
                try: os.remove(req_path)
                except: pass
                return "STRIPE_DENIED: User declined Stripe. Build a payment UI mockup without real processing. Show realistic checkout forms but make buttons display a 'Coming soon' message."
        try: os.remove(req_path)
        except: pass
        return "STRIPE_TIMEOUT: No response within 5 minutes. Build a payment UI mockup without real Stripe integration."

    def request_ai(reason: str = "") -> str:
        _workspace = workspace
        if not _workspace:
            return "AI_ERROR: No workspace configured"
        proxy_url = "https://entrepreneur-bot-backend.onrender.com/auth/ai/proxy"
        app_token = ai_config.get("app_token", "") if ai_config else ""
        if "AI / CLAUDE INTEGRATION" not in agent6.system_prompt:
            prompt_with_url = AI_PROMPT_ADDITION.replace(
                "{AI_PROXY_URL}", proxy_url
            ).replace(
                "{APP_TOKEN}", app_token
            )
            agent6.system_prompt += prompt_with_url
        return (
            f"AI_APPROVED: Claude AI proxy is ready.\n"
            f"Proxy URL: {proxy_url}\n"
            f"App Token: {app_token}\n\n"
            f"IMPORTANT: Use APP_TOKEN hardcoded in useAI.ts — safe to embed, scoped to AI calls only.\n"
            f"Credits are charged to the app owner's account automatically.\n\n"
            f"NEXT STEPS:\n"
            f"1. Create src/hooks/useAI.ts with the hook from the AI integration guide\n"
            f"2. Import and use useAI() in your components"
        )

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
            supabase_url      = supabase_config["url"],
            anon_key          = supabase_config["anon_key"],
            service_role_key  = supabase_config.get("service_role_key", ""),
            preview_url       = supabase_config.get("preview_url", ""),
            project_ref       = supabase_config.get("project_ref", ""),
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