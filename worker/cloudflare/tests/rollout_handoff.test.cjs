const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto, createHash } = require('node:crypto');
const ts = require('typescript');

// Deploy handoffs (Oct 2026), against the real adapter with transactional
// in-memory Durable Object storage (same harness as skew.test.cjs).
//  * A container that is gone when the Worker sends /run makes the SDK answer
//    itself ("Failed to start container: ..."): nothing reached Python, so the
//    call is a startup refusal the dispatcher retries, never an `unknown`
//    call whose mutation might have landed.
//  * Each lane's deployment-probe Durable Object keeps a release registry; an
//    idle container on an older release is recycled before /run once this
//    Worker's release is known to start on the lane, so nothing is served by
//    stale code after new code can serve it.
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
  console: { log() {}, warn() {}, error() {} }, require: (name) => {
    assert.equal(name, '@cloudflare/containers'); return { Container };
  },
});

const job = { id: 73449, total_claims: 1, project_id: 3771, type: 'mcp_tool',
  payload: { tool: 'set_picture_card', args: {}, mutation: true } };
const digest = createHash('sha256').update(`${job.type}:${job.id}:${job.total_claims}`)
  .digest('hex').slice(0, 20);
const callId = `cf-mcp-p${job.project_id}-${digest}`;

function storageFor(values) {
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
  return storage;
}

// An MCP namespace whose deployment probe is a real adapter instance: the
// lane's release registry.
function lane(cls = 'ValmeraMcp', source = 'new-source') {
  const registryValues = new Map();
  const waits = [];
  const env = { SOURCE_VERSION: source };
  const registry = new exportsObject[cls]({ storage: storageFor(registryValues),
    waitUntil: (p) => waits.push(p) }, env);
  const names = [];
  env.MCP = env.INTERACTIVE = env.BATCH = env.AGENT = env.SHORTS = {
    getByName: (name) => {
      names.push(name);
      return { fetch: (url, init) => registry.fetch(new Request(String(url), init)) };
    },
  };
  function shard() {
    const values = new Map();
    const adapter = new exportsObject[cls]({ storage: storageFor(values),
      waitUntil: (p) => waits.push(p) }, env);
    return { adapter, values };
  }
  return { env, registry, registryValues, shard, names, waits };
}

function execute(adapter, body = job, launch_id = 'launch-0001') {
  return adapter.fetch(new Request(`https://container.internal/execute/${callId}`, {
    method: 'POST', body: JSON.stringify({ job: body, timeout_s: 600, launch_id }),
  }));
}

function health(code_version, status = 'ok') {
  return { status, role: 'mcp_executor', code_version, features: ['custom_filter', 'skew_guard'] };
}

test('/run that the SDK could not deliver is a pre-run refusal, never an unknown call', async () => {
  for (const [httpStatus, text] of [
    [500, 'Failed to start container: Error: The container is not running, consider calling start()'],
    [503, 'There is no Container instance available at this time.\nThis is likely ...'],
  ]) {
    const { shard } = lane();
    const { adapter, values } = shard();
    adapter.startAndWaitForPorts = async () => {};
    adapter.containerFetch = async (url) => {
      if (String(url).endsWith('/health')) {
        return new Response(JSON.stringify(health('new-source')));
      }
      return new Response(text, { status: httpStatus });
    };
    const response = await execute(adapter);
    assert.equal(response.status, 503);
    const body = await response.json();
    assert.equal(body.safe_to_fallback, true);
    assert.equal(body.retryable, true);
    const record = values.get(`call:${callId}`);
    assert.equal(record.status, 'refused');
    assert.equal(record.launchId, 'launch-0001');
    assert.equal(values.has('active'), false);
    if (httpStatus === 503) assert.equal(body.capacity_unavailable, true);
  }
});

test('an answer from the executor itself still goes through the normal path', async () => {
  const { shard } = lane();
  const { adapter, values } = shard();
  adapter.startAndWaitForPorts = async () => {};
  adapter.containerFetch = async (url) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify(health('new-source')));
    return new Response(JSON.stringify({ error: 'boom', retryable: false }), { status: 200 });
  };
  const response = await execute(adapter);
  assert.equal(response.status, 200);
  assert.equal(values.get(`call:${callId}`).status, 'failed');
});

test('a startup that loses its container is refused before /run with the launch id', async () => {
  const { shard } = lane();
  const { adapter, values } = shard();
  adapter.startAndWaitForPorts = async () => {
    throw new Error('The container is not running, consider calling start()');
  };
  const runs = [];
  adapter.containerFetch = async (url) => { runs.push(url); return new Response('{}'); };
  const response = await execute(adapter);
  assert.equal(response.status, 503);
  const body = await response.json();
  assert.equal(body.safe_to_fallback, true);
  assert.equal(body.failure.kind, 'provider_start_abandoned');
  assert.equal(values.get(`call:${callId}`).status, 'refused');
  assert.equal(runs.length, 0);
});

