"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require.resolve("../workbench-ui.js"), "utf8");
function load(mode, storage, search = "") {
  const window = { CrewClient: { current: { mode } } };
  vm.runInNewContext(source, {
    window,
    sessionStorage: storage,
    URLSearchParams,
    location: { search },
    crypto: require("node:crypto").webcrypto,
  });
  return window.CrewWorkbenchUI;
}
test("mock and real receipts, drafts and view state never cross the transport boundary", () => {
  const values = new Map(),
    storage = {
      setItem: (key, value) => values.set(key, value),
      getItem: (key) => values.get(key) ?? null,
    };
  const mock = load("mock", storage, "?data=mock");
  mock.remember("home.conversation", {
    task: { id: "synthetic-task" },
    message_id: "synthetic-input",
  });
  mock.remember("home.capture", "样例草稿");
  const real = load("real", storage);
  assert.equal(real.recall("home.conversation", null), null);
  assert.equal(real.recall("home.capture", ""), "");
  real.remember("home.conversation", {
    task: { id: "real-task" },
    message_id: "uncertain-real-input",
  });
  const revisited = load("mock", storage, "?data=mock");
  assert.equal(
    revisited.recall("home.conversation", null).task.id,
    "synthetic-task",
  );
  assert.equal(
    load("real", storage).recall("home.conversation", null).message_id,
    "uncertain-real-input",
  );
});
test("existing capability links retain both workspace and explicit data selection", () => {
  const ui = load("mock", { getItem: () => null, setItem() {} }, "?data=mock");
  const url = new URL(
    ui.legacyURL("普通 文件夹", "git"),
    "http://fixture.local",
  );
  assert.equal(url.searchParams.get("legacy"), "1");
  assert.equal(url.searchParams.get("project"), "普通 文件夹");
  assert.equal(url.searchParams.get("data"), "mock");
  assert.equal(url.hash, "#git");
});
