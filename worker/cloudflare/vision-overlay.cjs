// Merge the DARK ValmeraVision overlay (vision.jsonc) into a parsed
// wrangler config. Used by .github/workflows/deploy-cloudflare-executor.yml
// only when the repository variable VALMERA_VISION_LANE == '1'; tested by
// tests/vision.test.cjs. Pure: returns a new object and never touches files
// unless run as a script with --write.
"use strict";
const fs = require("node:fs");
const path = require("node:path");

const OVERLAY = path.join(__dirname, "vision.jsonc");

function readOverlay(file = OVERLAY) {
  const overlay = JSON.parse(fs.readFileSync(file, "utf8"));
  delete overlay._comment;
  return overlay;
}

function mergeVisionOverlay(config, overlay = readOverlay()) {
  const out = structuredClone(config);
  const containers = out.containers || [];
  for (const c of overlay.containers || []) {
    if (containers.some((x) => x.class_name === c.class_name)) {
      throw new Error(`container ${c.class_name} is already configured`);
    }
    containers.push(structuredClone(c));
  }
  out.containers = containers;
  out.durable_objects = out.durable_objects || { bindings: [] };
  const bindings = out.durable_objects.bindings || [];
  for (const b of (overlay.durable_objects || {}).bindings || []) {
    if (bindings.some((x) => x.name === b.name || x.class_name === b.class_name)) {
      throw new Error(`durable object ${b.name} is already bound`);
    }
    bindings.push(structuredClone(b));
  }
  out.durable_objects.bindings = bindings;
  const migrations = out.migrations || [];
  for (const m of overlay.migrations || []) {
    if (migrations.some((x) => x.tag === m.tag)) {
      throw new Error(`migration ${m.tag} already exists`);
    }
    migrations.push(structuredClone(m));
  }
  out.migrations = migrations;
  return out;
}

module.exports = { mergeVisionOverlay, readOverlay, OVERLAY };

if (require.main === module) {
  // node vision-overlay.cjs --write wrangler.jsonc
  const target = process.argv[process.argv.indexOf("--write") + 1];
  if (!process.argv.includes("--write") || !target) {
    console.error("usage: node vision-overlay.cjs --write <wrangler.jsonc>");
    process.exit(2);
  }
  const merged = mergeVisionOverlay(JSON.parse(fs.readFileSync(target, "utf8")));
  fs.writeFileSync(target, `${JSON.stringify(merged, null, 2)}\n`);
  console.log("merged the ValmeraVision overlay into", target);
}