test('an idle container on an older release is recycled once the new release starts on the lane', async () => {
  const { shard, registryValues } = lane();
  registryValues.set('releaseReady', { source: 'new-source', at: Date.now() });
  const { adapter, values } = shard();
  let image = 'old-source';
  let starts = 0;
  let destroyed = 0;
  adapter.startAndWaitForPorts = async () => { starts += 1; };
  adapter.destroy = async () => { destroyed += 1; image = 'new-source'; };
  const served = [];
  adapter.containerFetch = async (url) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify(health(image)));
    served.push(image);
    return new Response(JSON.stringify({ result: { text: 'ok' }, job_completed: true }));
  };
  const response = await execute(adapter);
  assert.equal(response.status, 200);
  assert.equal(destroyed, 1);
  assert.equal(starts, 2);
  assert.deepEqual(served, ['new-source']);
  assert.equal(values.get(`call:${callId}`).status, 'done');
  assert.equal(values.has('active'), false);
});

test('while the new release cannot start yet, the guarded old container keeps serving', async () => {
  const { shard } = lane();
  const { adapter } = shard();
  adapter.startAndWaitForPorts = async () => {};
  adapter.destroy = async () => { throw new Error('must not recycle without a startable release'); };
  const served = [];
  adapter.containerFetch = async (url) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify(health('old-source')));
    served.push('old-source');
    return new Response(JSON.stringify({ result: { text: 'ok' }, job_completed: true }));
  };
  assert.equal((await execute(adapter)).status, 200);
  assert.deepEqual(served, ['old-source']);
});

test('a recycle that still comes back old serves it and does not retry on every call', async () => {
  const { shard, registryValues } = lane();
  registryValues.set('releaseReady', { source: 'new-source', at: Date.now() });
  const { adapter } = shard();
  let destroyed = 0;
  adapter.startAndWaitForPorts = async () => {};
  adapter.destroy = async () => { destroyed += 1; };
  adapter.containerFetch = async (url) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify(health('old-source')));
    return new Response(JSON.stringify({ result: { text: 'ok' }, job_completed: true }));
  };
  assert.equal((await execute(adapter)).status, 200);
  const second = { ...job, id: job.id + 1 };
  const secondDigest = createHash('sha256').update(`${second.type}:${second.id}:1`).digest('hex').slice(0, 20);
  const response = await adapter.fetch(new Request(
    `https://container.internal/execute/cf-mcp-p${job.project_id}-${secondDigest}`, {
      method: 'POST', body: JSON.stringify({ job: second, timeout_s: 600, launch_id: 'launch-0002' }),
    }));
  assert.equal(response.status, 200);
  assert.equal(destroyed, 1);
});

test('a call on the new release reports it to the lane registry, once', async () => {
  const { shard, registryValues, waits, names } = lane();
  const { adapter } = shard();
  adapter.startAndWaitForPorts = async () => {};
  adapter.containerFetch = async (url) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify(health('new-source')));
    return new Response(JSON.stringify({ result: { text: 'ok' }, job_completed: true }));
  };
  assert.equal((await execute(adapter)).status, 200);
  const second = { ...job, id: job.id + 1 };
  const secondDigest = createHash('sha256').update(`${second.type}:${second.id}:1`).digest('hex').slice(0, 20);
  assert.equal((await adapter.fetch(new Request(
    `https://container.internal/execute/cf-mcp-p${job.project_id}-${secondDigest}`, {
      method: 'POST', body: JSON.stringify({ job: second, timeout_s: 600, launch_id: 'launch-0002' }),
    }))).status, 200);
  await Promise.all(waits);
  assert.equal(registryValues.get('releaseReady').source, 'new-source');
  assert.deepEqual(names, ['mcp-deployment-probe']);
});

test('the registry records only its own release and the probe records a fresh start', async () => {
  const { registry, registryValues } = lane();
  const stale = await registry.fetch(new Request('https://container.internal/release-ready', {
    method: 'POST', body: JSON.stringify({ source: 'old-source' }) }));
  assert.equal(stale.status, 409);
  assert.equal(registryValues.has('releaseReady'), false);
  registry.startAndWaitForPorts = async () => {};
  registry.destroy = async () => {};
  registry.containerFetch = async () => new Response(JSON.stringify(health('new-source')));
  assert.equal((await registry.fetch(new Request('https://container.internal/probe'))).status, 200);
  const body = await (await registry.fetch(new Request('https://container.internal/release-ready'))).json();
  assert.equal(body.source, 'new-source');
});

test('a draining container names its state in the readiness refusal', async () => {
  const { shard } = lane();
  const { adapter } = shard();
  adapter.startAndWaitForPorts = async () => {};
  adapter.destroy = async () => {};
  adapter.containerFetch = async () => new Response(JSON.stringify(health('new-source', 'draining')));
  const response = await execute(adapter);
  assert.equal(response.status, 503);
  const body = await response.json();
  assert.match(body.error, /^container readiness mismatch .* status=draining$/);
});
