"""
supabase_module.py — Drop-in replacement for all Supabase logic in Agent5.py

This module provides:
  1. SupabaseTools class — all backend operations
  2. SUPABASE_TOOL_DEFINITIONS — tool schemas for the AI agent
  3. SUPABASE_PROMPT_ADDITION — comprehensive prompt injection
  4. inject_scaffold() — creates client.ts, types.ts, .env in workspace
  5. register_supabase_tools() — wires everything into an agent instance
"""

import os
import json
import re
import requests
import time


# ══════════════════════════════════════════════════════════════════════════════
#  POSTGRES → TYPESCRIPT TYPE MAP
# ══════════════════════════════════════════════════════════════════════════════

_PG_TO_TS = {
    "uuid": "string",
    "text": "string",
    "varchar": "string",
    "character varying": "string",
    "char": "string",
    "character": "string",
    "citext": "string",
    "name": "string",
    "bytea": "string",
    "inet": "string",
    "cidr": "string",
    "macaddr": "string",
    "macaddr8": "string",
    "ltree": "string",
    "tsvector": "string",
    "tsquery": "string",
    "xml": "string",
    "int2": "number",
    "int4": "number",
    "int8": "number",
    "integer": "number",
    "smallint": "number",
    "bigint": "number",
    "float4": "number",
    "float8": "number",
    "numeric": "number",
    "decimal": "number",
    "real": "number",
    "double precision": "number",
    "money": "number",
    "oid": "number",
    "serial": "number",
    "bigserial": "number",
    "smallserial": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "json": "Json",
    "jsonb": "Json",
    "timestamp": "string",
    "timestamptz": "string",
    "timestamp with time zone": "string",
    "timestamp without time zone": "string",
    "date": "string",
    "time": "string",
    "time with time zone": "string",
    "time without time zone": "string",
    "timetz": "string",
    "interval": "string",
    "point": "unknown",
    "line": "unknown",
    "lseg": "unknown",
    "box": "unknown",
    "path": "unknown",
    "polygon": "unknown",
    "circle": "unknown",
}


def _pg_type_to_ts(pg_type: str, udt_name: str = "", enums: dict = None) -> str:
    """Convert a PostgreSQL type to a TypeScript type string."""
    enums = enums or {}

    # Check if it's a user-defined enum
    if udt_name in enums:
        return f"Database['public']['Enums']['{udt_name}']"

    # Array types (udt_name starts with underscore)
    if udt_name.startswith("_"):
        inner = udt_name[1:]
        inner_ts = _pg_type_to_ts(inner, inner, enums)
        return f"{inner_ts}[]"

    if pg_type == "ARRAY":
        return "any[]"
    if pg_type == "USER-DEFINED" and udt_name in enums:
        return f"Database['public']['Enums']['{udt_name}']"
    if pg_type == "USER-DEFINED":
        return "string"

    return _PG_TO_TS.get(pg_type, _PG_TO_TS.get(udt_name, "string"))


# ══════════════════════════════════════════════════════════════════════════════
#  SUPABASE TOOLS CLASS
# ══════════════════════════════════════════════════════════════════════════════

