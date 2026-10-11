const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');
const ts = require('typescript');
const { mergeVisionOverlay, readOverlay } = require('../vision-overlay.cjs');

// Wave 0 track 0A: the sidecar job kind on the batch lane (its own shard
// cap) and the DARK ValmeraVision lane. Executed against the real adapter.
const code = ts.transpileModule(fs.readFileSync(require.resolve('../src/index.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const exportsObject = {};
class Container { constructor(ctx, env) { this.ctx = ctx; this.env = env; } }
vm.runInNewContext(code, {
  exports: exportsObject, crypto: webcrypto, TextEncoder, Request, Response, URL,
  console, require: (name) => {
    assert.equal(name, '@cloudflare/containers'); return { Container };
  },
});
const worker = exportsObject.default;
const SECRET = 'test-secret';

function namespace(lane, seen) {
  return {
    getByName(name) {
      return { fetch: async (url, init) => { seen.push({ lane, name, url: String(url), init }); return new Response('{}'); } };
    },
  };
}

function env(withVision) {
  const seen = [];
  const e = { EXECUTOR_SECRET: SECRET };
  for (const lane of ['INTERACTIVE', 'BATCH', 'AGENT', 'MCP', 'SHORTS']) e[lane] = namespace(lane, seen);
  if (withVision) e.VISION = namespace('VISION', seen);
  return { e, seen };
}

function call(lane, callId, type) {
  return new Request(`https://w.example/calls/${lane}/${callId}`, {
    method: 'POST',
    headers: { authorization: `Bearer ${SECRET}`, 'content-type': 'application/json' },
    body: JSON.stringify({ job: { id: null, type, project_id: 7, total_claims: null, payload: {} } }),
  });
}

test('sidecar calls hold at most three batch shards, below the faces pair', async () => {
  const shards = new Set();
  for (const [head, slot] of [['00000000', 0], ['00000001', 1], ['00000002', 2], ['ffffffff', 0xffffffff % 3]]) {
    const { e, seen } = env(false);
    const res = await worker.fetch(call('batch', `cf-sidecar-${head}abcdef012345`, 'sidecar'), e);
    assert.equal(res.status, 200);
    assert.equal(seen[0].lane, 'BATCH');
    assert.equal(seen[0].name, `batch-${8 - 1 - 2 - slot}`);
    shards.add(seen[0].name);
  }
  assert.deepEqual([...shards].sort(), ['batch-3', 'batch-4', 'batch-5']);
  const { e, seen } = env(false);
  await worker.fetch(call('batch', 'cf-faces-0123456789abcdef0123', 'faces'), e);
  assert.ok(['batch-6', 'batch-7'].includes(seen[0].name));
});

test('the vision lane is dark: unbound means a safe fallback, never a launch', async () => {
  const { e, seen } = env(false);
  const res = await worker.fetch(call('vision', 'cf-sidecar-0000000000000000abcd', 'sidecar'), e);
  assert.equal(res.status, 404);
  const body = await res.json();
  assert.equal(body.safe_to_fallback, true);
  assert.equal(seen.length, 0);
});

test('a deployed vision binding serves sidecars only', async () => {
  const { e, seen } = env(true);
  const ok = await worker.fetch(call('vision', 'cf-sidecar-0000000000000000abcd', 'sidecar'), e);
  assert.equal(ok.status, 200);
  assert.equal(seen[0].lane, 'VISION');
  assert.match(seen[0].name, /^vision-([0-9]|1[01])$/);
  const refused = await worker.fetch(call('vision', 'cf-index-0000000000000000abcd', 'index'), e);
  assert.equal(refused.status, 400);
  assert.equal(seen.length, 1);
});

test('ValmeraVision is exported but wrangler.jsonc does not deploy it', () => {
  assert.equal(typeof exportsObject.ValmeraVision, 'function');
  const wrangler = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'wrangler.jsonc'), 'utf8'));
  const text = JSON.stringify(wrangler);
  assert.ok(!text.includes('ValmeraVision'));
  assert.ok(!text.includes('"VISION"'));
  assert.deepEqual(wrangler.migrations.map((m) => m.tag), ['v1', 'v2']);
});

test('the overlay adds exactly the dark lane and refuses a double merge', () => {
  const base = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'wrangler.jsonc'), 'utf8'));
  const merged = mergeVisionOverlay(base);
  const vision = merged.containers.find((c) => c.class_name === 'ValmeraVision');
  assert.equal(vision.image, '../Dockerfile.vision');
  assert.equal(vision.instance_type, 'standard-4');
  assert.deepEqual(merged.containers.slice(0, base.containers.length), base.containers);
  assert.deepEqual(merged.durable_objects.bindings.at(-1), { name: 'VISION', class_name: 'ValmeraVision' });
  assert.deepEqual(merged.migrations.at(-1), { tag: 'v3', new_sqlite_classes: ['ValmeraVision'] });
  assert.equal(readOverlay()._comment, undefined);
  assert.throws(() => mergeVisionOverlay(merged), /already/);
  // the base config object is untouched
  assert.ok(!JSON.stringify(base).includes('ValmeraVision'));
});
