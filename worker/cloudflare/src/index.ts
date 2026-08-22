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
  activeUntil: number;
  envelope?: JsonObject;
  error?: string;
}

interface ActiveCall {
  callId: string;
  expiresAt: number;
}

type Reservation =
  | { kind: "reserved" }
  | { kind: "terminal"; state: CallState }
  | { kind: "existing"; state: CallState }
  | { kind: "conflict" }
  | { kind: "busy" }
  | { kind: "reset"; resetId: string; expiredCallId: string };

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
const SHARD_COUNTS = { interactive: 5, batch: 3 } as const;
const TERMINAL_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;

function shardName(lane: "interactive" | "batch", callId: string): string {
  // Every novel Container ID cold-starts. A fixed pool reuses Python images
  // and their bounded immutable source cache, while this deterministic hash
  // lets any later status request find the same durable call record.
  let hash = 2166136261;
  for (let i = 0; i < callId.length; i += 1) {
    hash ^= callId.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return `${lane}-${(hash >>> 0) % SHARD_COUNTS[lane]}`;
}

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
  sleepAfter = "60s";
  enableInternet = true;
  protected abstract readonly containerProfile: "standard-3" | "standard-4";

  private environment(): Record<string, string> {
    const optional = (value: string | undefined): string => value ?? "";
    return {
      WORKER_ROLE: "executor",
      EXECUTOR_PROVIDER: "cloudflare",
      CLOUDFLARE_CONTAINER_PROFILE: this.containerProfile,
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

  private stateKey(callId: string): string {
    return `call:${callId}`;
  }

  private terminalKey(callId: string, at: number): string {
    return `terminal:${String(at).padStart(13, "0")}:${callId}`;
  }

  private async callState(callId: string): Promise<CallState | null> {
    return (await this.ctx.storage.get<CallState>(this.stateKey(callId))) ?? null;
  }

  private async release(callId: string): Promise<void> {
    await this.ctx.storage.transaction(async (txn) => {
      const active = await txn.get<ActiveCall>("active");
      if (active?.callId === callId) await txn.delete("active");
    });
  }

  private async storeTerminal(
    callId: string,
    state: CallState,
    at = Date.now(),
  ): Promise<void> {
    await this.ctx.storage.put({
      [this.stateKey(callId)]: state,
      [this.terminalKey(callId, at)]: callId,
    });
  }

  private async pruneTerminalCalls(now = Date.now()): Promise<void> {
    // Marker keys are ordered by completion time. Delete at most 64 calls per
    // completion so cleanup remains bounded and the Storage API's 128-key
    // delete limit is never crossed. Repeated jobs drain any backlog.
    const markers = await this.ctx.storage.list<string>({
      prefix: "terminal:", limit: 64,
    });
    const cutoff = now - TERMINAL_RETENTION_MS;
    const keys: string[] = [];
    for (const [marker, callId] of markers) {
      const completedAt = Number(marker.split(":", 3)[1]);
      if (!Number.isFinite(completedAt) || completedAt >= cutoff) break;
      keys.push(marker, this.stateKey(callId));
    }
    if (keys.length) await this.ctx.storage.delete(keys);
  }

  private async reserve(
    callId: string,
    jobType: string,
    now: number,
    activeUntil: number,
  ): Promise<Reservation> {
    // A Durable Object may interleave requests at await points. Keep the
    // call-id check and per-shard admission lock in one storage transaction,
    // otherwise two simultaneous edits could both observe an idle shard.
    return this.ctx.storage.transaction(async (txn) => {
      const existing = await txn.get<CallState>(this.stateKey(callId));
      if (existing?.status === "done" || existing?.status === "failed") {
        return { kind: "terminal", state: existing };
      }
      if (existing && existing.jobType !== jobType) {
        return { kind: "conflict" };
      }
      if (existing) return { kind: "existing", state: existing };

      const active = await txn.get<ActiveCall>("active");
      if (active && active.expiresAt > now) return { kind: "busy" };
      if (active) {
        // Serialize the destructive container reset too. Other new calls see
        // this short reset lease as busy and may safely stay on Modal.
        const expiredCallId = active.callId.startsWith("reset:")
          ? active.callId.slice("reset:".length)
          : active.callId;
        const resetId = `reset:${expiredCallId}`;
        await txn.put("active", { callId: resetId, expiresAt: now + 120_000 });
        return {
          kind: "reset", resetId, expiredCallId,
        };
      }

      const state: CallState = {
        status: "submitted", jobType,
        updatedAt: new Date().toISOString(), activeUntil,
      };
      await txn.put({
        [this.stateKey(callId)]: state,
        active: { callId, expiresAt: activeUntil } satisfies ActiveCall,
      });
      return { kind: "reserved" };
    });
  }

  override async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const statusMatch = url.pathname.match(/^\/status\/([^/]+)$/);
    if (request.method === "GET" && statusMatch && CALL_ID.test(statusMatch[1])) {
      const state = await this.callState(statusMatch[1]);
      return state ? json(state) : json({ status: "missing" }, 404);
    }
    const executeMatch = url.pathname.match(/^\/execute\/([^/]+)$/);
    if (request.method !== "POST" || !executeMatch || !CALL_ID.test(executeMatch[1])) {
      return json({ error: "not found" }, 404);
    }
    const callId = executeMatch[1];

    const body = (await request.json()) as { job?: ExecutorJob; timeout_s?: number };
    const job = body.job;
    if (!job || !Number.isInteger(job.id) || !Number.isInteger(job.total_claims)) {
      return json({ error: "invalid queue job", safe_to_fallback: true }, 400);
    }
    const now = Date.now();
    const requestedTimeout = Number(body.timeout_s ?? 3600);
    const timeoutSeconds = Number.isFinite(requestedTimeout)
      ? Math.max(60, Math.min(7200, requestedTimeout))
      : 3600;
    const activeUntil = now + timeoutSeconds * 1000;
    let reservation = await this.reserve(
      callId, job.type, now, activeUntil,
    );
    if (reservation.kind === "reset") {
      // The prior dispatch lease has expired. Its /run may still occupy this
      // shared container after an ambiguous Worker disconnect; starting a new
      // ffmpeg beside it would exceed the instance shape. Stop the shard first
      // and cold-restart it for the next call. No new /run has been sent yet,
      // so a stop failure remains safe to handle on Modal.
      try {
        await this.stop();
      } catch (error) {
        return json({
          error: `expired Cloudflare shard could not be reset: ${String(error)}`,
          safe_to_fallback: true,
        }, 503);
      }
      const expiredAt = Date.now();
      await this.storeTerminal(reservation.expiredCallId, {
        status: "failed", jobType: "expired",
        error: "Cloudflare execution lease expired before reconciliation",
        updatedAt: new Date(expiredAt).toISOString(), activeUntil: expiredAt,
      }, expiredAt);
      await this.release(reservation.resetId);
      reservation = await this.reserve(
        callId, job.type, Date.now(), activeUntil,
      );
    }
    if (reservation.kind === "terminal") {
      const existing = reservation.state;
      return json(existing.envelope ?? {
        error: existing.error ?? "executor failed",
      });
    }
    if (reservation.kind === "conflict") {
      return json({ error: "call id already belongs to another job type" }, 409);
    }
    if (reservation.kind === "existing") {
      // A named call is at-most-one physical /run. Reconnectors use /status;
      // repeating POST while the first request is ambiguous must never start
      // another Python runner in the same container.
      return json({
        error: "call already accepted",
        call_status: reservation.state.status,
      }, 409);
    }
    if (reservation.kind !== "reserved") {
      // No /run request exists for this new call, so the dispatcher can keep
      // the user moving on Modal rather than queueing behind a busy shard.
      return json({
        error: "Cloudflare Container shard is busy",
        safe_to_fallback: true,
      }, 429);
    }

    const stateKey = this.stateKey(callId);
    const update = async (state: CallState): Promise<void> => {
      await this.ctx.storage.put(stateKey, state);
    };
    try {
      await update({
        status: "starting", jobType: job.type,
        updatedAt: new Date().toISOString(), activeUntil,
      });
      await this.startAndWaitForPorts({
        ports: [8080],
        startOptions: { envVars: this.environment(), enableInternet: true },
        cancellationOptions: { portReadyTimeoutMS: 120_000, instanceGetTimeoutMS: 30_000 },
      });
    } catch (error) {
      // No /run request was sent. The dispatcher may safely use Modal.
      await this.ctx.storage.delete(stateKey);
      await this.release(callId);
      return json({
        error: String(error),
        safe_to_fallback: true,
      }, 503);
    }

    await update({
      status: "running", jobType: job.type,
      updatedAt: new Date().toISOString(), activeUntil,
    });
    try {
      const response = await this.containerFetch("http://localhost:8080/run", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${this.env.EXECUTOR_SECRET}`,
        },
        body: JSON.stringify({
          job: {
            ...job,
            provider_call_id: callId,
            provider_adapter_version: this.env.CODE_VERSION ?? "unknown",
          },
        }),
      });
      const envelope = (await response.json()) as JsonObject;
      const status = envelope.error ? "failed" : "done";
      const terminalAt = Date.now();
      await this.storeTerminal(callId, {
        status, jobType: job.type, envelope,
        updatedAt: new Date(terminalAt).toISOString(), activeUntil,
      }, terminalAt);
      await this.release(callId);
      this.ctx.waitUntil(this.pruneTerminalCalls(terminalAt));
      return json(envelope);
    } catch (error) {
      // Once /run was sent, a lost Worker-side connection is ambiguous: the
      // Python process may still be encoding and will commit through Postgres.
      // Keep the named call recoverable; never authorize a second provider.
      const failedAt = Date.now();
      const active = await this.ctx.storage.get<ActiveCall>("active");
      if (active?.callId === callId) {
        await update({
          status: "unknown",
          jobType: job.type,
          error: String(error),
          updatedAt: new Date(failedAt).toISOString(),
          activeUntil,
        });
      } else {
        await this.storeTerminal(callId, {
          status: "failed", jobType: job.type,
          error: `container reset after ambiguous call: ${String(error)}`,
          updatedAt: new Date(failedAt).toISOString(), activeUntil,
        }, failedAt);
        this.ctx.waitUntil(this.pruneTerminalCalls(failedAt));
      }
      return json({ error: String(error), call_status: "unknown" }, 502);
    }
  }
}

export class ValmeraInteractive extends ValmeraContainer {
  protected readonly containerProfile = "standard-3" as const;
}
export class ValmeraBatch extends ValmeraContainer {
  protected readonly containerProfile = "standard-4" as const;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      if (!(await authorized(request, env.EXECUTOR_SECRET))) {
        return json({ error: "unauthorized", safe_to_fallback: true }, 401);
      }
      return json({
        status: "ok", provider: "cloudflare",
        code_version: env.CODE_VERSION ?? "unknown",
        shards: SHARD_COUNTS,
      });
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
    const [, rawLane, callId] = match;
    const lane = rawLane as "interactive" | "batch";
    const shard = shardName(lane, callId);
    const stub = lane === "interactive"
      ? env.INTERACTIVE.getByName(shard)
      : env.BATCH.getByName(shard);
    if (request.method === "GET") {
      return stub.fetch(`https://container.internal/status/${callId}`);
    }
    if (request.method !== "POST") return json({ error: "method not allowed" }, 405);
    const body = (await request.json()) as { job?: ExecutorJob };
    const jobType = body.job?.type ?? "";
    const allowed = lane === "interactive" ? INTERACTIVE_TYPES : BATCH_TYPES;
    if (!allowed.has(jobType)) {
      return json({ error: `job type ${jobType} is not allowed on ${lane}`, safe_to_fallback: true }, 400);
    }
    return stub.fetch(`https://container.internal/execute/${callId}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  },
} satisfies ExportedHandler<Env>;
