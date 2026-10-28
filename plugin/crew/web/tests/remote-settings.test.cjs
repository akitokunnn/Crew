"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { create: createActions } = require("../ui-actions.js");
const flush = async () => {
  for (let i = 0; i < 4; i++)
    await new Promise((resolve) => setImmediate(resolve));
};
function fixture({ local = true, custom } = {}) {
  delete require.cache[require.resolve("../remote-settings.js")];
  const { create } = require("../remote-settings.js");
  const listeners = new Map(),
    secrets = new Map(),
    requests = [],
    loggedOut = [];
  let html = "",
    authenticated = true;
  const config = {
    enabled: false,
    origins: [],
    version: 0,
    password_configured: false,
    deployment: "not_verified",
    transport: "loopback-reverse-proxy",
    session_seconds: 86400,
  };
  const host = {
    classList: { add() {}, remove() {} },
    addEventListener: (key, fn) => listeners.set(key, fn),
    removeEventListener: (key, fn) => {
      if (listeners.get(key) === fn) listeners.delete(key);
    },
    querySelector: (selector) => {
      if (
        selector.includes('data-remote-field="new-passphrase"') ||
        selector.includes('data-remote-field="current-passphrase"')
      ) {
        if (!secrets.has(selector)) secrets.set(selector, { value: "" });
        return secrets.get(selector);
      }
      return null;
    },
  };
  Object.defineProperty(host, "innerHTML", {
    get: () => html,
    set: (value) => {
      html = value;
    },
  });
  async function request(path, body) {
    requests.push({
      path,
      body: body ? JSON.parse(JSON.stringify(body)) : null,
    });
    if (custom) {
      const result = custom({ path, body, config });
      if (result !== undefined) return result;
    }
    if (path === "/api/auth/session")
      return {
        local,
        authenticated,
        origin: local ? "http://localhost:12345" : "https://crew.example.test",
      };
    if (path === "/api/remote") return JSON.parse(JSON.stringify(config));
    if (path === "/api/remote/configure") {
      if (body.version !== config.version)
        throw Object.assign(Error("远程设置已变化"), { status: 409 });
      Object.assign(config, {
        enabled: body.enabled,
        origins: body.origins,
        version: config.version + 1,
        password_configured:
          config.password_configured || Boolean(body.passphrase),
      });
      if (!local) authenticated = false;
      return JSON.parse(JSON.stringify(config));
    }
    if (path === "/api/auth/logout") {
      authenticated = false;
      return { authenticated: false };
    }
    throw Error(`Unexpected path ${path}`);
  }
  const actions = createActions(),
    settingsOptions = {
      host,
      client: { request },
      actions,
      toast() {},
      onLoggedOut: (detail) => loggedOut.push(detail),
    };
  let module = create(settingsOptions);
  return {
    host,
    config,
    requests,
    loggedOut,
    secrets,
    get module() {
      return module;
    },
    open: () => module.open(),
    async dispatch(action) {
      actions.dispatch(action, { element: { dataset: {} } });
      await flush();
    },
    async input(field, value) {
      listeners.get("input")({
        target: { dataset: { remoteField: field }, value, checked: value },
      });
      await flush();
    },
    recreate() {
      module.destroy();
      module = create(settingsOptions);
    },
    actions,
  };
}

test("local remote configuration uses the real CAS contract and does not claim deployment", async () => {
  const f = fixture();
  await f.open();
  assert.match(f.host.innerHTML, /公网部署尚未验证/);
  await f.input("enabled", true);
  await f.input("origins", "https://crew.example.test");
  await f.input("new-passphrase", "secret-example-123");
  assert.doesNotMatch(f.host.innerHTML, /secret-example-123/);
  await f.dispatch("remote-settings-save");
  const body = f.requests.find(
    (item) => item.path === "/api/remote/configure",
  ).body;
  assert.deepEqual(body, {
    version: 0,
    enabled: true,
    origins: ["https://crew.example.test"],
    passphrase: "secret-example-123",
  });
  assert.match(f.host.innerHTML, /现有远程登录已失效/);
  assert.match(f.host.innerHTML, /公网部署仍未验证/);
  assert.equal(f.loggedOut.length, 0);
  assert.equal(
    [...f.secrets.values()].every((input) => input.value === ""),
    true,
  );
  f.module.destroy();
});

