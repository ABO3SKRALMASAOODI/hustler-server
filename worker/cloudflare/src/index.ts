import { Container } from "@cloudflare/containers";

type JsonObject = Record<string, unknown>;

interface ExecutorJob extends JsonObject {
  id: number;
  type: string;
  project_id: number;
  total_claims: number;
  payload: JsonObject;
}

interface CallState {
  status: "submitted" | "starting" | "running" | "unknown" | "done" | "failed";
  jobType: string;
  updatedAt: string;
  envelope?: JsonObject;
  error?: string;
}

interface Env {
  INTERACTIVE: DurableObjectNamespace<ValmeraInteractive>;
  BATCH: DurableObjectNamespace<ValmeraBatch>;
  EXECUTOR_SECRET: string;
  DATABASE_URL: string;
  S3_ENDPOINT: string;
  S3_ACCESS_KEY_ID: string;
  S3_SECRET_ACCESS_KEY: string;
  S3_BUCKET: string;
  S3_REGION?: string;
  OPENAI_API_KEY?: string;
  OPENAI_BASE_URL?: string;
  VISION_API_KEY?: string;
  VISION_BASE_URL?: string;
  VISION_MODEL?: string;
  CODE_VERSION?: string;
}

const INTERACTIVE_TYPES = new Set(["preview", "preview_check", "filmstrip"]);
const BATCH_TYPES = new Set(["index", "final"]);
const CALL_ID = /^[a-zA-Z0-9_-]{8,96}$/;

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

async function authorized(request: Request, expected: string): Promise<boolean> {
  if (!expected) return false;
  const supplied = request.headers.get("authorization")?.replace(/^Bearer /, "") ?? "";
  const encoded = new TextEncoder();
  const [left, right] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoded.encode(supplied)),
    crypto.subtle.digest("SHA-256", encoded.encode(expected)),
  ]);
  const a = new Uint8Array(left);
  const b = new Uint8Array(right);
  let different = a.length ^ b.length;
  for (let i = 0; i < Math.min(a.length, b.length); i += 1) different |= a[i] ^ b[i];
  return different === 0;
}

abstract class ValmeraContainer extends Container<Env> {
  defaultPort = 8080;
  sleepAfter = "10s";
  enableInternet = true;

  private environment(): Record<string, string> {
    const optional = (value: string | undefined): string => value ?? "";
    return {
      WORKER_ROLE: "executor",
      EXECUTOR_PROVIDER: "cloudflare",
      EXECUTION_POLICY_MODE: "legacy",
      PORT: "8080",
      PYTHONUNBUFFERED: "1",
      WORKER_TMP_DIR: "/tmp/valmera",
      REMOTE_EXECUTOR_SECRET: this.env.EXECUTOR_SECRET,
      DATABASE_URL: this.env.DATABASE_URL,
      S3_ENDPOINT: this.env.S3_ENDPOINT,
      S3_ACCESS_KEY_ID: this.env.S3_ACCESS_KEY_ID,
      S3_SECRET_ACCESS_KEY: this.env.S3_SECRET_ACCESS_KEY,
      S3_BUCKET: this.env.S3_BUCKET,
      S3_REGION: optional(this.env.S3_REGION) || "auto",
      OPENAI_API_KEY: optional(this.env.OPENAI_API_KEY),
      OPENAI_BASE_URL: optional(this.env.OPENAI_BASE_URL) || "https://api.openai.com/v1",
      VISION_API_KEY: optional(this.env.VISION_API_KEY),
      VISION_BASE_URL: optional(this.env.VISION_BASE_URL),
      VISION_MODEL: optional(this.env.VISION_MODEL),
    };
  }

  private async callState(): Promise<CallState | null> {
    return (await this.ctx.storage.get<CallState>("call")) ?? null;
  }

