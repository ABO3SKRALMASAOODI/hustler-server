const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');
const ts = require('typescript');

// The dispatcher mirrors shardName in Python (worker/remote.py
// _cloudflare_shard_index) so a busy refusal can hop to an identity that
// lands on a DIFFERENT shard. These vectors are pinned on both sides
// (worker/tests/test_busy_shard_hop.py): change one, change both.
const code = ts.transpileModule(fs.readFileSync(require.resolve('../src/index.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText + '\nexports.__shardName = shardName;';
const exportsObject = {};
vm.runInNewContext(code, {
  exports: exportsObject, crypto: webcrypto, TextEncoder, Request, Response, URL,
  console, require: () => ({ Container: class {} }),
});

const VECTORS = [
  ['batch', 'cf-index-21a53b11decccd9a5549', 'batch-1'],
  ['batch', 'cf-alt1-index-p3655-21a53b11decccd9a5549', 'batch-4'],
  ['batch', 'cf-alt7-final-p3395-5ec08d0813e4c419c43a', 'batch-4'],
  ['batch', 'cf-faces-0123456789abcdef0123', 'batch-7'],
  ['batch', 'cf-render-g12-1-5ec08d0813e4c419c43a', 'batch-6'],
  ['mcp', 'cf-mcp-p3614-e5ab61fc8ba0594f5760', 'mcp-12'],
  ['mcp', 'cf-alt3-mcp_tool-p3614-e5ab61fc8ba0594f5760', 'mcp-9'],
  ['interactive', 'cf-preview-p3690-aaaaaaaaaaaaaaaaaaaa', 'interactive-8'],
  ['interactive', 'cf-filmstrip-bbbbbbbbbbbbbbbbbbbb', 'interactive-16'],
  ['interactive', 'cf-render-g7-0-cccccccccccccccccccc', 'interactive-1'],
];

test('shard routing matches the vectors the Python dispatcher mirrors', () => {
  for (const [lane, callId, shard] of VECTORS) {
    assert.equal(exportsObject.__shardName(lane, callId), shard, callId);
  }
});