class SupabaseTools:
    """
    All Supabase backend operations for the AI agent.
    Designed to match Lovable's tool capabilities.
    """

    def __init__(
        self,
        supabase_url: str,
        anon_key: str,
        service_role_key: str,
        project_ref: str,
        workspace: str,
        preview_url: str = "",
    ):
        self.url = supabase_url.rstrip("/")
        self.anon_key = anon_key
        self.service_role_key = service_role_key
        self.project_ref = project_ref
        self.workspace = workspace
        self.preview_url = preview_url

    # ── Internal SQL execution via Management API ─────────────────────

    def _execute_sql(self, sql: str) -> dict:
        access_token = os.getenv("SUPABASE_ACCESS_TOKEN", "")
        if not access_token or not self.project_ref:
            return {"success": False, "error": "SUPABASE_ACCESS_TOKEN or project_ref not configured"}
        try:
            resp = requests.post(
                f"https://api.supabase.com/v1/projects/{self.project_ref}/database/query",
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
                return {"success": False, "error": resp.text[:800]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _rest_headers(self):
        return {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
        }

    # ══════════════════════════════════════════════════════════════════
    #  TOOL: migration
    # ══════════════════════════════════════════════════════════════════

    def migration(self, sql: str) -> str:
        """
        Execute schema changes: CREATE TABLE, ALTER TABLE, CREATE POLICY,
        CREATE FUNCTION, CREATE TRIGGER, storage bucket creation, etc.

        After success, automatically regenerates TypeScript types.
        Returns warnings about missing RLS if detected.
        """
        sql = sql.strip()
        if not sql:
            return "MIGRATION_ERROR: Empty SQL"

        # Block destructive operations
        sql_lower = sql.lower()
        blocked = ["drop database", "drop schema public", "pg_terminate_backend",
                    "drop owned", "reassign owned", "truncate"]
        for b in blocked:
            if b in sql_lower:
                return f"MIGRATION_BLOCKED: '{b}' is not allowed."

        # Block pure data operations — those belong in insert_data
        first_word = sql_lower.lstrip().split()[0] if sql_lower.strip() else ""
        if first_word in ("insert", "update", "delete") and "create" not in sql_lower and "alter" not in sql_lower:
            return "MIGRATION_ERROR: Use the insert_data tool for INSERT/UPDATE/DELETE operations. The migration tool is for schema changes only."

        result = self._execute_sql(sql)
        if not result["success"]:
            return f"MIGRATION_ERROR: {result['error']}"

        print(f"[supabase] Migration executed successfully")

        # Check for security warnings
        warnings = self._check_security_warnings()

        # Regenerate TypeScript types
        types_result = self._regenerate_types()

        output = "MIGRATION_SUCCESS: Schema changes applied."
        if types_result:
            output += f"\n{types_result}"
        if warnings:
            output += f"\n\nSECURITY_WARNINGS:\n{warnings}"
            output += "\nCRITICAL: Fix any warnings related to this migration before writing frontend code."

        return output

    # ══════════════════════════════════════════════════════════════════
    #  TOOL: insert_data
    # ══════════════════════════════════════════════════════════════════

    def insert_data(self, sql: str) -> str:
        """
        Execute data changes: INSERT, UPDATE, DELETE.
        Cannot modify schema. Does not use auth.uid() — no user context.
        """
        sql = sql.strip()
        if not sql:
            return "INSERT_ERROR: Empty SQL"

        sql_lower = sql.lower()

        # Block schema changes
        schema_keywords = ["create table", "alter table", "drop table",
                           "create policy", "create function", "create trigger",
                           "create index", "create type", "create extension",
                           "drop policy", "drop function", "drop trigger"]
        for kw in schema_keywords:
            if kw in sql_lower:
                return f"INSERT_ERROR: '{kw}' is a schema change. Use the migration tool instead."

        blocked = ["drop database", "drop schema", "pg_terminate_backend"]
        for b in blocked:
            if b in sql_lower:
                return f"INSERT_BLOCKED: '{b}' is not allowed."

        result = self._execute_sql(sql)
        if result["success"]:
            data_preview = json.dumps(result["data"], indent=2)[:1500] if result["data"] else "No rows returned"
            return f"DATA_OPERATION_SUCCESS\n{data_preview}"
        return f"INSERT_ERROR: {result['error']}"

    # ══════════════════════════════════════════════════════════════════
    #  TOOL: read_query
    # ══════════════════════════════════════════════════════════════════

    def read_query(self, sql: str) -> str:
        """
        Execute SELECT queries for debugging and data inspection.
        Useful when users report issues — check the actual data.
        """
        sql = sql.strip()
        if not sql:
            return "QUERY_ERROR: Empty SQL"

        sql_lower = sql.lower().strip()
        if not sql_lower.startswith("select") and not sql_lower.startswith("with"):
            return "QUERY_ERROR: Only SELECT statements are allowed. Use insert_data for writes or migration for schema changes."

        result = self._execute_sql(sql)
        if result["success"]:
            data = result["data"]
            if not data:
                return "QUERY_RESULT: No rows returned."
            preview = json.dumps(data, indent=2)[:3000]
            return f"QUERY_RESULT ({len(data)} rows):\n{preview}"
        return f"QUERY_ERROR: {result['error']}"

    # ══════════════════════════════════════════════════════════════════
    #  TOOL: list_tables
    # ══════════════════════════════════════════════════════════════════

    def list_tables(self) -> str:
        """List all tables with columns, types, constraints, and RLS status."""
        sql = """
            SELECT
                t.table_name,
                json_agg(json_build_object(
                    'column_name', c.column_name,
                    'data_type', c.data_type,
                    'udt_name', c.udt_name,
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
        if not result["success"]:
            return f"LIST_TABLES_ERROR: {result['error']}"

        data = result["data"]
        if not data:
            return "NO_TABLES: The database has no tables yet."

        # Also check RLS status
        rls_sql = """
            SELECT tablename, rowsecurity
            FROM pg_tables
            WHERE schemaname = 'public';
        """
        rls_result = self._execute_sql(rls_sql)
        rls_map = {}
        if rls_result["success"]:
            for row in rls_result["data"]:
                rls_map[row["tablename"]] = row.get("rowsecurity", False)

        # Check policies
        policy_sql = """
            SELECT tablename, policyname, permissive, roles, cmd, qual, with_check
            FROM pg_policies
            WHERE schemaname = 'public'
            ORDER BY tablename, policyname;
        """
        policy_result = self._execute_sql(policy_sql)
        policy_map = {}
        if policy_result["success"]:
            for row in policy_result["data"]:
                tbl = row["tablename"]
                if tbl not in policy_map:
                    policy_map[tbl] = []
                policy_map[tbl].append(row)

        output = "DATABASE_SCHEMA:\n"
        for table in data:
            name = table.get("table_name", "unknown")
            cols = table.get("columns", [])
            rls_on = rls_map.get(name, False)
            policies = policy_map.get(name, [])

            rls_status = "RLS ON" if rls_on else "RLS OFF (WARNING: table is publicly accessible!)"
            output += f"\n  {name} [{rls_status}]:\n"
            output += f"    Columns:\n"
            for col in cols:
                nullable = "nullable" if col.get("is_nullable") == "YES" else "not null"
                default = f" default={col['column_default']}" if col.get("column_default") else ""
                output += f"      - {col['column_name']}: {col['data_type']} ({nullable}{default})\n"

            if policies:
                output += f"    Policies:\n"
                for p in policies:
                    output += f"      - {p['policyname']} ({p['cmd']}): USING={p.get('qual', 'n/a')}\n"
            elif rls_on:
                output += f"    Policies: NONE (WARNING: RLS is on but no policies exist — table is inaccessible!)\n"

        return output

    # ══════════════════════════════════════════════════════════════════
    #  TOOL: create_storage_bucket
    # ══════════════════════════════════════════════════════════════════

    def create_storage_bucket(self, bucket_name: str, public: bool = True) -> str:
        """
        Create a storage bucket and set up default policies.
        Idempotent — safe to call multiple times.
        """
        # Step 1: Create the bucket (ON CONFLICT handles re-runs)
        bucket_sql = f"INSERT INTO storage.buckets (id, name, public) VALUES ('{bucket_name}', '{bucket_name}', {str(public).lower()}) ON CONFLICT (id) DO NOTHING;"
        result = self._execute_sql(bucket_sql)
        if not result["success"]:
            return f"STORAGE_ERROR: Could not create bucket: {result['error']}"

        # Step 2: Create policies wrapped in exception handlers (idempotent)
        if public:
            policies = [
                (f"Public read {bucket_name}", "SELECT", "public",
                 f"bucket_id = '{bucket_name}'", None),
                (f"Authenticated upload {bucket_name}", "INSERT", "authenticated",
                 None, f"bucket_id = '{bucket_name}'"),
                (f"Owner update {bucket_name}", "UPDATE", "authenticated",
                 f"bucket_id = '{bucket_name}' AND auth.uid()::text = (storage.foldername(name))[1]", None),
                (f"Owner delete {bucket_name}", "DELETE", "authenticated",
                 f"bucket_id = '{bucket_name}' AND auth.uid()::text = (storage.foldername(name))[1]", None),
            ]
        else:
            policies = [
                (f"Owner read {bucket_name}", "SELECT", "authenticated",
                 f"bucket_id = '{bucket_name}' AND auth.uid()::text = (storage.foldername(name))[1]", None),
                (f"Owner upload {bucket_name}", "INSERT", "authenticated",
                 None, f"bucket_id = '{bucket_name}' AND auth.uid()::text = (storage.foldername(name))[1]"),
                (f"Owner update {bucket_name}", "UPDATE", "authenticated",
                 f"bucket_id = '{bucket_name}' AND auth.uid()::text = (storage.foldername(name))[1]", None),
                (f"Owner delete {bucket_name}", "DELETE", "authenticated",
                 f"bucket_id = '{bucket_name}' AND auth.uid()::text = (storage.foldername(name))[1]", None),
            ]

        for policy_name, cmd, role, using_expr, check_expr in policies:
            using_clause = f"USING ({using_expr})" if using_expr else ""
            check_clause = f"WITH CHECK ({check_expr})" if check_expr else ""
            policy_sql = f"""
                DO $$ BEGIN
                    CREATE POLICY "{policy_name}" ON storage.objects
                        FOR {cmd} TO {role}
                        {using_clause} {check_clause};
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$;
            """
            res = self._execute_sql(policy_sql)
            if not res["success"]:
                print(f"[storage] Policy '{policy_name}' warning: {res['error'][:200]}")

        public_url = f"{self.url}/storage/v1/object/public/{bucket_name}"
        upload_pattern = (
            f"const {{ data, error }} = await supabase.storage\n"
            f"  .from('{bucket_name}')\n"
            f"  .upload(`${{userId}}/${{fileName}}`, file);\n\n"
            f"// Get public URL:\n"
            f"const {{ data: urlData }} = supabase.storage\n"
            f"  .from('{bucket_name}')\n"
            f"  .getPublicUrl(filePath);"
        )
        return (
            f"STORAGE_BUCKET_CREATED: '{bucket_name}' ({'public' if public else 'private'})\n"
            f"Public base URL: {public_url}\n\n"
            f"Upload pattern:\n{upload_pattern}"
        )
    # ══════════════════════════════════════════════════════════════════
    #  TOOL: upload_to_storage
    # ══════════════════════════════════════════════════════════════════

    def upload_to_storage(self, bucket_name: str, local_path: str, storage_path: str = "") -> str:
        """
        Upload a local file to a Supabase Storage bucket.
        Returns the permanent public URL.
        Used after generate_image for database-referenced images.
        """
        # Resolve path relative to workspace if not absolute
        if not os.path.isabs(local_path) and self.workspace:
            resolved = os.path.join(self.workspace, local_path)
            if os.path.isfile(resolved):
                local_path = resolved

        if not os.path.isfile(local_path):
            return f"UPLOAD_ERROR: File not found: {local_path}"

        if not self.service_role_key:
            return "UPLOAD_ERROR: service_role_key not configured — cannot upload to storage."

        try:
            with open(local_path, "rb") as f:
                file_data = f.read()
        except Exception as e:
            return f"UPLOAD_ERROR: Could not read file: {e}"

        if len(file_data) < 1024:
            return f"UPLOAD_ERROR: File too small ({len(file_data)} bytes) — likely corrupt or empty."

        ext = local_path.rsplit(".", 1)[-1].lower() if "." in local_path else ""
        content_type_map = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "webp": "image/webp",
            "gif": "image/gif", "svg": "image/svg+xml",
            "pdf": "application/pdf",
        }
        content_type = content_type_map.get(ext, "application/octet-stream")

        if not storage_path:
            storage_path = os.path.basename(local_path)

        upload_url = f"{self.url}/storage/v1/object/{bucket_name}/{storage_path}"
        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": content_type,
            "x-upsert": "true",
        }

        try:
            resp = requests.post(upload_url, headers=headers, data=file_data, timeout=60)
            print(f"[storage] Upload {storage_path} to {bucket_name}: HTTP {resp.status_code}")

            if resp.status_code >= 400:
                error_text = resp.text[:300]
                print(f"[storage] Upload failed: {error_text}")
                return f"UPLOAD_ERROR: {error_text}"
        except Exception as e:
            print(f"[storage] Upload exception: {e}")
            return f"UPLOAD_ERROR: {str(e)[:200]}"

        public_url = f"{self.url}/storage/v1/object/public/{bucket_name}/{storage_path}"
        size_kb = len(file_data) / 1024
        print(f"[storage] Uploaded: {storage_path} ({size_kb:.1f} KB) -> {public_url[:100]}")

        # Clean up local file to save disk space
        try:
            os.remove(local_path)
            print(f"[storage] Cleaned up local file: {local_path}")
        except Exception:
            pass

        return (
            f"UPLOAD_SUCCESS URL:{public_url}\n"
            f"Size: {size_kb:.1f} KB\n"
            f"Use this URL directly in code: <img src=\"{public_url}\" />\n"
            f"Or store it in a database column (e.g. image_url TEXT).\n"
            f"Do NOT import this as an ES6 module — use the URL string directly."
        )
    # ══════════════════════════════════════════════════════════════════
    #  TOOL: configure_auth
    # ══════════════════════════════════════════════════════════════════

    def configure_auth(self, enable_email_confirm: bool = True) -> str:
        """
        Get auth configuration and code patterns.
        Returns the exact code the agent should use.
        """
        config = {
            "supabase_url": self.url,
            "anon_key": self.anon_key,
            "email_confirmation": enable_email_confirm,
            "supported_methods": [
                "email/password",
                "magic link",
            ],
        }

        patterns = """
        AUTH IMPLEMENTATION PATTERNS:

        1. SIGN UP:
          const { data, error } = await supabase.auth.signUp({
            email,
            password,
            options: { emailRedirectTo: window.location.origin }
          });
          // IMPORTANT: If email confirmation is enabled, show a "Check your email" message.
          // Do NOT auto-redirect to dashboard after signup.

        2. SIGN IN:
          const { data, error } = await supabase.auth.signInWithPassword({ email, password });

        3. SIGN OUT:
          await supabase.auth.signOut();

        4. GET CURRENT USER:
          const { data: { user } } = await supabase.auth.getUser();

        5. AUTH STATE LISTENER (CRITICAL — set up BEFORE getSession):
          useEffect(() => {
            const { data: { subscription } } = supabase.auth.onAuthStateChange(
              (event, session) => {
                setUser(session?.user ?? null);
                setLoading(false);
              }
            );
            // Then check existing session
            supabase.auth.getSession().then(({ data: { session } }) => {
              setUser(session?.user ?? null);
              setLoading(false);
            });
            return () => subscription.unsubscribe();
          }, []);

        6. PROTECTED ROUTE PATTERN:
          if (loading) return <LoadingSpinner />;
          if (!user) return <Navigate to="/login" />;
          return <Outlet />;

        7. PASSWORD RESET:
          // Step 1: Send reset email
          await supabase.auth.resetPasswordForEmail(email, {
            redirectTo: `${window.location.origin}/reset-password`
          });
          // Step 2: Create a /reset-password page that:
          //   - Checks URL hash for type=recovery
          //   - Shows new password form
          //   - Calls: await supabase.auth.updateUser({ password: newPassword })
          //   - This page MUST be a public route (not behind auth guard)
        """
        return f"AUTH_CONFIG:\n{json.dumps(config, indent=2)}\n\n{patterns}"
    


    # ══════════════════════════════════════════════════════════════════
    #  TOOL: get_project_info
    # ══════════════════════════════════════════════════════════════════

    def get_project_info(self) -> str:
        """Get Supabase project URL, anon key, and client setup info."""
        return json.dumps({
            "supabase_url": self.url,
            "anon_key": self.anon_key,
            "project_ref": self.project_ref,
            "preview_url": self.preview_url,
            "client_import": "import { supabase } from '@/integrations/supabase/client';",
            "types_import": "import type { Database } from '@/integrations/supabase/types';",
            "note": (
                "The Supabase client is already configured at src/integrations/supabase/client.ts. "
                "TypeScript types are at src/integrations/supabase/types.ts and auto-regenerate after migrations. "
                "Environment variables are in .env. Do NOT create a separate supabase.ts file."
            ),
        }, indent=2)

    # ══════════════════════════════════════════════════════════════════
    #  INTERNAL: Security warnings checker
    # ══════════════════════════════════════════════════════════════════

    def _check_security_warnings(self) -> str:
        """Check for common security issues after a migration."""
        warnings = []

        # Check tables without RLS
        sql = """
            SELECT t.tablename
            FROM pg_tables t
            WHERE t.schemaname = 'public'
              AND t.tablename NOT LIKE 'pg_%'
              AND t.rowsecurity = false;
        """
        result = self._execute_sql(sql)
        if result["success"] and result["data"]:
            for row in result["data"]:
                warnings.append(f"CRITICAL: Table '{row['tablename']}' has RLS DISABLED. Anyone can read/write all data.")

        # Check tables with RLS but no policies
        sql2 = """
            SELECT t.tablename
            FROM pg_tables t
            WHERE t.schemaname = 'public'
              AND t.rowsecurity = true
              AND t.tablename NOT IN (
                  SELECT DISTINCT tablename FROM pg_policies WHERE schemaname = 'public'
              );
        """
        result2 = self._execute_sql(sql2)
        if result2["success"] and result2["data"]:
            for row in result2["data"]:
                warnings.append(f"WARNING: Table '{row['tablename']}' has RLS enabled but NO policies. Table is completely inaccessible.")

        return "\n".join(warnings) if warnings else ""

    # ══════════════════════════════════════════════════════════════════
    #  INTERNAL: TypeScript type regeneration
    # ══════════════════════════════════════════════════════════════════

    def _regenerate_types(self) -> str:
        """Query the database schema and regenerate src/integrations/supabase/types.ts."""
        try:
            # Get all tables and columns
            tables_sql = """
                SELECT
                    t.table_name,
                    c.column_name,
                    c.data_type,
                    c.udt_name,
                    c.is_nullable,
                    c.column_default,
                    c.ordinal_position
                FROM information_schema.tables t
                JOIN information_schema.columns c
                    ON c.table_name = t.table_name AND c.table_schema = t.table_schema
                WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE'
                ORDER BY t.table_name, c.ordinal_position;
            """
            tables_result = self._execute_sql(tables_sql)
            if not tables_result["success"]:
                return f"TYPES_WARNING: Could not regenerate types: {tables_result['error']}"

            # Get enums
            enums_sql = """
                SELECT t.typname as enum_name, e.enumlabel as enum_value
                FROM pg_type t
                JOIN pg_enum e ON t.oid = e.enumtypid
                JOIN pg_namespace n ON t.typnamespace = n.oid
                WHERE n.nspname = 'public'
                ORDER BY t.typname, e.enumsortorder;
            """
            enums_result = self._execute_sql(enums_sql)
            enum_map = {}  # name -> [values]
            if enums_result["success"]:
                for row in enums_result["data"]:
                    name = row["enum_name"]
                    if name not in enum_map:
                        enum_map[name] = []
                    enum_map[name].append(row["enum_value"])

            # Build table structure
            table_map = {}  # table_name -> [columns]
            for row in tables_result["data"]:
                tbl = row["table_name"]
                if tbl not in table_map:
                    table_map[tbl] = []
                table_map[tbl].append(row)

            # Generate TypeScript
            types_content = self._build_types_file(table_map, enum_map)

            # Write to workspace
            types_path = os.path.join(self.workspace, "src", "integrations", "supabase", "types.ts")
            os.makedirs(os.path.dirname(types_path), exist_ok=True)
            with open(types_path, "w", encoding="utf-8") as f:
                f.write(types_content)

            print(f"[supabase] Regenerated types.ts with {len(table_map)} table(s) and {len(enum_map)} enum(s)")
            return f"TYPES_REGENERATED: Updated types.ts with {len(table_map)} table(s)."

        except Exception as e:
            print(f"[supabase] Types regeneration error: {e}")
            return f"TYPES_WARNING: Could not regenerate types: {str(e)}"

    def _build_types_file(self, table_map: dict, enum_map: dict) -> str:
        """Build the complete TypeScript types file from schema data."""

        # Build Tables section
        tables_ts = ""
        for tbl_name, columns in sorted(table_map.items()):
            row_fields = []
            insert_fields = []
            update_fields = []

            for col in columns:
                col_name = col["column_name"]
                pg_type = col["data_type"]
                udt_name = col.get("udt_name", "")
                nullable = col["is_nullable"] == "YES"
                has_default = col.get("column_default") is not None

                ts_type = _pg_type_to_ts(pg_type, udt_name, enum_map)

                # Row type — all fields present, nullable ones get | null
                if nullable:
                    row_fields.append(f"          {col_name}: {ts_type} | null")
                else:
                    row_fields.append(f"          {col_name}: {ts_type}")

                # Insert type — fields with defaults or nullable are optional
                if has_default or nullable:
                    if nullable:
                        insert_fields.append(f"          {col_name}?: {ts_type} | null")
                    else:
                        insert_fields.append(f"          {col_name}?: {ts_type}")
                else:
                    insert_fields.append(f"          {col_name}: {ts_type}")

                # Update type — everything optional
                if nullable:
                    update_fields.append(f"          {col_name}?: {ts_type} | null")
                else:
                    update_fields.append(f"          {col_name}?: {ts_type}")

            tables_ts += f"""      {tbl_name}: {{
        Row: {{
{chr(10).join(row_fields)}
        }}
        Insert: {{
{chr(10).join(insert_fields)}
        }}
        Update: {{
{chr(10).join(update_fields)}
        }}
        Relationships: []
      }}\n"""

        if not tables_ts:
            tables_ts = "      [_ in never]: never\n"

        # Build Enums section
        enums_ts = ""
        enums_const = ""
        for enum_name, values in sorted(enum_map.items()):
            quoted = " | ".join(f'"{v}"' for v in values)
            enums_ts += f"      {enum_name}: {quoted}\n"
            values_list = ", ".join(f'"{v}"' for v in values)
            enums_const += f"      {enum_name}: [{values_list}],\n"

        if not enums_ts:
            enums_ts = "      [_ in never]: never\n"

        if not enums_const:
            enums_const = ""

        return f'''// This file is automatically generated after each migration. Do not edit it directly.

export type Json =
  | string
  | number
  | boolean
  | null
  | {{ [key: string]: Json | undefined }}
  | Json[]

export type Database = {{
  __InternalSupabase: {{
    PostgrestVersion: "14.4"
  }}
  public: {{
    Tables: {{
{tables_ts}    }}
    Views: {{
      [_ in never]: never
    }}
    Functions: {{
      [_ in never]: never
    }}
    Enums: {{
{enums_ts}    }}
    CompositeTypes: {{
      [_ in never]: never
    }}
  }}
}}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | {{ schema: keyof DatabaseWithoutInternals }},
  TableName extends DefaultSchemaTableNameOrOptions extends {{
    schema: keyof DatabaseWithoutInternals
  }}
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {{
  schema: keyof DatabaseWithoutInternals
}}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {{
      Row: infer R
    }}
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {{
        Row: infer R
      }}
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | {{ schema: keyof DatabaseWithoutInternals }},
  TableName extends DefaultSchemaTableNameOrOptions extends {{
    schema: keyof DatabaseWithoutInternals
  }}
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {{
  schema: keyof DatabaseWithoutInternals
}}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {{
      Insert: infer I
    }}
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {{
        Insert: infer I
      }}
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | {{ schema: keyof DatabaseWithoutInternals }},
  TableName extends DefaultSchemaTableNameOrOptions extends {{
    schema: keyof DatabaseWithoutInternals
  }}
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {{
  schema: keyof DatabaseWithoutInternals
}}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {{
      Update: infer U
    }}
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {{
        Update: infer U
      }}
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | {{ schema: keyof DatabaseWithoutInternals }},
  EnumName extends DefaultSchemaEnumNameOrOptions extends {{
    schema: keyof DatabaseWithoutInternals
  }}
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = DefaultSchemaEnumNameOrOptions extends {{
  schema: keyof DatabaseWithoutInternals
}}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | {{ schema: keyof DatabaseWithoutInternals }},
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {{
    schema: keyof DatabaseWithoutInternals
  }}
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends {{
  schema: keyof DatabaseWithoutInternals
}}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {{
  public: {{
    Enums: {{
{enums_const}    }},
  }},
}} as const
'''


# ══════════════════════════════════════════════════════════════════════════════
#  SCAFFOLD INJECTION
# ══════════════════════════════════════════════════════════════════════════════

def inject_scaffold(workspace: str, supabase_url: str, anon_key: str, project_ref: str):
    """
    Inject Supabase scaffold files into the project workspace.
    Called once after backend is enabled. Creates:
      - .env
      - src/integrations/supabase/client.ts
      - src/integrations/supabase/types.ts
    Also installs @supabase/supabase-js if not in package.json.
    """

    # 1. Create .env
    env_path = os.path.join(workspace, ".env")
    env_content = (
        f'VITE_SUPABASE_URL="{supabase_url}"\n'
        f'VITE_SUPABASE_PUBLISHABLE_KEY="{anon_key}"\n'
        f'VITE_SUPABASE_PROJECT_ID="{project_ref}"\n'
    )
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(env_content)
    print(f"[scaffold] Created .env")

    # 2. Create src/integrations/supabase/ directory
    supabase_dir = os.path.join(workspace, "src", "integrations", "supabase")
    os.makedirs(supabase_dir, exist_ok=True)

    # 3. Create client.ts
    client_path = os.path.join(supabase_dir, "client.ts")
    client_content = """// This file is automatically generated. Do not edit it directly.
import { createClient } from '@supabase/supabase-js';
import type { Database } from './types';

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL;
const SUPABASE_PUBLISHABLE_KEY = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;

if (!SUPABASE_URL || !SUPABASE_PUBLISHABLE_KEY) {
  throw new Error('Missing Supabase environment variables. Check your .env file.');
}

// Import the supabase client like this:
// import { supabase } from "@/integrations/supabase/client";
export const supabase = createClient<Database>(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
  auth: {
    storage: localStorage,
    persistSession: true,
    autoRefreshToken: true,
  }
});
"""
    with open(client_path, "w", encoding="utf-8") as f:
        f.write(client_content)
    print(f"[scaffold] Created client.ts")

    # 4. Create initial types.ts (matches Lovable format — populated after first migration)
    types_path = os.path.join(supabase_dir, "types.ts")
    types_content = """// This file is automatically generated after each migration. Do not edit it directly.

export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  __InternalSupabase: {
    PostgrestVersion: "14.4"
  }
  public: {
    Tables: {
      [_ in never]: never
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      [_ in never]: never
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {},
  },
} as const
"""
    with open(types_path, "w", encoding="utf-8") as f:
        f.write(types_content)
    print(f"[scaffold] Created types.ts")

    # 5. Ensure @supabase/supabase-js is in package.json
    pkg_path = os.path.join(workspace, "package.json")
    if os.path.exists(pkg_path):
        try:
            with open(pkg_path, "r") as f:
                pkg = json.load(f)
            deps = pkg.get("dependencies", {})
            if "@supabase/supabase-js" not in deps:
                deps["@supabase/supabase-js"] = "^2.49.1"
                pkg["dependencies"] = deps
                with open(pkg_path, "w") as f:
                    json.dump(pkg, f, indent=2)
                print(f"[scaffold] Added @supabase/supabase-js to package.json")
        except Exception as e:
            print(f"[scaffold] Warning: could not update package.json: {e}")

    # 6. Add .env to .gitignore if exists
    gitignore_path = os.path.join(workspace, ".gitignore")
    if os.path.exists(gitignore_path):
        try:
            with open(gitignore_path, "r") as f:
                content = f.read()
            if ".env" not in content:
                with open(gitignore_path, "a") as f:
                    f.write("\n.env\n")
        except Exception:
            pass

    # 7. Register scaffold files in Files_list.txt so the agent discovers them
    scaffold_files = [
        ".env",
        "src/integrations/supabase/client.ts",
        "src/integrations/supabase/types.ts",
    ]
    files_list_path = os.path.join(workspace, "Files_list.txt")
    try:
        existing_files = []
        if os.path.exists(files_list_path):
            with open(files_list_path, "r", encoding="utf-8") as f:
                existing_files = [line.strip() for line in f if line.strip()]

        added = []
        for sf in scaffold_files:
            if sf not in existing_files:
                existing_files.append(sf)
                added.append(sf)

        if added:
            with open(files_list_path, "w", encoding="utf-8") as f:
                f.write("\n".join(existing_files))
            print(f"[scaffold] Registered {len(added)} files in Files_list.txt: {added}")
    except Exception as e:
        print(f"[scaffold] Warning: could not update Files_list.txt: {e}")

    print(f"[scaffold] Supabase scaffold injection complete")


# ══════════════════════════════════════════════════════════════════════════════
#  TOOL DEFINITIONS — what the AI agent sees
# ══════════════════════════════════════════════════════════════════════════════

SUPABASE_TOOL_DEFINITIONS = [
    {
        "name": "migration",
        "description": (
            "Execute database schema changes: CREATE TABLE, ALTER TABLE, CREATE POLICY, "
            "CREATE FUNCTION, CREATE TRIGGER, storage buckets, enums, indexes.\n\n"
            "CRITICAL RULES:\n"
            "- ALWAYS include RLS policies when creating tables with user data.\n"
            "- ALWAYS include proper constraints (NOT NULL, UNIQUE, FOREIGN KEY).\n"
            "- ALWAYS add timestamps: created_at timestamptz default now(), updated_at timestamptz default now().\n"
            "- ALWAYS create an update_updated_at trigger for tables with updated_at.\n"
            "- After this tool runs, TypeScript types are auto-regenerated. Wait for the result before writing code.\n"
            "- Do NOT call this in parallel with write_file or edit_file. Run migration FIRST, then write code.\n"
            "- For INSERT/UPDATE/DELETE data operations, use insert_data instead.\n\n"
            "COMMON PATTERNS:\n"
            "- User-owned table: 'user_id uuid references auth.users(id) on delete cascade not null'\n"
            "- Primary key: 'id uuid default gen_random_uuid() primary key'\n"
            "- RLS for user data: USING (auth.uid() = user_id)\n"
            "- Storage bucket: INSERT INTO storage.buckets ... + CREATE POLICY on storage.objects"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "Complete SQL with all tables, RLS policies, triggers, and functions in one statement."
                }
            },
            "required": ["sql"]
        }
    },
    {
        "name": "insert_data",
        "description": (
            "Execute data operations: INSERT, UPDATE, DELETE.\n"
            "Cannot modify schema (use migration for that).\n"
            "NOTE: auth.uid() is not available — there is no authenticated user context.\n"
            "If RLS blocks the operation, temporarily use service_role or adjust the query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "INSERT, UPDATE, or DELETE SQL statement."}
            },
            "required": ["sql"]
        }
    },
    {
        "name": "read_query",
        "description": (
            "Execute SELECT queries for debugging and data inspection.\n"
            "Use this when users report issues — check the actual database data.\n"
            "Also useful to verify migration results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SELECT or WITH (CTE) query."}
            },
            "required": ["sql"]
        }
    },
    {
        "name": "list_tables",
        "description": (
            "List all database tables with columns, types, RLS status, and policies.\n"
            "Call this before writing queries to check exact column names and types."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "create_storage_bucket",
        "description": (
            "Create a Supabase Storage bucket with default RLS policies.\n"
            "Use for: product images, user avatars, document uploads, etc.\n"
            "Public buckets: anyone can read, authenticated users can upload.\n"
            "Private buckets: only the owner can read/write their own files.\n\n"
            "UPLOAD PATTERN (frontend):\n"
            "  const { data, error } = await supabase.storage\n"
            "    .from('bucket-name')\n"
            "    .upload(`${userId}/${fileName}`, file);\n\n"
            "GET PUBLIC URL:\n"
            "  const { data } = supabase.storage\n"
            "    .from('bucket-name')\n"
            "    .getPublicUrl(filePath);"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "bucket_name": {"type": "string", "description": "Bucket name (lowercase, hyphens OK). E.g. 'product-images', 'avatars'."},
                "public": {"type": "boolean", "description": "True for publicly readable (product images, avatars). False for private files."}
            },
            "required": ["bucket_name", "public"]
        }
    },
    {
        "name": "upload_to_storage",
        "description": (
            "Upload a local file (image, PDF, etc.) to a Supabase Storage bucket.\n"
            "Returns a permanent public URL that works forever — no build hashing, no path issues.\n\n"
            "WORKFLOW FOR DATABASE-REFERENCED IMAGES:\n"
            "1. Call create_storage_bucket (once per project) to create the bucket\n"
            "2. Call generate_image to create the image locally (to public/images/ as temp)\n"
            "3. Call upload_to_storage to upload it and get a permanent URL\n"
            "4. Use that URL directly in your code or store it in a database column\n\n"
            "IMPORTANT:\n"
            "- The local file is auto-deleted after upload to save disk space\n"
            "- The returned URL is permanent and CDN-served — it never breaks\n"
            "- Use storage_path to organize: 'products/shirt.jpg', 'heroes/banner.jpg'\n"
            "- Always create the bucket first with create_storage_bucket\n"
            "- NEVER use the local path after uploading — only use the returned URL"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "bucket_name": {
                    "type": "string",
                    "description": "Name of the storage bucket (must already exist). E.g. 'images', 'product-images'."
                },
                "local_path": {
                    "type": "string",
                    "description": "Path to the local file to upload. E.g. 'public/images/shirt.jpg'."
                },
                "storage_path": {
                    "type": "string",
                    "description": "Path inside the bucket. E.g. 'products/shirt.jpg'. Defaults to filename."
                },
            },
            "required": ["bucket_name", "local_path"]
        }
    },
    {
        "name": "configure_auth",
        "description": (
            "Get authentication configuration, code patterns, and implementation guide.\n"
            "Call this BEFORE implementing any auth code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "enable_email_confirm": {
                    "type": "boolean",
                    "description": "Whether email confirmation is required after signup. Default true."
                }
            },
        }
    },
    {
        "name": "get_project_info",
        "description": (
            "Get Supabase project URL, anon key, and client import paths.\n"
            "The client is already set up at src/integrations/supabase/client.ts.\n"
            "Do NOT create a separate supabase.ts file — use the existing one."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
]


# ══════════════════════════════════════════════════════════════════════════════
#  PROMPT ADDITION — injected when backend is enabled
# ══════════════════════════════════════════════════════════════════════════════

SUPABASE_PROMPT_ADDITION = """

────────────────────────────────────────────────────────
BACKEND / DATABASE (SUPABASE)
────────────────────────────────────────────────────────
CRITICAL: Supabase is now your database. STOP and re-plan if you were going to use localStorage.
- NEVER use localStorage for application data (products, carts, orders, users, messages, profiles)
- NEVER create a "storage.ts", "data.ts", or "store.ts" file that wraps localStorage for app data
- localStorage is ONLY acceptable for UI preferences (theme, sidebar collapsed state)
- ALL data persistence MUST go through Supabase using the tools below
- If you already planned a localStorage approach — DISCARD that plan entirely and use Supabase

This project has a Supabase backend enabled with a pre-configured client.

ALREADY SET UP (do NOT recreate these):
- src/integrations/supabase/client.ts — the Supabase client, import as:
    import { supabase } from '@/integrations/supabase/client';
- src/integrations/supabase/types.ts — auto-generated TypeScript types
- .env — contains VITE_SUPABASE_URL, VITE_SUPABASE_PUBLISHABLE_KEY, VITE_SUPABASE_PROJECT_ID

WORKFLOW — ALWAYS FOLLOW THIS ORDER:
1. Call migration tool with complete SQL (tables + RLS + triggers) — WAIT for result
2. TypeScript types are auto-regenerated after migration
3. THEN write frontend code using the updated types
4. NEVER call migration in parallel with write_file/edit_file

MIGRATION BEST PRACTICES:
- Include ALL related changes in a single migration call (table + RLS + triggers)
- ALWAYS enable RLS on every table: ALTER TABLE public.xxx ENABLE ROW LEVEL SECURITY;
- ALWAYS add policies after enabling RLS or the table becomes inaccessible
- Standard user-owned data pattern:
    CREATE POLICY "Users can view own data" ON public.xxx FOR SELECT USING (auth.uid() = user_id);
    CREATE POLICY "Users can insert own data" ON public.xxx FOR INSERT WITH CHECK (auth.uid() = user_id);
    CREATE POLICY "Users can update own data" ON public.xxx FOR UPDATE USING (auth.uid() = user_id);
    CREATE POLICY "Users can delete own data" ON public.xxx FOR DELETE USING (auth.uid() = user_id);
- Always add created_at/updated_at with a trigger
- Use gen_random_uuid() for primary keys

AUTHENTICATION — CRITICAL FIRST STEP:
Before implementing ANY auth code, decide: does the app need user profiles?
- YES (username, avatar, preferences): Create a profiles table + auto-creation trigger
- NO: Use Supabase auth.users directly

Profile table pattern:
    CREATE TABLE public.profiles (
        id uuid references auth.users(id) on delete cascade primary key,
        email text,
        full_name text,
        avatar_url text,
        created_at timestamptz default now(),
        updated_at timestamptz default now()
    );
    ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
    CREATE POLICY "Users can view own profile" ON public.profiles FOR SELECT USING (auth.uid() = id);
    CREATE POLICY "Users can update own profile" ON public.profiles FOR UPDATE USING (auth.uid() = id);

    -- Auto-create profile on signup
    CREATE OR REPLACE FUNCTION public.handle_new_user()
    RETURNS trigger AS $$
    BEGIN
        INSERT INTO public.profiles (id, email, full_name)
        VALUES (NEW.id, NEW.email, NEW.raw_user_meta_data->>'full_name');
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

    CREATE TRIGGER on_auth_user_created
        AFTER INSERT ON auth.users
        FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

Auth state management (CRITICAL ORDER):
    useEffect(() => {
      const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
        setUser(session?.user ?? null);
        setLoading(false);
      });
      supabase.auth.getSession().then(({ data: { session } }) => {
        setUser(session?.user ?? null);
        setLoading(false);
      });
      return () => subscription.unsubscribe();
    }, []);