  override async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/status") {
      const state = await this.callState();
      return state ? json(state) : json({ status: "missing" }, 404);
    }
    if (request.method !== "POST" || url.pathname !== "/execute") {
      return json({ error: "not found" }, 404);
    }

    const body = (await request.json()) as { job?: ExecutorJob };
    const job = body.job;
    if (!job || !Number.isInteger(job.id) || !Number.isInteger(job.total_claims)) {
      return json({ error: "invalid queue job" }, 400);
    }
    const existing = await this.callState();
    if (existing?.status === "done" || existing?.status === "failed") {
      return json(existing.envelope ?? { error: existing.error ?? "executor failed" });
    }
    if (existing && existing.jobType !== job.type) {
      return json({ error: "call id already belongs to another job type" }, 409);
    }
    if (existing) {
      // A named call is at-most-one physical /run. Reconnectors use /status;
      // repeating POST while the first request is ambiguous must never start
      // another Python runner in the same container.
      return json({ error: "call already accepted", call_status: existing.status }, 409);
    }

    const update = async (state: CallState): Promise<void> => {
      await this.ctx.storage.put("call", state);
    };
    await update({ status: "submitted", jobType: job.type, updatedAt: new Date().toISOString() });
    try {
      await update({ status: "starting", jobType: job.type, updatedAt: new Date().toISOString() });
      await this.startAndWaitForPorts({
        ports: [8080],
        startOptions: { envVars: this.environment(), enableInternet: true },
        cancellationOptions: { portReadyTimeoutMS: 120_000, instanceGetTimeoutMS: 30_000 },
      });
    } catch (error) {
      // No /run request was sent. The dispatcher may safely use Modal.
      await this.ctx.storage.delete("call");
      return json({
        error: String(error),
        safe_to_fallback: true,
      }, 503);
    }

    await update({ status: "running", jobType: job.type, updatedAt: new Date().toISOString() });
    try {
      const response = await this.containerFetch("http://localhost:8080/run", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${this.env.EXECUTOR_SECRET}`,
        },
        body: JSON.stringify({ job }),
      });
      const envelope = (await response.json()) as JsonObject;
      const status = envelope.error ? "failed" : "done";
      await update({ status, jobType: job.type, envelope, updatedAt: new Date().toISOString() });
      return json(envelope);
    } catch (error) {
      // Once /run was sent, a lost Worker-side connection is ambiguous: the
      // Python process may still be encoding and will commit through Postgres.
      // Keep the named call recoverable; never authorize a second provider.
      await update({
        status: "unknown",
        jobType: job.type,
        error: String(error),
        updatedAt: new Date().toISOString(),
      });
      return json({ error: String(error), call_status: "unknown" }, 502);
    }
  }
}

export class ValmeraInteractive extends ValmeraContainer {}
export class ValmeraBatch extends ValmeraContainer {}

function binding(env: Env, lane: string): DurableObjectNamespace<ValmeraContainer> | null {
  if (lane === "interactive") return env.INTERACTIVE;
  if (lane === "batch") return env.BATCH;
  return null;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      if (!(await authorized(request, env.EXECUTOR_SECRET))) {
        return json({ error: "unauthorized", safe_to_fallback: true }, 401);
      }
      return json({ status: "ok", provider: "cloudflare", code_version: env.CODE_VERSION ?? "unknown" });
    }
    if (!(await authorized(request, env.EXECUTOR_SECRET))) {
      // Authentication happens before a Durable Object is resolved, so no
      // named Container call can exist and Modal fallback is unambiguous.
      return json({ error: "unauthorized", safe_to_fallback: true }, 401);
    }
    const match = url.pathname.match(/^\/calls\/(interactive|batch)\/([^/]+)$/);
    if (!match || !CALL_ID.test(match[2])) {
      return json({ error: "not found", safe_to_fallback: true }, 404);
    }
    const [, lane, callId] = match;
    const namespace = binding(env, lane);
    if (!namespace) return json({ error: "unknown lane" }, 404);
    const stub = namespace.getByName(callId);
    if (request.method === "GET") {
      return stub.fetch("https://container.internal/status");
    }
    if (request.method !== "POST") return json({ error: "method not allowed" }, 405);
    const body = (await request.json()) as { job?: ExecutorJob };
    const jobType = body.job?.type ?? "";
    const allowed = lane === "interactive" ? INTERACTIVE_TYPES : BATCH_TYPES;
    if (!allowed.has(jobType)) {
      return json({ error: `job type ${jobType} is not allowed on ${lane}`, safe_to_fallback: true }, 400);
    }
    return stub.fetch("https://container.internal/execute", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  },
} satisfies ExportedHandler<Env>;
