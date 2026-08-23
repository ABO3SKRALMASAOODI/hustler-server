import { Container } from "@cloudflare/containers";

type JsonObject = Record<string, unknown>;

interface ExecutorJob extends JsonObject {
  id: number | null;
  type: string;
  project_id: number;
  total_claims: number | null;
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
  AGENT: DurableObjectNamespace<ValmeraAgent>;
  MCP: DurableObjectNamespace<ValmeraMcp>;
  SHORTS: DurableObjectNamespace<ValmeraShorts>;
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
  IMAGE_API_KEY?: string;
  IMAGE_BASE_URL?: string;
  IMAGE_GEN_MODEL?: string;
  FAL_KEY?: string;
  DEEPGRAM_API_KEY?: string;
  PEXELS_API_KEY?: string;
  PIXABAY_API_KEY?: string;
  MODAL_TOKEN_ID?: string;
  MODAL_TOKEN_SECRET?: string;
  MODAL_EXECUTOR_APP?: string;
  MODAL_EXECUTOR_ENVIRONMENT?: string;
  CLOUDFLARE_EXECUTOR_URL?: string;
  CODE_VERSION?: string;
  SOURCE_VERSION?: string;
}

const INTERACTIVE_TYPES = new Set(["preview", "preview_check", "filmstrip", "frames"]);
const BATCH_TYPES = new Set(["index", "final"]);
const AGENT_TYPES = new Set(["agent_turn"]);
const MCP_TYPES = new Set(["mcp_tool"]);
const SHORTS_TYPES = new Set(["shorts_plan"]);
const CALL_ID = /^[a-zA-Z0-9_-]{8,96}$/;
const SHARD_COUNTS = {
  interactive: 5, batch: 3, agent: 5, mcp: 12, shorts: 8,
} as const;
type Lane = keyof typeof SHARD_COUNTS;
const TERMINAL_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;
// startAndWaitForPorts normally returns inside its 120-second port-ready
// bound. A Worker version replacement can instead abandon the handler after
// it persisted `starting`. No Python /run can precede the awaited transition
// to `running`, so this state is provably safe to fail over after three
// minutes rather than occupying an MCP request for its full 26-minute lease.
const STARTING_STALE_MS = 180 * 1000;

