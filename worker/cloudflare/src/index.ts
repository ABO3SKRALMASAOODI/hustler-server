import { Container } from "@cloudflare/containers";

type JsonObject = Record<string, unknown>;

interface ExecutorJob extends JsonObject {
  id: number | null;
  type: string;
  project_id: number;
  total_claims: number | null;
  payload: JsonObject;
}

interface JobIdentity {
  id: number | null;
  type: string;
  project_id: number | null;
  total_claims: number | null;
}

interface CallState {
  status: "submitted" | "starting" | "running" | "unknown" | "stopping" | "done" | "failed";
  jobType: string;
  updatedAt: string;
  activeUntil: number;
  envelope?: JsonObject;
  error?: string;
  readOnlyRetry?: boolean;
  // Any MCP tool the dispatcher marked `mutation: false`. A lost read is
  // safely retryable; a lost mutation has an unknown outcome.
  readOnly?: boolean;
  disconnectedAt?: number;
  // Set while a dead-call abandonment is fencing the container, so a retry
  // after a failed destroy keeps the abandonment outcome.
  abandonReason?: string;
  // Queue identity of the claim that owns this call. A dispatcher refused
  // with "shard is busy" reads it to check whether the owner is still alive.
  job?: JobIdentity;
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
  | { kind: "busy"; activeCallId: string }
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
  CLOUDFLARE_EXECUTOR_URL?: string;
  CODE_VERSION?: string;
  SOURCE_VERSION?: string;
}

const INTERACTIVE_TYPES = new Set([
  "preview", "preview_check", "filmstrip", "frames", "mcp_media",
]);
const BATCH_TYPES = new Set([
  "index", "final", "capture", "track", "matte", "smatch", "clean",
  "stems", "fetch", "search", "stock_acquire", "ytprobe", "faces",
]);
const AGENT_TYPES = new Set(["agent_turn"]);
const MCP_TYPES = new Set(["mcp_tool"]);
const SHORTS_TYPES = new Set(["shorts_plan"]);
const SYNCHRONOUS_TYPES = new Set([
  "capture", "frames", "track", "matte", "smatch", "clean", "stems",
  "fetch", "search", "stock_acquire", "ytprobe", "mcp_media", "faces",
]);
const CALL_ID = /^[a-zA-Z0-9_-]{8,96}$/;
const SHARD_COUNTS = {
  interactive: 20, batch: 8, agent: 5, mcp: 20, shorts: 8,
} as const;
// A follow's face track (`faces`, Oct 2026) is a hedge the MCP and agent
// lanes race against their own measurement, and every face-following card
// or crop can fire one. A batch shard runs one call at a time, so these may
// hold at most this many of the batch shards (the last ones): a burst of
// parallel editors never crowds index, final or the other synchronous media
// tools off the lane. A busy pair only leaves the caller its own pass.
const FACES_SHARDS = 2;
type Lane = keyof typeof SHARD_COUNTS;
const TERMINAL_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;
// startAndWaitForPorts normally returns inside its 120-second port-ready
// bound. A Worker version replacement can instead abandon the handler after
// it persisted `starting`. No Python /run can precede the awaited transition
// to `running`, so this state is provably safe to fail over after three
// minutes rather than occupying the shard for the call's whole executor
// lease (30 minutes for an MCP tool, up to six hours for renders and index).
const STARTING_STALE_MS = 180 * 1000;
// A dispatcher may ask to abandon an `unknown` call only after it has proved,
// from PostgreSQL, that the executor stopped heartbeating or finished. The
// Durable Object independently refuses until the call has been disconnected
// for at least this long, so a brief transport blip is never treated as death.
const ABANDON_MIN_DISCONNECTED_MS = 120 * 1000;
// Model think time between MCP calls is p50 11 s and p90 198 s; previews of
// one project arrive minutes apart. Sixty seconds of idle grace destroyed
// these containers (and their ToolContext/proxy caches, ~180 MB per session)
// between almost every pair of calls. Keep them for four minutes. Idle
// standard-1 costs ~$0.0019 and standard-3 ~$0.0038 per extra 180 s of
// provisioned memory and disk (CPU is billed only when used), against a
// ~8 s cold start plus an 18-23 s proxy re-download on the next call.
// Batch, Studio-agent and Shorts lanes keep the 60-second default.
const MODEL_GAP_SLEEP_AFTER = "240s";
// Job types whose lost executor leaves an unknown outcome. A Shorts plan is
// not one: run_shorts_plan resumes its own saved clips on a re-claim and
// adopts children through materialization keys, so a retry is safe (and is
// what an in-process worker death already gets).
const MUTATING_JOB_TYPES = new Set(["mcp_tool", "agent_turn"]);