IMPORTANT: Set up onAuthStateChange BEFORE calling getSession.

PASSWORD RESET — REQUIRES TWO COMPONENTS:
1. ForgotPassword — calls supabase.auth.resetPasswordForEmail(email, { redirectTo: `${window.location.origin}/reset-password` })
2. /reset-password PAGE (REQUIRED, must be a public route) — checks URL hash for type=recovery, shows new password form, calls supabase.auth.updateUser({ password })

USER ROLES — CRITICAL SECURITY:
- Roles MUST be in a separate user_roles table, NEVER on the profiles table
- NEVER check admin status using localStorage or hardcoded credentials
- Use a security definer function: public.has_role(user_id, role)

STORAGE — FOR FILE/IMAGE UPLOADS:
CRITICAL: ALL database-referenced images (products, listings, blog posts, portfolios)
MUST be uploaded to Supabase Storage. NEVER store local file paths in a database.

WORKFLOW FOR DYNAMIC/DATABASE-REFERENCED IMAGES:
1. Call create_storage_bucket('images', true) — once at the start of the project
2. Call generate_image to create the image locally (use public/images/ as temp location)
3. Call upload_to_storage(bucket_name='images', local_path='public/images/filename.jpg', storage_path='products/filename.jpg')
4. The tool returns a permanent CDN URL like: https://xxxxx.supabase.co/storage/v1/object/public/images/products/shirt.jpg
5. Use that URL directly in your code or store it in a database column (image_url TEXT)
6. The local temp file is auto-deleted after upload — this is expected behavior

