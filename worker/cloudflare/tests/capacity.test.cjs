const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto, createHash } = require('node:crypto');
const ts = require('typescript');

// "There is no container instance that can be provided to this durable
// object" and its relatives, executed against the real adapter with
// transactional in-memory Durable Object storage (same harness as
// reservation.test.cjs). Container destruction throws unless a test allows it.
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

const NO_INSTANCE = 'Error: there is no container instance that can be provided to this durable object';
const MINUTE = 60 * 1000;

function digest(job) {
  return createHash('sha256').update(`${job.type}:${job.id}:${job.total_claims}`)
    .digest('hex').slice(0, 20);
}

function fixture(cls = 'ValmeraInteractive', initial = []) {
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
  const adapter = new exportsObject[cls]({ storage, waitUntil: () => {} }, {});
  return { values, adapter };
}

function execute(adapter, callId, job, timeout_s = 600, launch_id = 'launch-0001') {
  return adapter.fetch(new Request(`https://container.internal/execute/${callId}`, {
    method: 'POST', body: JSON.stringify({ job, timeout_s, launch_id }),
  }));
}

function status(adapter, callId) {
  return adapter.fetch(new Request(`https://container.internal/status/${callId}`));
}

const mediaChild = { id: null, total_claims: null, project_id: 3419, type: 'mcp_media',
  payload: { tool: '__media__', args: {} } };
const mediaChildId = 'cf-mcp_media-04c59a146fd338da4d7a';

test('no container instance is a structured, retryable refusal that accepted nothing', async () => {
  const { adapter, values } = fixture();
  adapter.startAndWaitForPorts = async () => { throw new Error(NO_INSTANCE.slice(7)); };
  const response = await execute(adapter, mediaChildId, mediaChild);
  assert.equal(response.status, 503);
  const body = await response.json();
  assert.equal(body.error, NO_INSTANCE);
  assert.equal(body.safe_to_fallback, true);
  assert.equal(body.capacity_unavailable, true);
  assert.deepEqual(body.failure, { kind: 'provider_capacity_unavailable', retryable: true,
    max_attempts: 2, agent_repairable: false });
  // The admission lock is free for the next caller and nothing was destroyed.
  assert.equal(values.has('active'), false);
  assert.equal(values.get(`call:${mediaChildId}`).status, 'refused');
  assert.ok([...values.keys()].some((key) => key.startsWith('terminal:')
    && values.get(key) === mediaChildId));
});

test('a reconnecting dispatcher reads the refusal instead of a missing call', async () => {
  const { adapter } = fixture();
  adapter.startAndWaitForPorts = async () => { throw new Error(NO_INSTANCE.slice(7)); };
  await execute(adapter, mediaChildId, mediaChild);
  // getState would mean a lease expiry tried to observe/fence compute.
  adapter.getState = async () => { throw new Error('a refusal must not be fenced'); };
  const response = await status(adapter, mediaChildId);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.status, 'refused');
  // The dispatcher trusts only a refusal of its own launch request.
  assert.equal(body.launchId, 'launch-0001');
  assert.equal(body.envelope.capacity_unavailable, true);
  assert.equal(body.envelope.safe_to_fallback, true);
});

