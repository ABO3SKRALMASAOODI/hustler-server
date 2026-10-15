const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto, createHash } = require('node:crypto');
const ts = require('typescript');

// Zero-downtime deploys (Oct 2026), executed against the real adapter with
// transactional in-memory Durable Object storage (same harness as
// capacity.test.cjs). A container on another release is served only when it
// carries the skew guard; a launch that never reached its Durable Object is
// fenced so it can never start later; a lost `submitted` record no longer
// holds a shard for the call's whole lease.
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

const job = { id: 70001, total_claims: 1, project_id: 3655, type: 'mcp_tool',
  payload: { tool: 'get_edl', args: {}, mutation: false } };
const digest = createHash('sha256').update(`${job.type}:${job.id}:${job.total_claims}`)
  .digest('hex').slice(0, 20);
const callId = `cf-mcp-p${job.project_id}-${digest}`;

function fixture(cls = 'ValmeraMcp', initial = [], env = { SOURCE_VERSION: 'new-source' }) {
  const values = new Map(initial);
  const api = (map) => ({
    get: async (key) => structuredClone(map.get(key)),
    put: async (key, value) => {
      if (typeof key === 'string') map.set(key, structuredClone(value));
      else for (const [k, v] of Object.entries(key)) map.set(k, structuredClone(v));
    },
    delete: async (key) => Array.isArray(key) ? key.forEach((k) => map.delete(k)) : map.delete(key),
    list: async ({ prefix, limit }) => new Map([...map.entries()]
      .filter(([k]) => k.startsWith(prefix)).sort(([a], [b]) => a.localeCompare(b))
      .slice(0, limit)),
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
  const adapter = new exportsObject[cls]({ storage, waitUntil: () => {} }, env);
  return { values, adapter };
}

function container(adapter, health, runs) {
  adapter.startAndWaitForPorts = async () => {};
  adapter.containerFetch = async (url) => {
    if (String(url).endsWith('/health')) {
      return new Response(JSON.stringify(health), { status: 200 });
    }
    runs.push(url);
    return new Response(JSON.stringify({ result: { text: 'ok' }, job_completed: true }));
  };
}

function execute(adapter, id = callId, body = job, launch_id = 'launch-0001') {
  return adapter.fetch(new Request(`https://container.internal/execute/${id}`, {
    method: 'POST', body: JSON.stringify({ job: body, timeout_s: 600, launch_id }),
  }));
}

function fence(adapter, id = callId, launch_id = 'launch-0001') {
  return adapter.fetch(new Request(`https://container.internal/fence/${id}`, {
    method: 'POST', body: JSON.stringify({ job: { id: job.id, type: job.type,
      project_id: job.project_id, total_claims: job.total_claims }, launch_id }),
  }));
}

function status(adapter, id = callId) {
  return adapter.fetch(new Request(`https://container.internal/status/${id}`));
}

test('a guarded container on an older release serves the call during a rollout', async () => {
  const { adapter, values } = fixture();
  const runs = [];
  container(adapter, { status: 'ok', role: 'mcp_executor', code_version: 'old-source',
    features: ['custom_filter', 'skew_guard'] }, runs);
  const response = await execute(adapter);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { result: { text: 'ok' }, job_completed: true });
  assert.equal(runs.length, 1);
  assert.equal(values.get(`call:${callId}`).status, 'done');
  assert.equal(values.has('active'), false);
});

test('an older container without the guard is still retired before any /run', async () => {
  const { adapter, values } = fixture();
  const runs = [];
  container(adapter, { status: 'ok', role: 'mcp_executor', code_version: 'old-source',
    features: ['custom_filter'] }, runs);
  let destroyed = 0;
  adapter.destroy = async () => { destroyed += 1; };
  const response = await execute(adapter);
  assert.equal(response.status, 503);
  const body = await response.json();
  assert.match(body.error, /^container readiness mismatch /);
  assert.equal(body.safe_to_fallback, true);
  assert.equal(runs.length, 0);
  assert.equal(destroyed, 1);
  assert.equal(values.get(`call:${callId}`).status, 'refused');
});

test('a draining container or one with another role never receives a call', async () => {
  for (const health of [
    { status: 'draining', role: 'mcp_executor', code_version: 'new-source', features: ['skew_guard'] },
    { status: 'ok', role: 'executor', code_version: 'old-source', features: ['skew_guard'] },
  ]) {
    const { adapter } = fixture();
    const runs = [];
    container(adapter, health, runs);
    adapter.destroy = async () => {};
    const response = await execute(adapter);
    assert.equal(response.status, 503);
    assert.equal(runs.length, 0);
  }
});

test('a submitted record whose handler was lost expires after a minute, not its lease', async () => {
  const now = Date.now();
  const { adapter, values } = fixture('ValmeraBatch', [
    [`call:${callId}`, { status: 'submitted', jobType: 'mcp_tool',
      updatedAt: new Date(now - 61_000).toISOString(), activeUntil: now + 6 * 3600_000 }],
    ['active', { callId, expiresAt: now + 6 * 3600_000 }],
  ]);
  adapter.getState = async () => { throw new Error('nothing to observe'); };
  const response = await status(adapter);
  const body = await response.json();
  assert.equal(body.status, 'failed');
  assert.equal(body.envelope.failure.kind, 'provider_start_abandoned');
  assert.equal(body.envelope.retryable, true);
  assert.equal(values.has('active'), false);
});

test('a fresh submitted record is left alone', async () => {
  const now = Date.now();
  const { adapter, values } = fixture('ValmeraBatch', [
    [`call:${callId}`, { status: 'submitted', jobType: 'mcp_tool',
      updatedAt: new Date(now - 5_000).toISOString(), activeUntil: now + 600_000 }],
    ['active', { callId, expiresAt: now + 600_000 }],
  ]);
  adapter.getState = async () => ({ status: 'running' });
  const body = await (await status(adapter)).json();
  assert.equal(body.status, 'submitted');
  assert.equal(values.get('active').callId, callId);
});

test('a launch that never arrived is fenced, and a late arrival can never start it', async () => {
  const { adapter, values } = fixture();
  const fenced = await fence(adapter);
  assert.equal(fenced.status, 200);
  const body = await fenced.json();
  assert.equal(body.status, 'failed');
  assert.equal(body.fenced, true);
  assert.equal(body.envelope.failure.kind, 'provider_start_abandoned');
  assert.equal(body.envelope.retryable, true);
  const runs = [];
  container(adapter, { status: 'ok', role: 'mcp_executor', code_version: 'new-source',
    features: ['skew_guard'] }, runs);
  adapter.startAndWaitForPorts = async () => { throw new Error('a fenced call must not start'); };
  const late = await (await execute(adapter)).json();
  assert.equal(late.fenced, true);
  assert.equal(runs.length, 0);
  assert.equal(values.has('active'), false);
});

test('fencing never touches a call that is alive or already finished', async () => {
  const now = Date.now();
  for (const record of [
    { status: 'running', jobType: 'mcp_tool', updatedAt: new Date(now).toISOString(), activeUntil: now + 600_000 },
    { status: 'starting', jobType: 'mcp_tool', updatedAt: new Date(now).toISOString(), activeUntil: now + 600_000 },
    { status: 'done', jobType: 'mcp_tool', envelope: { result: {} }, updatedAt: new Date(now).toISOString(), activeUntil: now },
  ]) {
    const { adapter, values } = fixture('ValmeraMcp', [[`call:${callId}`, record]]);
    const body = await (await fence(adapter)).json();
    assert.equal(body.status, record.status);
    assert.equal(body.fenced, false);
    assert.equal(values.get(`call:${callId}`).status, record.status);
  }
});

test('an older launch refusal is fenced; the current launch refusal is reported as is', async () => {
  const now = Date.now();
  const refused = { status: 'refused', jobType: 'mcp_tool', launchId: 'launch-0001',
    envelope: { error: 'no instance', safe_to_fallback: true },
    updatedAt: new Date(now).toISOString(), activeUntil: now };
  const own = fixture('ValmeraMcp', [[`call:${callId}`, refused]]);
  assert.equal((await (await fence(own.adapter, callId, 'launch-0001')).json()).status, 'refused');
  const older = fixture('ValmeraMcp', [[`call:${callId}`, refused]]);
  const body = await (await fence(older.adapter, callId, 'launch-0002')).json();
  assert.equal(body.status, 'failed');
  assert.equal(body.fenced, true);
});

test('alternate identities up to cf-alt32 acknowledge only their exact claim', async () => {
  for (const slot of [3, 17, 32]) {
    const altId = `cf-alt${slot}-${job.type}-p${job.project_id}-${digest}`;
    const now = Date.now();
    const { adapter, values } = fixture('ValmeraMcp', [
      [`call:${altId}`, { status: 'unknown', jobType: job.type, activeUntil: now + 600_000,
        updatedAt: new Date(now).toISOString() }],
      ['active', { callId: altId, expiresAt: now + 600_000 }],
    ]);
    const response = await adapter.fetch(new Request(`https://container.internal/complete/${altId}`, {
      method: 'POST', body: JSON.stringify({ job, envelope: { result: {}, job_completed: true } }),
    }));
    assert.equal(response.status, 200);
    assert.equal(values.get(`call:${altId}`).status, 'done');
  }
  const tooHigh = `cf-alt33-${job.type}-p${job.project_id}-${digest}`;
  const now = Date.now();
  const { adapter } = fixture('ValmeraMcp', [
    [`call:${tooHigh}`, { status: 'unknown', jobType: job.type, activeUntil: now + 600_000,
      updatedAt: new Date(now).toISOString() }],
    ['active', { callId: tooHigh, expiresAt: now + 600_000 }],
  ]);
  const refused = await adapter.fetch(new Request(`https://container.internal/complete/${tooHigh}`, {
    method: 'POST', body: JSON.stringify({ job, envelope: { result: {}, job_completed: true } }),
  }));
  assert.equal(refused.status, 409);
});

test('the root health advertises the skew, fence and identity features', async () => {
  const response = await exportsObject.default.fetch(
    new Request('https://worker.example/health', { headers: { authorization: 'Bearer s3cret' } }),
    { EXECUTOR_SECRET: 's3cret', SOURCE_VERSION: 'new-source', CODE_VERSION: 'abc' });
  const body = await response.json();
  for (const feature of ['custom_filter', 'stems', 'skew_guard', 'fence', 'alt_slots_32']) {
    assert.ok(body.features.includes(feature), feature);
  }
});

test('the fence route is reachable through the Worker for every lane', async () => {
  const seen = [];
  const stub = { fetch: async (url, init) => { seen.push([String(url), init?.method]); return new Response('{}'); } };
  const ns = { getByName: () => stub };
  const env = { EXECUTOR_SECRET: 's3cret', INTERACTIVE: ns, BATCH: ns, AGENT: ns, MCP: ns, SHORTS: ns };
  const response = await exportsObject.default.fetch(new Request(
    `https://worker.example/calls/mcp/${callId}/fence`, {
      method: 'POST', headers: { authorization: 'Bearer s3cret' },
      body: JSON.stringify({ job: { id: job.id, type: 'mcp_tool', project_id: job.project_id, total_claims: 1 } }),
    }), env);
  assert.equal(response.status, 200);
  assert.deepEqual(seen, [[`https://container.internal/fence/${callId}`, 'POST']]);
});

test('a retirement orphaned before /run ends as a retryable abandoned start', async () => {
  const now = Date.now();
  const { adapter, values } = fixture('ValmeraMcp', [
    [`call:${callId}`, { status: 'stopping', preRun: true, jobType: 'mcp_tool',
      updatedAt: new Date(now - 5_000).toISOString(), activeUntil: now + 600_000 }],
    ['active', { callId: `reset:${callId}`, expiresAt: now + 60_000 }],
  ]);
  let destroyed = 0;
  adapter.destroy = async () => { destroyed += 1; };
  adapter.getState = async () => ({ status: 'running' });
  const body = await (await status(adapter)).json();
  assert.equal(body.status, 'failed');
  assert.equal(body.envelope.retryable, true);
  assert.equal(body.envelope.failure.kind, 'provider_start_abandoned');
  assert.equal(destroyed, 1);
  assert.equal(values.has('active'), false);
});

test('an unready container retirement marks its record as pre-run', async () => {
  const { adapter, values } = fixture();
  const runs = [];
  container(adapter, { status: 'ok', role: 'mcp_executor', code_version: 'old-source',
    features: [] }, runs);
  let seen = null;
  adapter.destroy = async () => { seen = values.get(`call:${callId}`); };
  await execute(adapter);
  assert.equal(seen.status, 'stopping');
  assert.equal(seen.preRun, true);
  assert.equal(runs.length, 0);
});
