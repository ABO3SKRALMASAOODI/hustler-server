const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto, createHash } = require('node:crypto');
const ts = require('typescript');

// Dead-call detection and lane idle policy, executed against the real adapter
// with transactional in-memory Durable Object storage (same harness as
// reservation.test.cjs). Container destruction throws unless a test installs
// an explicit fence check.
const code = ts.transpileModule(fs.readFileSync(require.resolve('../src/index.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const exportsObject = {};
class Container {
  constructor(ctx, env) { this.ctx = ctx; this.env = env; }
  stop() { throw new Error('must not stop compute'); }
  destroy() { throw new Error('must not destroy compute'); }
}
vm.runInNewContext(code, {
  exports: exportsObject, crypto: webcrypto, TextEncoder, Request, Response, URL,
  console, require: (name) => {
    assert.equal(name, '@cloudflare/containers'); return { Container };
  },
});

const MINUTE = 60 * 1000;
function identity(job) {
  const digest = createHash('sha256')
    .update(`${job.type}:${job.id}:${job.total_claims}`).digest('hex').slice(0, 20);
  const prefix = job.type === 'mcp_tool' ? `mcp-p${job.project_id}`
    : ['preview', 'preview_check'].includes(job.type) ? `preview-p${job.project_id}`
    : job.type.slice(0, 18);
  return `cf-${prefix}-${digest}`;
}
const mutation = { id: 58049, total_claims: 1, project_id: 3177, type: 'mcp_tool' };
const mutationId = identity(mutation);

function fixture({ job = mutation, callId = identity(job), status = 'unknown',
  extra = {}, cls = 'ValmeraMcp', active = callId } = {}) {
  const values = new Map([
    [`call:${callId}`, { status, jobType: job.type, activeUntil: Date.now() + 30 * MINUTE,
      updatedAt: '2026-10-09T00:00:00.000Z', ...extra }],
    ['active', { callId: active, expiresAt: Date.now() + 30 * MINUTE }],
  ]);
  const api = (map) => ({
    get: async (key) => structuredClone(map.get(key)),
    put: async (key, value) => {
      if (typeof key === 'string') map.set(key, structuredClone(value));
      else for (const [k, v] of Object.entries(key)) map.set(k, structuredClone(v));
    },
    delete: async (key) => map.delete(key),
  });
  const storage = api(values);
  let tail = Promise.resolve();
  storage.transaction = (fn) => {
    const pending = tail.then(async () => {
      const copy = new Map(values);
      const result = await fn(api(copy));
      values.clear(); for (const [k, v] of copy) values.set(k, v);
      return result;
    });
    tail = pending.catch(() => {}); return pending;
  };
  return { values, callId, adapter: new exportsObject[cls]({ storage }, {}) };
}

function abandon(adapter, callId, job, reason = 'no executor heartbeat for 180s') {
  return adapter.fetch(new Request(`https://container.internal/abandon/${callId}`, {
    method: 'POST', body: JSON.stringify({ job, reason }),
  }));
}

function status(adapter, callId) {
  return adapter.fetch(new Request(`https://container.internal/status/${callId}`));
}

test('a running call whose handler was lost to a Worker restart becomes unknown, still fenced', async () => {
  const { adapter, values, callId } = fixture({ status: 'running' });
  adapter.getState = async () => ({ status: 'healthy' });
  const before = Date.now();
  const body = await (await status(adapter, callId)).json();
  assert.equal(body.status, 'unknown');
  assert.ok(body.disconnectedAt >= before);
  assert.match(body.error, /handler awaiting \/run was lost/);
  assert.equal(values.get('active').callId, callId);
  assert.equal((await adapter.reserve('cf-next-identity', 'mcp_tool', Date.now(), Date.now() + MINUTE)).kind, 'busy');
});

test('a running call whose handler is alive in this instance is left running', async () => {
  const { adapter, values, callId } = fixture({ status: 'running' });
  adapter.liveRuns.add(callId);
  const body = await (await status(adapter, callId)).json();
  assert.equal(body.status, 'running');
  assert.equal(values.get(`call:${callId}`).disconnectedAt, undefined);
});

test('abandonment requires proof of the exact claim and never touches compute otherwise', async () => {
  for (const changed of [{ total_claims: 2 }, { id: 58050 }, { project_id: 9 }, { type: 'preview' }]) {
    const { adapter, values, callId } = fixture({ extra: { disconnectedAt: Date.now() - 10 * MINUTE } });
    const response = await abandon(adapter, callId, { ...mutation, ...changed });
    assert.equal(response.status, 409);
    assert.equal(values.get(`call:${callId}`).status, 'unknown');
    assert.equal(values.get('active').callId, callId);
  }
});

test('a briefly disconnected call is not abandoned before the floor', async () => {
  const { adapter, values, callId } = fixture({ extra: { disconnectedAt: Date.now() - 30 * 1000 } });
  const response = await abandon(adapter, callId, mutation);
  assert.equal(response.status, 409);
  assert.equal((await response.json()).abandoned, false);
  assert.equal(values.get('active').callId, callId);
});

test('a live /run handler is never abandoned', async () => {
  const { adapter, values, callId } = fixture({ status: 'running',
    extra: { disconnectedAt: Date.now() - 10 * MINUTE } });
  adapter.liveRuns.add(callId);
  const response = await abandon(adapter, callId, mutation);
  assert.equal(response.status, 409);
  assert.equal(values.get(`call:${callId}`).status, 'running');
  assert.equal(values.get('active').callId, callId);
});

test('a dead mutation is fenced, destroyed, released and reported as outcome unknown', async () => {
  const { adapter, values, callId } = fixture({ extra: { disconnectedAt: Date.now() - 5 * MINUTE } });
  let destroyed = false;
  adapter.destroy = async () => {
    // The shard stays reserved under a reset lease until destruction ends.
    assert.equal(values.get(`call:${callId}`).status, 'stopping');
    assert.equal(values.get('active').callId, `reset:${callId}`);
    assert.equal((await adapter.reserve('cf-next-identity', 'mcp_tool', Date.now(), Date.now() + MINUTE)).kind, 'busy');
    destroyed = true;
  };
  const response = await abandon(adapter, callId, mutation);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(destroyed, true);
  assert.equal(body.abandoned, true);
  assert.equal(body.status, 'failed');
  assert.equal(body.envelope.retryable, false);
  assert.equal(body.envelope.failure.kind, 'outcome_unknown');
  assert.equal(body.envelope.failure.max_attempts, 0);
  assert.match(body.envelope.error, /outcome is unknown/);
  assert.match(body.envelope.error, /Re-read the current project state/);
  assert.match(body.envelope.error, /no executor heartbeat for 180s/);
  assert.equal(values.has('active'), false);
  assert.equal((await adapter.reserve('cf-next-identity', 'mcp_tool', Date.now(), Date.now() + MINUTE)).kind, 'reserved');
  // Repeating the request returns the same terminal outcome without new work.
  adapter.destroy = async () => assert.fail('terminal call must not be destroyed twice');
  assert.equal((await abandon(adapter, callId, mutation)).status, 200);
});

test('dead reads and deterministic media jobs are retryable', async () => {
  const cases = [
    // An MCP read is retried by its caller: the MCP lane never re-claims.
    { job: mutation, extra: { readOnly: true }, attempts: 1 },
    { job: { ...mutation, type: 'preview' }, extra: {}, cls: 'ValmeraInteractive', attempts: 2 },
    { job: { ...mutation, type: 'index' }, extra: {}, cls: 'ValmeraBatch', attempts: 2 },
  ];
  for (const { job, extra, cls, attempts } of cases) {
    const { adapter, values, callId } = fixture({ job, cls,
      extra: { disconnectedAt: Date.now() - 5 * MINUTE, ...extra } });
    adapter.destroy = async () => {};
    const body = await (await abandon(adapter, callId, job)).json();
    assert.equal(body.status, 'failed', job.type);
    assert.equal(body.envelope.retryable, true);
    assert.equal(body.envelope.failure.kind, 'transient_infrastructure');
    assert.equal(body.envelope.failure.max_attempts, attempts);
    assert.match(body.envelope.error, /safe to retry/);
    assert.equal(values.has('active'), false);
  }
});

test('expired retryable leases never ask a one-attempt lane to requeue', async () => {
  for (const [type, cls, attempts] of [['agent_turn', 'ValmeraAgent', 1],
    ['shorts_plan', 'ValmeraShorts', 2], ['index', 'ValmeraBatch', 2]]) {
    const job = { ...mutation, type };
    const { adapter, callId } = fixture({ job, cls, status: 'running',
      extra: { activeUntil: Date.now() - 1 } });
    adapter.liveRuns.add(callId);
    adapter.destroy = async () => {};
    const result = await adapter.expireExecutorLease(callId);
    assert.equal(result.envelope.retryable, true, type);
    assert.equal(result.envelope.failure.max_attempts, attempts, type);
  }
});

test('Studio turns are mutations; resumable Shorts plans retry', async () => {
  for (const [type, cls, kind, attempts] of [
    ['agent_turn', 'ValmeraAgent', 'outcome_unknown', 0],
    // run_shorts_plan resumes its saved clips and adopts children by
    // materialization key on a re-claim, so a lost plan is safe to re-run.
    ['shorts_plan', 'ValmeraShorts', 'transient_infrastructure', 2],
  ]) {
    const job = { ...mutation, type };
    const { adapter, callId } = fixture({ job, cls, extra: { disconnectedAt: Date.now() - 5 * MINUTE } });
    adapter.destroy = async () => {};
    const body = await (await abandon(adapter, callId, job)).json();
    assert.equal(body.envelope.failure.kind, kind, type);
    assert.equal(body.envelope.failure.max_attempts, attempts, type);
    assert.equal(body.envelope.retryable, attempts > 0, type);
  }
});

// Shorts renders route by render group: every child preview/preview_check
// (agent_tools._child_payload) and bulk shorts finals (backend video.py).
function renderGroupId(job, group = '3900-0') {
  const digest = createHash('sha256')
    .update(`${job.type}:${job.id}:${job.total_claims}`).digest('hex').slice(0, 20);
  return `cf-render-g${group}-${digest}`;
}

test('dead render-group shorts renders are abandoned and release their shard', async () => {
  for (const [type, cls, group] of [['final', 'ValmeraBatch', '3900-0'],
    ['preview', 'ValmeraInteractive', '3900-1'], ['preview_check', 'ValmeraInteractive', '12-0']]) {
    const job = { id: 61000, total_claims: 1, project_id: 4000, type };
    const callId = renderGroupId(job, group);
    const { adapter, values } = fixture({ job, callId, cls,
      extra: { disconnectedAt: Date.now() - 5 * MINUTE } });
    let destroyed = false;
    adapter.destroy = async () => {
      assert.equal(values.get('active').callId, `reset:${callId}`);
      destroyed = true;
    };
    const response = await abandon(adapter, callId, job);
    assert.equal(response.status, 200, type);
    const body = await response.json();
    assert.equal(destroyed, true, type);
    assert.equal(body.abandoned, true);
    assert.equal(body.envelope.retryable, true);
    assert.equal(body.envelope.failure.kind, 'transient_infrastructure');
    assert.equal(body.envelope.failure.max_attempts, 2);
    assert.equal(values.has('active'), false, type);
    assert.equal((await adapter.reserve('cf-next-identity', type, Date.now(), Date.now() + MINUTE)).kind,
      'reserved', type);
  }
});

test('render-group identity still proves the exact claim', async () => {
  const job = { id: 61000, total_claims: 1, project_id: 4000, type: 'final' };
  const callId = renderGroupId(job);
  for (const changed of [{ total_claims: 2 }, { id: 61001 }, { type: 'preview' },
    { type: 'mcp_tool' }, { type: 'index' }]) {
    const { adapter, values } = fixture({ job, callId, cls: 'ValmeraBatch',
      extra: { disconnectedAt: Date.now() - 10 * MINUTE } });
    adapter.destroy = async () => assert.fail('a different claim must not destroy compute');
    const response = await abandon(adapter, callId, { ...job, ...changed });
    assert.equal(response.status, 409, JSON.stringify(changed));
    assert.equal(values.get('active').callId, callId);
  }
  // Malformed groups never match, even with the right digest.
  for (const bad of ['cf-render-g3900-2-', 'cf-render-gx-0-', 'cf-render-g-0-']) {
    const digest = callId.slice(callId.lastIndexOf('-') + 1);
    const { adapter } = fixture({ job, callId: `${bad}${digest}`, cls: 'ValmeraBatch',
      extra: { disconnectedAt: Date.now() - 10 * MINUTE } });
    adapter.destroy = async () => assert.fail('malformed identity must not destroy compute');
    assert.equal((await abandon(adapter, `${bad}${digest}`, job)).status, 409, bad);
  }
});

test('a render-group call whose response was lost is acknowledged by /complete', async () => {
  const job = { id: 61002, total_claims: 1, project_id: 4000, type: 'preview' };
  const callId = renderGroupId(job, '3900-1');
  const { adapter, values } = fixture({ job, callId, cls: 'ValmeraInteractive',
    extra: { disconnectedAt: Date.now() - MINUTE } });
  const response = await adapter.fetch(new Request(`https://container.internal/complete/${callId}`, {
    method: 'POST', body: JSON.stringify({ job, envelope: { job_completed: true, result: {} } }),
  }));
  assert.equal(response.status, 200);
  assert.equal(values.get(`call:${callId}`).status, 'done');
  assert.equal(values.has('active'), false);
});

test('a deploy-lost render whose container was replaced is retried, not budget-exceeded', async () => {
  for (const [type, cls] of [['final', 'ValmeraBatch'], ['preview', 'ValmeraInteractive'],
    ['preview_check', 'ValmeraInteractive']]) {
    const job = { ...mutation, type };
    // The Worker deploy dropped the handler; the 300 s rollout grace then
    // replaced the container, so it is observed stopped.
    const { adapter, values, callId } = fixture({ job, cls, status: 'running' });
    adapter.getState = async () => ({ status: 'stopped' });
    adapter.destroy = async () => {};
    const body = await (await status(adapter, callId)).json();
    assert.equal(body.status, 'failed', type);
    assert.match(body.error, /container exited during/);
    assert.equal(body.envelope.retryable, true, type);
    assert.equal(body.envelope.failure.kind, 'transient_infrastructure', type);
    assert.equal(body.envelope.failure.max_attempts, 2, type);
    assert.match(body.envelope.error, /safe to retry/);
    assert.equal(values.has('active'), false);
  }
});

test('a stopped container during an MCP mutation reports outcome unknown', async () => {
  const { adapter, callId } = fixture({ extra: { disconnectedAt: Date.now() - MINUTE } });
  adapter.getState = async () => ({ status: 'stopped_with_code' });
  adapter.destroy = async () => {};
  const body = await (await status(adapter, callId)).json();
  assert.equal(body.status, 'failed');
  assert.equal(body.envelope.failure.kind, 'outcome_unknown');
  assert.equal(body.envelope.retryable, false);
});

test('a real render lease expiry still reports the exhausted processing budget', async () => {
  const job = { ...mutation, type: 'final' };
  const { adapter, callId } = fixture({ job, cls: 'ValmeraBatch', status: 'running',
    extra: { activeUntil: Date.now() - 1 } });
  adapter.liveRuns.add(callId);
  adapter.destroy = async () => {};
  const result = await adapter.expireExecutorLease(callId);
  assert.equal(result.envelope.retryable, false);
  assert.equal(result.envelope.failure.kind, 'render_budget_exceeded');
  assert.match(result.envelope.error, /exceeded its executor lease/);
});

test('a lost running handler becomes abandonable only after the floor', async () => {
  const { adapter, values, callId } = fixture({ status: 'running' });
  adapter.destroy = async () => {};
  const t0 = Date.now();
  assert.equal((await adapter.abandonDeadCall(callId, mutation, 'stale', t0)).accepted, false);
  assert.equal(values.get(`call:${callId}`).status, 'unknown');
  assert.equal(values.get(`call:${callId}`).disconnectedAt, t0);
  const later = await adapter.abandonDeadCall(callId, mutation, 'stale', t0 + 2 * MINUTE + 1);
  assert.equal(later.accepted, true);
  assert.equal(later.state.envelope.failure.kind, 'outcome_unknown');
  assert.equal(values.has('active'), false);
});

test('a failed destroy keeps the reset fence and the abandonment outcome', async () => {
  const { adapter, values, callId } = fixture({ extra: { disconnectedAt: Date.now() - 5 * MINUTE } });
  adapter.destroy = async () => { throw new Error('provider unavailable'); };
  const first = await abandon(adapter, callId, mutation);
  assert.equal(first.status, 409);
  assert.equal(values.get(`call:${callId}`).status, 'stopping');
  assert.equal(values.get('active').callId, `reset:${callId}`);
  // The next status poll retries the fence and keeps the same outcome.
  adapter.destroy = async () => {};
  adapter.getState = async () => ({ status: 'healthy' });
  const body = await (await status(adapter, callId)).json();
  assert.equal(body.status, 'failed');
  assert.equal(body.envelope.failure.kind, 'outcome_unknown');
  assert.equal(values.has('active'), false);
});

test('an abandoned call that no longer owns the shard never destroys the new owner', async () => {
  const { adapter, values, callId } = fixture({ active: 'cf-new-customer',
    extra: { disconnectedAt: Date.now() - 5 * MINUTE } });
  adapter.destroy = async () => assert.fail('must not destroy another accepted call');
  const body = await (await abandon(adapter, callId, mutation)).json();
  assert.equal(body.status, 'failed');
  assert.equal(body.envelope.failure.kind, 'outcome_unknown');
  assert.equal(values.get('active').callId, 'cf-new-customer');
});

test('the busy owner exposes its queue identity for liveness checks', async () => {
  const { adapter, values } = fixture();
  values.delete('active');
  const job = { id: 7, total_claims: 3, project_id: 11, type: 'mcp_tool' };
  assert.equal((await adapter.reserve('cf-owner-identity', 'mcp_tool', Date.now(), Date.now() + MINUTE,
    { id: 7, type: 'mcp_tool', project_id: 11, total_claims: 3 })).kind, 'reserved');
  const body = await (await status(adapter, 'cf-owner-identity')).json();
  assert.deepEqual(body.job, job);
});

test('abandon API is authenticated and routed to the named shard', async () => {
  const unauthenticated = await exportsObject.default.fetch(new Request(
    `https://executor.example/calls/mcp/${mutationId}/abandon`, {
      method: 'POST', body: JSON.stringify({ job: mutation }),
    }), { EXECUTOR_SECRET: 'test-secret' });
  assert.equal(unauthenticated.status, 401);
  const seen = [];
  const env = { EXECUTOR_SECRET: 'test-secret', MCP: { getByName: (name) => ({
    fetch: async (url, init) => { seen.push([name, url, init.method]); return new Response('{}'); },
  }) } };
  await exportsObject.default.fetch(new Request(
    `https://executor.example/calls/mcp/${mutationId}/abandon`, {
      method: 'POST', headers: { authorization: 'Bearer test-secret' },
      body: JSON.stringify({ job: mutation, reason: 'stale' }),
    }), env);
  assert.equal(seen.length, 1);
  assert.match(seen[0][0], /^mcp-[0-9]+$/);
  assert.equal(seen[0][1], `https://container.internal/abandon/${mutationId}`);
  assert.equal(seen[0][2], 'POST');
});

test('MCP and interactive containers outlive model think time; batch lanes stay lean', async () => {
  const sleep = (cls) => fixture({ cls }).adapter.sleepAfter;
  assert.equal(sleep('ValmeraMcp'), '240s');
  assert.equal(sleep('ValmeraInteractive'), '240s');
  assert.equal(sleep('ValmeraBatch'), '60s');
  assert.equal(sleep('ValmeraAgent'), '60s');
  assert.equal(sleep('ValmeraShorts'), '60s');
  const mcp = fixture({ cls: 'ValmeraMcp' }).adapter;
  mcp.env = {};
  assert.equal(mcp.environment().CLOUDFLARE_IDLE_TAIL_S, '240');
  const batch = fixture({ cls: 'ValmeraBatch' }).adapter;
  batch.env = {};
  assert.equal(batch.environment().CLOUDFLARE_IDLE_TAIL_S, '60');
});