test('a relaunch replaces the refusal and carries its own launch nonce', async () => {
  const { adapter, values } = fixture();
  adapter.startAndWaitForPorts = async () => { throw new Error('Container sidecar is shutting down'); };
  await execute(adapter, mediaChildId, mediaChild, 600, 'launch-0001');
  let release;
  adapter.startAndWaitForPorts = () => new Promise((resolve) => { release = resolve; });
  const pending = execute(adapter, mediaChildId, mediaChild, 600, 'launch-0002');
  for (let i = 0; i < 20 && values.get(`call:${mediaChildId}`)?.status !== 'starting'; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  const state = await (await status(adapter, mediaChildId)).json();
  assert.equal(state.status, 'starting');
  assert.equal(state.launchId, 'launch-0002');
  // An unrecognised nonce is ignored rather than trusted.
  const bogus = fixture();
  bogus.adapter.startAndWaitForPorts = async () => { throw new Error(NO_INSTANCE.slice(7)); };
  await execute(bogus.adapter, mediaChildId, mediaChild, 600, 'x');
  assert.equal(bogus.values.get(`call:${mediaChildId}`).launchId, undefined);
  release();
  adapter.containerFetch = async () => { throw new Error('stop here'); };
  adapter.retireUnreadyContainer = async () => {};
  await pending;
});

test('MCP refusals keep the single-attempt MCP contract', async () => {
  const { adapter } = fixture('ValmeraMcp');
  const job = { id: 62751, total_claims: 1, project_id: 3419, type: 'mcp_tool',
    payload: { tool: 'get_edl', mutation: false } };
  adapter.startAndWaitForPorts = async () => { throw new Error('you are requesting too many containers per second'); };
  const body = await (await execute(adapter, `cf-mcp-p3419-${digest(job)}`, job)).json();
  assert.equal(body.capacity_unavailable, true);
  assert.equal(body.failure.max_attempts, 1);
});

test('the same identity may launch again over its refusal (rollout waits reuse it)', async () => {
  const { adapter, values } = fixture();
  adapter.startAndWaitForPorts = async () => { throw new Error('Container sidecar is shutting down'); };
  const refused = await (await execute(adapter, mediaChildId, mediaChild)).json();
  assert.equal(refused.capacity_unavailable, undefined);
  assert.equal(refused.failure.kind, 'provider_start_abandoned');
  assert.equal(refused.safe_to_fallback, true);
  const reservation = await adapter.reserve(mediaChildId, 'mcp_media', Date.now(), Date.now() + MINUTE);
  assert.equal(reservation.kind, 'reserved');
  assert.equal(values.get(`call:${mediaChildId}`).status, 'submitted');
  assert.equal(values.get('active').callId, mediaChildId);
});

test('a late startup failure cannot overwrite a terminal outcome or another call', async () => {
  const { adapter, values } = fixture();
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  adapter.startAndWaitForPorts = async () => { await gate; throw new Error(NO_INSTANCE.slice(7)); };
  const pending = execute(adapter, mediaChildId, mediaChild);
  // Let the handler persist `starting`, then terminalize it as an abandoned
  // start (a status poll after three minutes) and admit a different call.
  for (let i = 0; i < 20 && values.get(`call:${mediaChildId}`)?.status !== 'starting'; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.equal(values.get(`call:${mediaChildId}`).status, 'starting');
  await adapter.expireStaleStart(mediaChildId, Date.now() + 4 * MINUTE);
  assert.equal((await adapter.reserve('cf-frames-next-call', 'frames', Date.now(), Date.now() + MINUTE)).kind, 'reserved');
  release();
  const body = await (await pending).json();
  assert.equal(body.failure.kind, 'provider_start_abandoned');
  assert.equal(values.get(`call:${mediaChildId}`).status, 'failed');
  assert.equal(values.get('active').callId, 'cf-frames-next-call');
});

test('refusal tombstones are pruned with ordinary terminal records', async () => {
  const { adapter, values } = fixture();
  adapter.startAndWaitForPorts = async () => { throw new Error(NO_INSTANCE.slice(7)); };
  await execute(adapter, mediaChildId, mediaChild);
  await adapter.pruneTerminalCalls(Date.now() + 8 * 24 * 60 * MINUTE);
  assert.equal(values.has(`call:${mediaChildId}`), false);
  assert.equal([...values.keys()].some((key) => key.startsWith('terminal:')), false);
});

test('the deployment probe releases its container instead of idling on the lane quota', async () => {
  const { adapter } = fixture('ValmeraBatch');
  let destroyed = 0;
  adapter.startAndWaitForPorts = async () => {};
  adapter.containerFetch = async () => new Response(JSON.stringify({
    status: 'ok', role: 'executor', code_version: 'abc' }));
  adapter.destroy = async () => { destroyed += 1; };
  const response = await adapter.fetch(new Request('https://container.internal/probe'));
  assert.equal(response.status, 200);
  assert.equal((await response.json()).role, 'executor');
  assert.equal(destroyed, 1);
  // A failed destroy still answers the probe; idle expiry reclaims it.
  adapter.destroy = async () => { throw new Error('transient'); };
  assert.equal((await adapter.fetch(new Request('https://container.internal/probe'))).status, 200);
});

test('capacity-spread identities of every job type acknowledge only their exact claim', async () => {
  const envelope = { result: { ok: true }, job_completed: true };
  for (const type of ['final', 'index', 'agent_turn', 'shorts_plan', 'mcp_tool']) {
    const job = { id: 700, total_claims: 3, project_id: 41, type };
    for (const slot of [1, 2]) {
      const callId = `cf-alt${slot}-${type}-p41-${digest(job)}`;
      const { adapter, values } = fixture('ValmeraBatch', [
        [`call:${callId}`, { status: 'unknown', jobType: type, activeUntil: Date.now() + MINUTE }],
        ['active', { callId, expiresAt: Date.now() + MINUTE }],
      ]);
      const send = (j) => adapter.fetch(new Request(`https://container.internal/complete/${callId}`, {
        method: 'POST', body: JSON.stringify({ job: j, envelope }),
      }));
      assert.equal((await send({ ...job, total_claims: 4 })).status, 409);
      assert.equal((await send({ ...job, project_id: 42 })).status, 409);
      assert.equal(values.get('active').callId, callId);
      assert.equal((await send(job)).status, 200);
      assert.equal(values.has('active'), false);
    }
  }
});

function routerEnv() {
  const seen = [];
  const namespace = (lane) => ({
    getByName: (name) => ({
      fetch: async (url, init) => {
        seen.push({ lane, name, url, body: init?.body && JSON.parse(init.body) });
        return new Response('{}');
      },
    }),
  });
  return {
    seen,
    env: {
      EXECUTOR_SECRET: 'secret', INTERACTIVE: namespace('interactive'), BATCH: namespace('batch'),
      AGENT: namespace('agent'), MCP: namespace('mcp'), SHORTS: namespace('shorts'),
    },
  };
}

function route(env, lane, callId, job) {
  return exportsObject.default.fetch(new Request(`https://executor.example/calls/${lane}/${callId}`, {
    method: 'POST', headers: { authorization: 'Bearer secret' },
    body: JSON.stringify({ job, timeout_s: 600 }),
  }), env);
}

test('interactive media may fail over to the batch lane', async () => {
  const { env, seen } = routerEnv();
  for (const type of ['frames', 'mcp_media', 'preview', 'preview_check', 'filmstrip']) {
    const response = await route(env, 'batch', `cf-alt1-${type}-p7-0123456789abcdef0123`, {
      id: null, total_claims: null, project_id: 7, type, payload: {} });
    assert.equal(response.status, 200, type);
  }
  assert.ok(seen.every((call) => call.lane === 'batch' && /^batch-[0-7]$/.test(call.name)));
});

test('only watch_video may leave the MCP lane, and only for an executor lane', async () => {
  const { env, seen } = routerEnv();
  const media = { id: 62751, total_claims: 1, project_id: 3419, type: 'mcp_tool',
    payload: { tool: '__media__', mutation: false } };
  const callId = `cf-mcp-p3419-${digest(media)}`;
  for (const lane of ['interactive', 'batch']) {
    assert.equal((await route(env, lane, callId, media)).status, 200, lane);
  }
  assert.equal(seen.length, 2);
  for (const tool of ['get_edl', 'add_text', undefined]) {
    for (const lane of ['interactive', 'batch']) {
      const response = await route(env, lane, callId, { ...media, payload: { tool } });
      assert.equal(response.status, 400);
      assert.equal((await response.json()).safe_to_fallback, true);
    }
  }
  for (const lane of ['agent', 'shorts']) {
    assert.equal((await route(env, lane, callId, media)).status, 400, lane);
  }
  assert.equal(seen.length, 2);
});

test('batch-only work never fails over to the smaller interactive image', async () => {
  const { env, seen } = routerEnv();
  for (const type of ['index', 'final', 'fetch', 'search', 'matte', 'stems', 'clean']) {
    const response = await route(env, 'interactive', `cf-${type}-0123456789abcdef0123`, {
      id: null, total_claims: null, project_id: 7, type, payload: {} });
    assert.equal(response.status, 400, type);
  }
  assert.equal(seen.length, 0);
});