function shardName(lane: Lane, callId: string): string {
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
  protected abstract readonly containerProfile: "standard-1" | "standard-3" | "standard-4";
  protected abstract readonly workerRole:
    "executor" | "agent_executor" | "mcp_executor" | "shorts_executor";

  override async onActivityExpired(): Promise<void> {
    // Cloudflare's default hook sends SIGTERM.  The production Python
    // executors remained Running for 6-12 hours after completed calls even
    // though sleepAfter was 60s, continuing to bill provisioned memory and
    // disk.  A forced destroy is safe only when no durable provider lease can
    // still own work: an ambiguous disconnected /run keeps this row active
    // until its bounded deadline, while ordinary completions release it
    // before their response is returned.
    const active = await this.ctx.storage.get<ActiveCall>("active");
    if (active && active.expiresAt > Date.now()) {
      this.renewActivityTimeout();
      return;
    }
    console.log("Idle timeout expired with no active provider lease; destroying container");
    await this.destroy();
  }

  private environment(): Record<string, string> {
    const optional = (value: string | undefined): string => value ?? "";
    return {
      WORKER_ROLE: this.workerRole,
      EXECUTOR_PROVIDER: "cloudflare",
      CLOUDFLARE_CONTAINER_PROFILE: this.containerProfile,
      // Orchestration containers call synchronous media tools. Route those
      // child calls back through this Worker first; Modal stays the bounded
      // launch/capacity fallback instead of silently remaining primary.
      CLOUDFLARE_EXECUTOR_ENABLED: "1",
      CLOUDFLARE_EXECUTOR_PERCENT: "100",
      CLOUDFLARE_EXECUTOR_TYPES: "frames",
      CLOUDFLARE_SYNCHRONOUS_TYPES: "frames",
      CLOUDFLARE_EXECUTOR_URL: optional(this.env.CLOUDFLARE_EXECUTOR_URL),
      EXECUTION_POLICY_MODE: "redesign",
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
      IMAGE_API_KEY: optional(this.env.IMAGE_API_KEY),
      IMAGE_BASE_URL: optional(this.env.IMAGE_BASE_URL),
      IMAGE_GEN_MODEL: optional(this.env.IMAGE_GEN_MODEL),
      FAL_KEY: optional(this.env.FAL_KEY),
      DEEPGRAM_API_KEY: optional(this.env.DEEPGRAM_API_KEY),
      PEXELS_API_KEY: optional(this.env.PEXELS_API_KEY),
      PIXABAY_API_KEY: optional(this.env.PIXABAY_API_KEY),
      MODAL_TOKEN_ID: optional(this.env.MODAL_TOKEN_ID),
      MODAL_TOKEN_SECRET: optional(this.env.MODAL_TOKEN_SECRET),
      MODAL_EXECUTOR_ENABLED: "1",
      MODAL_EXECUTOR_PERCENT: "100",
      MODAL_EXECUTOR_APP: optional(this.env.MODAL_EXECUTOR_APP) || "valmera-executor",
      MODAL_EXECUTOR_ENVIRONMENT: optional(this.env.MODAL_EXECUTOR_ENVIRONMENT) || "main",
    };
  }

  private stateKey(callId: string): string {
    return `call:${callId}`;
  }

  private async containerReadiness(): Promise<{
    ok: boolean; body?: JsonObject; error?: string;
  }> {
    try {
      const response = await this.containerFetch("http://localhost:8080/health");
      const body = (await response.json()) as JsonObject;
      const expectedSource = this.env.SOURCE_VERSION ?? "unknown";
      const sourceMatches = expectedSource === "unknown"
        || expectedSource === "set-by-deploy-workflow"
        || body.code_version === expectedSource;
      const roleMatches = body.role === this.workerRole;
      if (response.ok && body.status === "ok" && sourceMatches && roleMatches) {
        return { ok: true, body };
      }
      return {
        ok: false,
        body,
        error: `container readiness mismatch role=${String(body.role)} source=${String(body.code_version)}`,
      };
    } catch (error) {
      return { ok: false, error: `container readiness failed: ${String(error)}` };
    }
  }

  private terminalKey(callId: string, at: number): string {
    return `terminal:${String(at).padStart(13, "0")}:${callId}`;
  }

  private async callState(callId: string): Promise<CallState | null> {
    return (await this.ctx.storage.get<CallState>(this.stateKey(callId))) ?? null;
  }

  private async expireStaleStart(
    callId: string, now = Date.now(),
  ): Promise<CallState | null> {
    const terminal = await this.ctx.storage.transaction(async (txn) => {
      const key = this.stateKey(callId);
      const current = (await txn.get<CallState>(key)) ?? null;
      if (current?.status !== "starting") return current;
      const updatedAt = Date.parse(current.updatedAt);
      if (!Number.isFinite(updatedAt) || now - updatedAt < STARTING_STALE_MS) {
        return current;
      }
      const message = "Cloudflare container startup was abandoned before /run";
      const failed: CallState = {
        status: "failed", jobType: current.jobType,
        envelope: {
          error: message, retryable: true,
          failure: { kind: "transient_infrastructure", retryable: true },
        },
        error: message,
        updatedAt: new Date(now).toISOString(), activeUntil: now,
      };
      const active = await txn.get<ActiveCall>("active");
      await txn.put({
        [key]: failed,
        [this.terminalKey(callId, now)]: callId,
      });
      if (active?.callId === callId) await txn.delete("active");
      return failed;
    });
    if (terminal?.status === "failed"
        && terminal.error?.includes("abandoned before /run")) {
      // Cleanup is asynchronous: the terminal state already proves no /run
      // was sent, so a slow provider stop cannot delay safe Modal fallback.
      this.ctx.waitUntil(this.stop().catch(() => undefined));
    }
    return terminal;
  }

  private async markRunning(
    callId: string, jobType: string, activeUntil: number,
  ): Promise<CallState | null> {
    return this.ctx.storage.transaction(async (txn) => {
      const key = this.stateKey(callId);
      const current = (await txn.get<CallState>(key)) ?? null;
      const active = await txn.get<ActiveCall>("active");
      if (current?.status !== "starting" || active?.callId !== callId) {
        return current;
      }
      const running: CallState = {
        status: "running", jobType,
        updatedAt: new Date().toISOString(), activeUntil,
      };
      await txn.put(key, running);
      return running;
    });
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
    if (request.method === "GET" && url.pathname === "/probe") {
      try {
        await this.startAndWaitForPorts({
          ports: [8080],
          startOptions: { envVars: this.environment(), enableInternet: true },
          cancellationOptions: { portReadyTimeoutMS: 120_000, instanceGetTimeoutMS: 30_000 },
        });
      } catch (error) {
        return json({ error: String(error) }, 503);
      }
      const readiness = await this.containerReadiness();
      return json(readiness.body ?? { error: readiness.error }, readiness.ok ? 200 : 503);
    }
    const statusMatch = url.pathname.match(/^\/status\/([^/]+)$/);
    if (request.method === "GET" && statusMatch && CALL_ID.test(statusMatch[1])) {
      const state = await this.expireStaleStart(statusMatch[1]);
      return state ? json(state) : json({ status: "missing" }, 404);
    }
    const executeMatch = url.pathname.match(/^\/execute\/([^/]+)$/);
    if (request.method !== "POST" || !executeMatch || !CALL_ID.test(executeMatch[1])) {
      return json({ error: "not found" }, 404);
    }
    const callId = executeMatch[1];

    const body = (await request.json()) as { job?: ExecutorJob; timeout_s?: number };
    const job = body.job;
    const queueBacked = Number.isInteger(job?.id)
      && Number.isInteger(job?.total_claims);
    const synchronous = job?.id == null && job?.total_claims == null
      && job?.type === "frames";
    if (!job || (!queueBacked && !synchronous)) {
      return json({ error: "invalid executor job", safe_to_fallback: true }, 400);
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
      const current = await this.callState(callId);
      if (current?.status === "done" || current?.status === "failed") {
        return json(current.envelope ?? {
          error: current.error ?? "Cloudflare call ended during startup",
        });
      }
      // No /run request was sent. The dispatcher may safely use Modal.
      await this.ctx.storage.delete(stateKey);
      await this.release(callId);
      return json({
        error: String(error),
        safe_to_fallback: true,
      }, 503);
    }

    // Wrangler activates Worker code before every old Container instance is
    // necessarily replaced. Validate the image's own source fingerprint and
    // role after startup but before /run; deploy skew then becomes a safe
    // Modal fallback instead of an older renderer touching a current EDL.
    const readiness = await this.containerReadiness();
    if (!readiness.ok) {
      try {
        await this.stop();
      } catch {
        // No /run was sent, so fallback remains safe even if cleanup fails.
      }
      await this.ctx.storage.delete(stateKey);
      await this.release(callId);
      return json({
        error: readiness.error ?? "Cloudflare container image is not ready",
        safe_to_fallback: true,
      }, 503);
    }

    // Atomically prove the startup still owns the reservation. A status
    // request may have terminalized an abandoned start while the provider API
    // was returning; that late handler must never send /run after fallback.
    const running = await this.markRunning(callId, job.type, activeUntil);
    if (running?.status !== "running") {
      return json(running?.envelope ?? {
        error: running?.error ?? "Cloudflare startup no longer owns this call",
        retryable: true,
        failure: { kind: "transient_infrastructure", retryable: true },
      });
    }
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
  protected readonly workerRole = "executor" as const;
}
export class ValmeraBatch extends ValmeraContainer {
  protected readonly containerProfile = "standard-4" as const;
  protected readonly workerRole = "executor" as const;
}
export class ValmeraAgent extends ValmeraContainer {
  protected readonly containerProfile = "standard-1" as const;
  protected readonly workerRole = "agent_executor" as const;
}
export class ValmeraMcp extends ValmeraContainer {
  protected readonly containerProfile = "standard-1" as const;
  protected readonly workerRole = "mcp_executor" as const;
}
export class ValmeraShorts extends ValmeraContainer {
  protected readonly containerProfile = "standard-1" as const;
  protected readonly workerRole = "shorts_executor" as const;
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
        source_version: env.SOURCE_VERSION ?? "unknown",
        shards: SHARD_COUNTS,
      });
    }
    if (!(await authorized(request, env.EXECUTOR_SECRET))) {
      // Authentication happens before a Durable Object is resolved, so no
      // named Container call can exist and Modal fallback is unambiguous.
      return json({ error: "unauthorized", safe_to_fallback: true }, 401);
    }
    const probeMatch = url.pathname.match(
      /^\/probes\/(interactive|batch|agent|mcp|shorts)$/,
    );
    if (request.method === "GET" && probeMatch) {
      const lane = probeMatch[1] as Lane;
      const namespaces = {
        interactive: env.INTERACTIVE,
        batch: env.BATCH,
        agent: env.AGENT,
        mcp: env.MCP,
        shorts: env.SHORTS,
      };
      return namespaces[lane]
        .getByName(`${lane}-deployment-probe`)
        .fetch("https://container.internal/probe");
    }
    const match = url.pathname.match(
      /^\/calls\/(interactive|batch|agent|mcp|shorts)\/([^/]+)$/,
    );
    if (!match || !CALL_ID.test(match[2])) {
      return json({ error: "not found", safe_to_fallback: true }, 404);
    }
    const [, rawLane, callId] = match;
    const lane = rawLane as Lane;
    const shard = shardName(lane, callId);
    const namespaces = {
      interactive: env.INTERACTIVE,
      batch: env.BATCH,
      agent: env.AGENT,
      mcp: env.MCP,
      shorts: env.SHORTS,
    };
    const stub = namespaces[lane].getByName(shard);
    if (request.method === "GET") {
      return stub.fetch(`https://container.internal/status/${callId}`);
    }
    if (request.method !== "POST") return json({ error: "method not allowed" }, 405);
    const body = (await request.json()) as { job?: ExecutorJob };
    const jobType = body.job?.type ?? "";
    const allowedByLane: Record<Lane, Set<string>> = {
      interactive: INTERACTIVE_TYPES,
      batch: BATCH_TYPES,
      agent: AGENT_TYPES,
      mcp: MCP_TYPES,
      shorts: SHORTS_TYPES,
    };
    const allowed = allowedByLane[lane];
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
