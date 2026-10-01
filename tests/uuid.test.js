"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");

function context(crypto) {
  const sandbox = vm.createContext({ crypto });
  vm.runInContext(readFileSync(join(__dirname, "../app/static/workspace.js"), "utf8"), sandbox);
  return sandbox;
}
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
test("private HTTP without randomUUID generates distinct secure UUIDs", () => {
  const sandbox = context({ getRandomValues: bytes => webcrypto.getRandomValues(bytes) });
  const ids = new Set();
  for (let i = 0; i < 500; i++) {
    const id = vm.runInContext("requestUUID()", sandbox);
    assert.match(id, uuidPattern);
    ids.add(id);
  }
  assert.equal(ids.size, 500);
});
test("fallback preserves entropy while setting UUID version and variant", () => {
  const sandbox = context({ getRandomValues: bytes => bytes.fill(255) });
  assert.equal(vm.runInContext("requestUUID()", sandbox), "ffffffff-ffff-4fff-bfff-ffffffffffff");
});
test("native UUID retains the Crypto receiver", () => {
  const source = { randomUUID() { assert.equal(this, source); return "native"; } };
  assert.equal(vm.runInContext("requestUUID()", context(source)), "native");
});
test("missing secure randomness fails explicitly", () => {
  assert.throws(() => vm.runInContext("requestUUID()", context(undefined)), /secure request IDs/);
});