You CAN batch multiple generate_image calls in parallel, then batch multiple upload_to_storage calls in parallel.

STATIC UI IMAGES (hero banners, logos, backgrounds hardcoded in components — NOT in any database):
- These are fine in src/assets/ with ES6 imports — they do NOT need storage upload
- Only database-referenced images need the upload workflow

FRONTEND USER-UPLOAD PATTERN (for images uploaded by end users at runtime):
  const { data, error } = await supabase.storage
    .from('images')
    .upload(`${user.id}/${file.name}`, file);
  const { data: urlData } = supabase.storage
    .from('images')
    .getPublicUrl(data.path);
  await supabase.from('products').update({ image_url: urlData.publicUrl }).eq('id', productId);

ANTI-PATTERNS — NEVER DO THESE:
- NEVER store paths like 'public/images/shirt.jpg' or './src/assets/shirt.jpg' in a database
- NEVER generate images to src/assets/ and reference them from database records
- NEVER use local filesystem paths as image URLs in database-backed content
- NEVER skip upload_to_storage when building database-backed content with images
- NEVER import a storage URL as an ES6 module — use it as a plain string in src or img tags

IMPORTANT: Email confirmation is ENABLED. After signup, show a "Check your email to verify" message.
Never put the service_role key in frontend code.
Column names in TypeScript MUST exactly match database column names.
"""


# ══════════════════════════════════════════════════════════════════════════════
#  REGISTRATION HELPER — wires tools into an agent instance
# ══════════════════════════════════════════════════════════════════════════════

def register_supabase_tools(agent, supabase_config: dict, workspace: str):
    """
    Register all Supabase tools on an agent instance.
    Called from create_generator() when supabase_config is present.

    Args:
        agent: The BaseAgent instance
        supabase_config: dict with url, anon_key, service_role_key, project_ref, preview_url
        workspace: path to the project workspace
    """
    sb = SupabaseTools(
        supabase_url=supabase_config["url"],
        anon_key=supabase_config["anon_key"],
        service_role_key=supabase_config.get("service_role_key", ""),
        project_ref=supabase_config.get("project_ref", ""),
        workspace=workspace,
        preview_url=supabase_config.get("preview_url", ""),
    )

    # Map tool names to methods
    tool_map = {
        "migration": lambda sql: sb.migration(sql),
        "insert_data": lambda sql: sb.insert_data(sql),
        "read_query": lambda sql: sb.read_query(sql),
        "list_tables": lambda: sb.list_tables(),
        "create_storage_bucket": lambda bucket_name, public=True: sb.create_storage_bucket(bucket_name, public),
        "upload_to_storage": lambda bucket_name, local_path, storage_path="": sb.upload_to_storage(bucket_name, local_path, storage_path),
        "configure_auth": lambda enable_email_confirm=True: sb.configure_auth(enable_email_confirm),
        "get_project_info": lambda: sb.get_project_info(),
    }

    # Register tools on the agent
    for name, fn in tool_map.items():
        agent.tool_map[name] = fn

    # Add tool definitions
    for tool_def in SUPABASE_TOOL_DEFINITIONS:
        if not any(t["name"] == tool_def["name"] for t in agent.tools):
            agent.tools.append(tool_def)

    # Inject prompt addition
    if "BACKEND / DATABASE (SUPABASE)" not in agent.system_prompt:
        agent.system_prompt += SUPABASE_PROMPT_ADDITION

    # Inject scaffold files into the workspace
    inject_scaffold(
        workspace=workspace,
        supabase_url=supabase_config["url"],
        anon_key=supabase_config["anon_key"],
        project_ref=supabase_config.get("project_ref", ""),
    )

    # Run initial type generation (picks up any existing tables)
    sb._regenerate_types()

    print(f"[supabase] Registered {len(SUPABASE_TOOL_DEFINITIONS)} Supabase tools")
    return sb