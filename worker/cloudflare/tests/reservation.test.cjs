const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto, createHash } = require('node:crypto');
const ts = require('typescript');

// Execute the actual adapter against transactional in-memory Durable Object
// storage. Container lifecycle methods deliberately throw: acknowledging a
// completed edit must neither launch work nor stop a warm process.
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
const job = { id: 39373, total_claims: 1, project_id: 2423, type: 'mcp_tool' };
const callId = 'cf-mcp-p2423-' + createHash('sha256')
  .update(`${job.type}:${job.id}:${job.total_claims}`).digest('hex').slice(0, 20);
const envelope = { result: { asset_id: 16172 }, job_completed: true };
function fixture(status = 'running', activeCallId = callId) {
  const values = new Map([
    [`call:${callId}`, { status, jobType: job.type, activeUntil: Date.now() + 21600000 }],
    ['active', { callId: activeCallId, expiresAt: Date.now() + 21600000 }],
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
  return { values, adapter: new exportsObject.ValmeraMcp({ storage }, {}) };
}
function complete(adapter, changedJob = job) {
  return adapter.fetch(new Request(`https://container.internal/complete/${callId}`, {
    method: 'POST', body: JSON.stringify({ job: changedJob, envelope }),
  }));
}

test('committed job releases an abandoned running reservation and retains its result', async () => {
  const { adapter, values } = fixture();
  assert.equal((await adapter.reserve('cf-next-identity', job.type, Date.now(), Date.now() + 60000)).activeCallId, callId);
  assert.equal((await complete(adapter)).status, 200);
  assert.equal(values.has('active'), false);
  assert.equal(values.get(`call:${callId}`).status, 'done');
  assert.deepEqual(values.get(`call:${callId}`).envelope, envelope);
  assert.equal((await adapter.reserve('cf-next-identity', job.type, Date.now(), Date.now() + 60000)).kind, 'reserved');
  // Duplicate success cannot release the newly admitted call.
  assert.equal((await complete(adapter)).status, 200);
  assert.equal(values.get('active').callId, 'cf-next-identity');
});

test('mismatched claims and projects cannot release the active call', async () => {
  for (const changed of [{ total_claims: 2 }, { id: 39374 }, { project_id: 9 }, { type: 'preview' }]) {
    const { adapter, values } = fixture();
    assert.equal((await complete(adapter, { ...job, ...changed })).status, 409);
    assert.equal(values.get('active').callId, callId);
  }
});

test('a start, failed call, reset or different active identity is never acknowledged', async () => {
  for (const [status, active] of [['starting', callId], ['submitted', callId], ['failed', callId], ['stopping', `reset:${callId}`], ['running', 'cf-another-call']]) {
    const { adapter, values } = fixture(status, active);
    assert.equal((await complete(adapter)).status, 409);
    assert.equal(values.get('active').callId, active);
  }
});

test('an unknown completed call can recover and late transport errors preserve success', async () => {
  const { adapter, values } = fixture('unknown');
  assert.equal((await complete(adapter)).status, 200);
  await adapter.reserve('cf-next-identity', job.type, Date.now(), Date.now() + 60000);
  await adapter.storeTerminal(callId, { status: 'failed', jobType: job.type, error: 'late disconnect' });
  assert.equal(values.get(`call:${callId}`).status, 'done');
  assert.equal(values.get('active').callId, 'cf-next-identity');
});

test('ordinary terminal storage also releases its reservation in the same transaction', async () => {
  const { adapter, values } = fixture();
  await adapter.storeTerminal(callId, { status: 'done', jobType: job.type, envelope });
  assert.equal(values.has('active'), false);
  assert.equal(values.get(`call:${callId}`).status, 'done');
});

test('completion API is authenticated before resolving any container', async () => {
  const response = await exportsObject.default.fetch(new Request(
    `https://executor.example/calls/mcp/${callId}/complete`, {
      method: 'POST', body: JSON.stringify({ job, envelope }),
    }), { EXECUTOR_SECRET: 'test-secret' });
  assert.equal(response.status, 401);
});