function jobIdentity(job: ExecutorJob): JobIdentity {
  const integer = (value: unknown): number | null =>
    Number.isInteger(value) ? value as number : null;
  return {
    id: integer(job.id), type: job.type,
    project_id: integer(job.project_id), total_claims: integer(job.total_claims),
  };
}

function retryAttempts(jobType: string): number {
  // The dispatcher requeues a retryable failure only while attempts <
  // max_attempts, but the MCP and Studio-agent lanes never claim a job again
  // after its first attempt (MAX_ATTEMPTS_MCP = MAX_ATTEMPTS_AGENT = 1). A
  // requeue there strands the row `queued` forever; their caller retries.
  return jobType === "mcp_tool" || jobType === "agent_turn" ? 1 : 2;
}

function abandonedEnvelope(state: CallState, reason: string): JsonObject {
  // The executor process is gone or provably finished, so nothing will ever
  // return this call's own envelope. Reads and deterministic media jobs are
  // safe to run again. A tool, turn or plan that writes project state may or
  // may not have committed before the executor was lost.
  const readOnly = state.readOnly === true || state.readOnlyRetry === true;
  if (MUTATING_JOB_TYPES.has(state.jobType) && !readOnly) {
    const error = `Cloudflare lost the executor during this ${state.jobType} `
      + `(${reason}). Its outcome is unknown: it may or may not have changed the `
      + "project. Re-read the current project state before retrying.";
    return { error, retryable: false, failure: {
      kind: "outcome_unknown", retryable: false, max_attempts: 0,
      agent_repairable: false,
    } };
  }
  const error = `Cloudflare lost the executor during this ${state.jobType} `
    + `(${reason}). No result was recorded; it is safe to retry.`;
  return { error, retryable: true, failure: {
    kind: "transient_infrastructure", retryable: true,
    max_attempts: retryAttempts(state.jobType), agent_repairable: false,
  } };
}