test("409 preserves the form and in-memory secret, requiring explicit review before a new version save", async () => {
  const f = fixture();
  await f.open();
  await f.input("enabled", true);
  await f.input("origins", "https://draft.example.test");
  await f.input("new-passphrase", "keep-in-memory-123");
  f.config.version = 1;
  f.config.origins = ["https://other.example.test"];
  await f.dispatch("remote-settings-save");
  assert.match(f.host.innerHTML, /当前表单保留/);
  assert.match(f.host.innerHTML, /draft.example.test/);
  await f.dispatch("remote-settings-save");
  assert.equal(
    f.requests.filter((item) => item.path === "/api/remote/configure").length,
    1,
  );
  await f.dispatch("remote-settings-reload");
  assert.match(f.host.innerHTML, /other.example.test/);
  assert.match(f.host.innerHTML, /draft.example.test/);
  await f.dispatch("remote-settings-keep");
  await f.dispatch("remote-settings-save");
  const body = f.requests
    .filter((item) => item.path === "/api/remote/configure")
    .at(-1).body;
  assert.equal(body.version, 1);
  assert.equal(body.passphrase, "keep-in-memory-123");
  f.module.destroy();
});

test("remote edits require current credentials and successful configuration ends the real session", async () => {
  const f = fixture({ local: false });
  await f.open();
  await f.input("origins", "https://crew.example.test");
  await f.input("new-passphrase", "new-secret-example-123");
  await f.dispatch("remote-settings-save");
  assert.match(f.host.innerHTML, /确认当前登录口令/);
  assert.equal(
    f.requests.some((item) => item.path === "/api/remote/configure"),
    false,
  );
  await f.input("current-passphrase", "current-secret-example-123");
  await f.dispatch("remote-settings-save");
  assert.equal(
    f.requests.find((item) => item.path === "/api/remote/configure").body
      .current_passphrase,
    "current-secret-example-123",
  );
  assert.deepEqual(f.loggedOut, [{ reason: "configuration_changed" }]);
  assert.match(f.host.innerHTML, /当前远程登录已失效/);
  f.module.destroy();
});

test("logout waits for the endpoint and destroyed/recreated views never retain passphrases", async () => {
  const f = fixture({ local: false });
  await f.open();
  await f.input("origins", "https://unsaved.example.test");
  await f.input("new-passphrase", "never-retained-123");
  f.recreate();
  await f.open();
  assert.match(f.host.innerHTML, /unsaved.example.test/);
  assert.equal(
    [...f.secrets.values()].every((input) => input.value === ""),
    true,
  );
  await f.dispatch("remote-settings-logout");
  assert.ok(f.requests.some((item) => item.path === "/api/auth/logout"));
  assert.deepEqual(f.loggedOut, [{ reason: "logout" }]);
  f.module.destroy();
  assert.equal(f.actions.dispatch("remote-settings-save", {}), false);
});

test("invalid HTTPS origins and unknown saves retain user input without automatic writes", async () => {
  let fail = true;
  const f = fixture({
    custom: ({ path }) => {
      if (path === "/api/remote/configure" && fail) {
        fail = false;
        throw Error("网络断开");
      }
    },
  });
  await f.open();
  await f.input("origins", "http://insecure.example.test/path");
  await f.dispatch("remote-settings-save");
  assert.match(f.host.innerHTML, /入口必须是完整 HTTPS/);
  assert.equal(
    f.requests.some((item) => item.path === "/api/remote/configure"),
    false,
  );
  await f.input("origins", "https://valid.example.test");
  await f.dispatch("remote-settings-save");
  assert.match(f.host.innerHTML, /保存结果尚未确认/);
  await f.dispatch("remote-settings-save");
  assert.equal(
    f.requests.filter((item) => item.path === "/api/remote/configure").length,
    1,
  );
  f.module.destroy();
});