function shardName(lane: Lane, callId: string): string {
  // Every novel Container ID cold-starts. A fixed pool reuses Python images
  // and their bounded immutable source cache, while this deterministic hash
  // lets any later status request find the same durable call record.
  // MCP ToolContext contains project-scoped caches that deliberately survive
  // from one tool call to the next. New call ids carry an explicit project
  // routing key; legacy ids retain their original placement so an in-flight
  // call remains observable across this rollout.
  const mcpProject = lane === "mcp"
    ? callId.match(/^cf-mcp-p([0-9]+)-/)
    : null;
  const previewProject = lane === "interactive"
    ? callId.match(/^cf-preview-p([0-9]+)-/)
    : null;
  const renderGroup = (lane === "batch" || lane === "interactive")
    ? callId.match(/^cf-render-g([0-9]+-[01])-/) : null;
  const routingKey = renderGroup ? `render-group:${renderGroup[1]}` : mcpProject ? `project:${mcpProject[1]}`
    : previewProject ? `project:${previewProject[1]}` : callId;
  let hash = 2166136261;
  for (let i = 0; i < routingKey.length; i += 1) {
    hash ^= routingKey.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  if (lane === "batch" && callId.startsWith("cf-faces-")) {
    return `${lane}-${SHARD_COUNTS.batch - 1 - ((hash >>> 0) % FACES_SHARDS)}`;
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

async function matchesCompletedJob(callId: string, job: ExecutorJob): Promise<boolean> {
  if (!Number.isInteger(job.id) || !Number.isInteger(job.total_claims)
      || !Number.isInteger(job.project_id)) return false;
  const raw = `${job.type}:${job.id}:${job.total_claims}`;
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(raw));
  const digest = Array.from(new Uint8Array(bytes))
    .map((byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, 20);
  const prefix = job.type === "mcp_tool" ? `mcp-p${job.project_id}`
    : ["preview", "preview_check"].includes(job.type) ? `preview-p${job.project_id}`
    : job.type.slice(0, 18);
  // Earlier preview calls did not include the project routing key. Their
  // deterministic identity still proves the exact completed claim.
  // Shorts renders (every child preview/check and bulk shorts finals) carry a
  // render-group routing key instead. The group only selects the shard; the
  // digest of type:id:claims is the claim proof, so any well-formed group is
  // accepted (the call record itself lives only on the routed shard).
  return callId === `cf-${prefix}-${digest}`
    || callId === `cf-${job.type.slice(0, 18)}-${digest}`
    || (["final", "preview", "preview_check"].includes(job.type)
      && new RegExp(`^cf-render-g[0-9]+-[01]-${digest}$`).test(callId))
    || (["preview", "preview_check", "filmstrip", "mcp_tool"].includes(job.type)
      && [1, 2].some((slot) => callId ===
        `cf-alt${slot}-${job.type}-p${job.project_id}-${digest}`));
}

abstract class ValmeraContainer extends Container<Env> {
  defaultPort = 8080;
  sleepAfter = "60s";
  enableInternet = true;
  protected abstract readonly containerProfile: "standard-1" | "standard-3" | "standard-4";
  protected abstract readonly workerRole:
    "executor" | "agent_executor" | "mcp_executor" | "shorts_executor";
  // Call ids whose /run handler is still awaiting the container in THIS
  // Durable Object instance. Durable Object state survives a Worker code
  // update but in-flight handlers do not, so a persisted `running` call that
  // is absent here has lost the only code path that could record its result.
  private readonly liveRuns = new Set<string>();

  override async onActivityExpired(): Promise<void> {
    // Cloudflare's default hook sends SIGTERM.  The production Python
    // executors remained Running for 6-12 hours after completed calls even
    // though sleepAfter was 60s, continuing to bill provisioned memory and
    // disk.  A forced destroy is safe only when no durable provider lease can
    // still own work: an ambiguous disconnected /run keeps this row active
    // until its bounded deadline, while ordinary completions release it
    // before their response is returned.
    // Admission and idle destruction share a fence. A plain read followed by
    // destroy could kill a new /run admitted while the idle handler awaited.
    const resetId = `reset:idle-${crypto.randomUUID()}`;
    const reserved = await this.ctx.storage.transaction(async (txn) => {
      const active = await txn.get<ActiveCall>("active");
      if (active && active.expiresAt > Date.now()) return false;
      await txn.put("active", { callId: resetId, expiresAt: Date.now() + 120_000 });
      return true;
    });
    if (!reserved) { this.renewActivityTimeout(); return; }
    console.log("Idle timeout expired with no active provider lease; destroying container");
    await this.destroy();
    await this.release(resetId);
  }

  private environment(): Record<string, string> {
    const optional = (value: string | undefined): string => value ?? "";
    return {
      WORKER_ROLE: this.workerRole,
      EXECUTOR_PROVIDER: "cloudflare",
      CLOUDFLARE_CONTAINER_PROFILE: this.containerProfile,
      // Cost telemetry prices the configured idle tail after each call.
      CLOUDFLARE_IDLE_TAIL_S: String(parseInt(String(this.sleepAfter), 10) || 60),
      // Orchestration containers call synchronous media tools. Route every
      // child back through this Worker so Cloudflare remains the physical
      // compute owner all the way down the call tree.
      CLOUDFLARE_EXECUTOR_ENABLED: "1",
      CLOUDFLARE_EXECUTOR_PERCENT: "100",
      CLOUDFLARE_EXECUTOR_TYPES: [
        "preview", "preview_check", "final", "index", "filmstrip",
        "agent_turn", "mcp_tool", "shorts_plan", "capture", "frames",
        "track", "matte", "smatch", "clean", "stems", "fetch", "search",
        "stock_acquire", "ytprobe", "mcp_media", "faces",
      ].join(","),
      CLOUDFLARE_SYNCHRONOUS_TYPES: [
        "capture", "frames", "track", "matte", "smatch", "clean", "stems",
        "fetch", "search", "stock_acquire", "ytprobe", "mcp_media", "faces",
      ].join(","),
      CLOUDFLARE_EXECUTOR_URL: optional(this.env.CLOUDFLARE_EXECUTOR_URL),
      CLOUDFLARE_MODAL_FALLBACK: "0",
      CLOUDFLARE_MAX_SOURCE_DURATION_S: "0",
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
      MODAL_EXECUTOR_ENABLED: "0",
      MODAL_EXECUTOR_PERCENT: "0",
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
        error: `container readiness mismatch role=${String(body.role)} source=${String(body.code_version)} expected_role=${this.workerRole} expected_source=${expectedSource}`,
      };
    } catch (error) {
      return { ok: false, error: `container readiness failed: ${String(error)}` };
    }
  }

  private async retireUnreadyContainer(callId: string): Promise<void> {
    // stop() only signals SIGTERM in SDK 0.3.7. Await actual destruction of
    // a rejected image before admitting its replacement. Keep an exclusive
    // reset reservation throughout; never destroy another accepted /run.
    const resetId = `reset:${callId}`;
    const owns = await this.ctx.storage.transaction(async (txn) => {
      const state = await txn.get<CallState>(this.stateKey(callId));
      const active = await txn.get<ActiveCall>("active");
      if (state?.status !== "starting" || active?.callId !== callId) return false;
      await txn.put({
        [this.stateKey(callId)]: { ...state, status: "stopping" },
        active: { callId: resetId, expiresAt: Date.now() + 120_000 },
      });
      return true;
    });
    if (!owns) return;
    let destroyed = false;
    try { await this.destroy(); destroyed = true; }
    catch { /* Retain the reset lease until cleanup can be retried. */ }
    await this.ctx.storage.transaction(async (txn) => {
      const active = await txn.get<ActiveCall>("active");
      if (active?.callId !== resetId) return;
      await txn.delete(this.stateKey(callId));
      if (destroyed) await txn.delete("active");
    });
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
          failure: { kind: "provider_start_abandoned", retryable: true },
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
    // No /run was sent. Do not schedule an unfenced asynchronous stop after
    // releasing admission: it can kill the next customer's healthy call.
    // The next owner checks readiness; normal idle expiry reclaims unused VMs.
    return terminal;
  }

  private async expireExecutorLease(
    callId: string, now = Date.now(),
  ): Promise<CallState | null> {
    const observed = await this.callState(callId);
    if (!observed || ["done", "failed"].includes(observed.status)) return observed;
    if (observed.status === "stopping" && observed.abandonReason) {
      // A dead-call fence whose destroy failed: finish it with its own outcome.
      return this.fenceAndFail(callId, observed, now,
        (current) => current.error ?? `Cloudflare ${current.jobType} call abandoned`,
        (current) => abandonedEnvelope(current, current.abandonReason ?? "executor liveness lost"));
    }
    // `reason` set here is evidence the executor is gone (its container
    // stopped, or a read-only listen was fenced), not an exhausted budget:
    // it gets the same outcome as a dispatcher-proven dead call. Only a real
    // lease expiry keeps the render_budget_exceeded envelope.
    let reason = "";
    if (observed.status === "unknown") {
      // A disconnected read-only listen must not occupy a six-hour edit lane.
      // Stop its process before permitting a retry. Mutations keep their fence.
      if (observed.readOnlyRetry && now - (observed.disconnectedAt ?? now) >= 90_000) {
        reason = "Cloudflare read-only audio review lost its response; fenced recovery";
      } else {
        try {
          const physical = await this.getState();
          if (["stopped", "stopped_with_code"].includes(physical.status)) {
            reason = `Cloudflare container exited during ${observed.jobType}`;
          }
        } catch { /* Unobservable compute is not proof of a stopped process. */ }
      }
    }
    if (!reason && observed.activeUntil > now && observed.status !== "stopping") return observed;
    return this.fenceAndFail(callId, observed, now, (current) => {
      const prior = current.error ? ` (${current.error})` : "";
      return reason || `Cloudflare ${current.jobType} call exceeded its executor lease while ${current.status}${prior}`;
    }, (current, error, fenced) => {
      if (!fenced) return { error, retryable: false };
      // A deploy that drops a long render's handler and then replaces its
      // container is infrastructure loss: retry it, never tell the user the
      // render could not fit its processing limits.
      if (reason) return abandonedEnvelope(current, reason);
      const render = ["preview", "preview_check", "final"].includes(current.jobType);
      const retryable = !render && (current.jobType !== "mcp_tool" || current.readOnlyRetry === true);
      return { error, retryable, failure: {
        kind: render ? "render_budget_exceeded" : "transient_infrastructure",
        retryable, max_attempts: retryable ? retryAttempts(current.jobType) : 0,
        agent_repairable: false,
      } };
    });
  }

  private async fenceAndFail(
    callId: string,
    observed: CallState,
    now: number,
    errorFor: (current: CallState) => string,
    envelopeFor: (current: CallState, error: string, fenced: boolean) => JsonObject,
    mark: Partial<CallState> = {},
  ): Promise<CallState | null> {
    // Terminalize one call that can no longer return its own envelope. When
    // it still owns the shard, destroy the container first: a wedged or
    // zombie Python process must never commit after the shard is reused.
    const state = await this.ctx.storage.transaction(async (txn) => {
      const key = this.stateKey(callId);
      const current = (await txn.get<CallState>(key)) ?? null;
      if (!current || ["done", "failed"].includes(current.status)) return current;
      if (current.updatedAt !== observed.updatedAt) return current;
      const error = errorFor(current);
      const active = await txn.get<ActiveCall>("active");
      // An old status poll must never stop a newer customer's container.
      if (active?.callId !== callId && active?.callId !== `reset:${callId}`) {
        const failed = { ...current, status: "failed" as const, error,
          envelope: envelopeFor(current, error, false), activeUntil: now,
          updatedAt: new Date(now).toISOString() };
        await txn.put({ [key]: failed, [this.terminalKey(callId, now)]: callId });
        return failed;
      }
      const stopping: CallState = { ...current, ...mark, status: "stopping", error,
        updatedAt: new Date(now).toISOString(), activeUntil: now };
      await txn.put({ [key]: stopping,
        active: { callId: `reset:${callId}`, expiresAt: now + 120_000 } satisfies ActiveCall });
      return stopping;
    });
    if (state?.status !== "stopping") return state;
    try {
      // SDK stop() signals SIGTERM but does not await process exit. Destroy
      // completes the physical fence before another request can use this lane.
      await this.destroy();
    } catch (error) {
      const stopped = { ...state,
        error: `${state.error}; container stop failed: ${String(error)}`,
        updatedAt: new Date().toISOString() } satisfies CallState;
      await this.ctx.storage.put(this.stateKey(callId), stopped);
      return stopped;
    }
    const terminalAt = Date.now();
    return this.ctx.storage.transaction(async (txn) => {
      const key = this.stateKey(callId);
      const current = (await txn.get<CallState>(key)) ?? state;
      if (current.status !== "stopping") {
        const active = await txn.get<ActiveCall>("active");
        if (active?.callId === `reset:${callId}`) await txn.delete("active");
        return current;
      }
      const error = current.error ?? `Cloudflare ${current.jobType} call exceeded its executor lease`;
      const failed: CallState = { ...current, status: "failed", error,
        envelope: envelopeFor(current, error, true),
        updatedAt: new Date(terminalAt).toISOString(), activeUntil: terminalAt };
      const active = await txn.get<ActiveCall>("active");
      await txn.put({ [key]: failed, [this.terminalKey(callId, terminalAt)]: callId });
      if (active?.callId === `reset:${callId}`) await txn.delete("active");
      return failed;
    });
  }

  private async noteLostHandler(
    callId: string, now = Date.now(),
  ): Promise<CallState | null> {
    // `running` means a handler is awaiting the container's /run response.
    // When no such handler exists in this instance (a Worker deploy replaced
    // it), that response can never be stored. The process may still be
    // working and committing through PostgreSQL, so this is `unknown`, not
    // failed: the shard stays fenced until completion, liveness or lease.
    if (this.liveRuns.has(callId)) return this.callState(callId);
    return this.ctx.storage.transaction(async (txn) => {
      const key = this.stateKey(callId);
      const current = (await txn.get<CallState>(key)) ?? null;
      if (current?.status !== "running" || this.liveRuns.has(callId)) return current;
      const lost: CallState = {
        ...current, status: "unknown",
        disconnectedAt: current.disconnectedAt ?? now,
        error: "the Durable Object handler awaiting /run was lost (Worker restart)",
        updatedAt: new Date(now).toISOString(),
      };
      await txn.put(key, lost);
      return lost;
    });
  }

  private async abandonDeadCall(
    callId: string, job: ExecutorJob, reason: string, now = Date.now(),
  ): Promise<{ accepted: boolean; state: CallState | null }> {
    // The authenticated dispatcher proved from PostgreSQL that this exact
    // claim's executor stopped heartbeating, finished, or lost its lease.
    // Only an `unknown` call (no handler can ever store its envelope) that
    // has stayed disconnected past the floor may be abandoned.
    if (!await matchesCompletedJob(callId, job)) return { accepted: false, state: null };
    let observed = await this.expireStaleStart(callId, now);
    if (observed?.status === "running") observed = await this.noteLostHandler(callId, now);
    if (!observed || observed.jobType !== job.type) return { accepted: false, state: observed };
    if (observed.status === "done" || observed.status === "failed") {
      return { accepted: true, state: observed };
    }
    if (observed.status !== "unknown"
        || now - (observed.disconnectedAt ?? now) < ABANDON_MIN_DISCONNECTED_MS) {
      return { accepted: false, state: observed };
    }
    const detail = reason.replace(/[^\x20-\x7e]/g, " ").slice(0, 160) || "executor liveness lost";
    const state = await this.fenceAndFail(callId, observed, now,
      () => `Cloudflare ${observed.jobType} call abandoned: ${detail}`,
      (current) => abandonedEnvelope(current, detail), { abandonReason: detail });
    return { accepted: state?.status === "failed", state };
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
        ...current, status: "running", jobType,
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
    await this.ctx.storage.transaction(async (txn) => {
      const current = await txn.get<CallState>(this.stateKey(callId));
      // A late disconnected handler must not replace a durable success
      // acknowledged by the dispatcher with a transport error.
      if (current?.status !== "done") {
        await txn.put({
          [this.stateKey(callId)]: state,
          [this.terminalKey(callId, at)]: callId,
        });
      }
      const active = await txn.get<ActiveCall>("active");
      if (active?.callId === callId) await txn.delete("active");
    });
  }

  private async acknowledgeCompleted(
    callId: string, job: ExecutorJob, envelope: JsonObject,
  ): Promise<boolean> {
    if (!await matchesCompletedJob(callId, job)
        || envelope.job_completed !== true || envelope.error) return false;
    const terminalAt = Date.now();
    return this.ctx.storage.transaction(async (txn) => {
      const key = this.stateKey(callId);
      const current = await txn.get<CallState>(key);
      if (!current || current.jobType !== job.type) return false;
      const active = await txn.get<ActiveCall>("active");
      if (current.status === "done") {
        if (active?.callId === callId) await txn.delete("active");
        return true;
      }
      if (!["running", "unknown"].includes(current.status)
          || active?.callId !== callId) return false;
      // The authenticated dispatcher checked PostgreSQL's completed queue
      // and provider records for this exact claim. Work is already finished;
      // retain the warm container and atomically close only its reservation.
      await txn.put({
        [key]: {
          status: "done", jobType: job.type, envelope,
          updatedAt: new Date(terminalAt).toISOString(), activeUntil: terminalAt,
        } satisfies CallState,
        [this.terminalKey(callId, terminalAt)]: callId,
      });
      await txn.delete("active");
      return true;
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
    identity?: JobIdentity,
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
      if (active && active.expiresAt > now) {
        return { kind: "busy", activeCallId: active.callId };
      }
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
        ...(identity ? { job: identity } : {}),
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
      let state = await this.expireStaleStart(statusMatch[1]);
      if (state?.status === "running") state = await this.noteLostHandler(statusMatch[1]);
      if (state?.status !== "done" && state?.status !== "failed") {
        state = await this.expireExecutorLease(statusMatch[1]);
      }
      return state ? json(state) : json({ status: "missing" }, 404);
    }
    const abandonMatch = url.pathname.match(/^\/abandon\/([^/]+)$/);
    if (request.method === "POST" && abandonMatch && CALL_ID.test(abandonMatch[1])) {
      const body = await request.json() as { job?: ExecutorJob; reason?: string };
      const outcome = body.job
        ? await this.abandonDeadCall(abandonMatch[1], body.job, String(body.reason ?? ""))
        : { accepted: false, state: null };
      return json({ ...(outcome.state ?? { status: "missing" }), abandoned: outcome.accepted },
        outcome.accepted ? 200 : 409);
    }
    const completeMatch = url.pathname.match(/^\/complete\/([^/]+)$/);
    if (request.method === "POST" && completeMatch && CALL_ID.test(completeMatch[1])) {
      const body = await request.json() as { job?: ExecutorJob; envelope?: JsonObject };
      const acknowledged = body.job && body.envelope && await this.acknowledgeCompleted(
        completeMatch[1], body.job, body.envelope,
      );
      return json({ acknowledged: !!acknowledged }, acknowledged ? 200 : 409);
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
      && SYNCHRONOUS_TYPES.has(job?.type ?? "");
    if (!job || (!queueBacked && !synchronous)) {
      return json({ error: "invalid executor job", safe_to_fallback: true }, 400);
    }
    const now = Date.now();
    const requestedTimeout = Number(body.timeout_s ?? 3600);
    const timeoutSeconds = Number.isFinite(requestedTimeout)
      ? Math.max(60, Math.min(21660, requestedTimeout))
      : 3600;
    const activeUntil = now + timeoutSeconds * 1000;
    const identity = jobIdentity(job);
    let reservation = await this.reserve(
      callId, job.type, now, activeUntil, identity,
    );
    if (reservation.kind === "reset") {
      // The prior dispatch lease has expired. Its /run may still occupy this
      // shared container after an ambiguous Worker disconnect; starting a new
      // ffmpeg beside it would exceed the instance shape. Stop the shard first
      // and cold-restart it for the next call. No new /run has been sent yet,
      // so a stop failure remains safe to handle on Modal.
      try {
        await this.destroy();
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
        callId, job.type, Date.now(), activeUntil, identity,
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
        active_call_id: reservation.kind === "busy" ? reservation.activeCallId : undefined,
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
        readOnlyRetry: job.type === "mcp_tool" && job.payload?.tool === "review_audio" && job.payload?.mutation === false,
        readOnly: job.type === "mcp_tool" && job.payload?.mutation === false,
        job: identity,
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
      await this.retireUnreadyContainer(callId);
      return json({
        error: readiness.error ?? "Cloudflare container image is not ready",
        safe_to_fallback: true,
      }, 503);
    }

    // Track this handler before `running` is persisted, so a status read in
    // this instance can never mistake a live /run for a lost one.
    this.liveRuns.add(callId);
    try {
      return await this.runAccepted(callId, job, activeUntil);
    } finally {
      this.liveRuns.delete(callId);
    }
  }

  private async runAccepted(
    callId: string, job: ExecutorJob, activeUntil: number,
  ): Promise<Response> {
    const stateKey = this.stateKey(callId);
    // Atomically prove the startup still owns the reservation. A status
    // request may have terminalized an abandoned start while the provider API
    // was returning; that late handler must never send /run after fallback.
    const running = await this.markRunning(callId, job.type, activeUntil);
    if (running?.status !== "running") {
      return json(running?.envelope ?? {
        error: running?.error ?? "Cloudflare startup no longer owns this call",
        retryable: true,
        failure: { kind: "provider_start_abandoned", retryable: true },
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
      const body = await response.text();
      let envelope: JsonObject;
      try { envelope = JSON.parse(body) as JsonObject; }
      catch { throw new Error(`Container /run HTTP ${response.status} returned non-JSON: ${body.slice(0, 240)}`); }
      if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)) {
        throw new Error(`Container /run HTTP ${response.status} returned an invalid envelope`);
      }
      const status = envelope.error ? "failed" : "done";
      const terminalAt = Date.now();
      await this.storeTerminal(callId, {
        status, jobType: job.type, envelope,
        updatedAt: new Date(terminalAt).toISOString(), activeUntil,
      }, terminalAt);
      this.ctx.waitUntil(this.pruneTerminalCalls(terminalAt));
      return json(envelope);
    } catch (error) {
      // Once /run was sent, a lost Worker-side connection is ambiguous: the
      // Python process may still be encoding and will commit through Postgres.
      // Keep the named call recoverable; never authorize a second provider.
      const failedAt = Date.now();
      const keptActive = await this.ctx.storage.transaction(async (txn) => {
        const current = await txn.get<CallState>(stateKey);
        if (current?.status === "done" || current?.status === "failed" || current?.status === "stopping") return true;
        const active = await txn.get<ActiveCall>("active");
        if (active?.callId !== callId) return false;
        await txn.put(stateKey, {
          ...current, status: "unknown",
          disconnectedAt: current?.disconnectedAt ?? failedAt,
          jobType: job.type,
          error: String(error),
          updatedAt: new Date(failedAt).toISOString(),
          activeUntil,
        } satisfies CallState);
        return true;
      });
      if (!keptActive) {
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
  override sleepAfter = MODEL_GAP_SLEEP_AFTER;
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
  override sleepAfter = MODEL_GAP_SLEEP_AFTER;
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
        // Dispatcher feature gates consult the executor's root /health
        // contract without starting a billed Container. custom_filter is in
        // every image; stems is guaranteed by the FULL_COMPUTE batch build
        // (the image build fails if Demucs or its weights cannot be baked).
        features: ["custom_filter", "stems"],
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
      /^\/calls\/(interactive|batch|agent|mcp|shorts)\/([^/]+)(\/complete|\/abandon)?$/,
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
    const action = match[3] ? match[3].slice(1) : "";
    if (request.method === "GET" && !action) {
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
    return stub.fetch(`https://container.internal/${action || "execute"}/${callId}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  },
} satisfies ExportedHandler<Env>;
