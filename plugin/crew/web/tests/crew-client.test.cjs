"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { create } = require("../crew-client.js");
const mock = require("../crew-mock.js");
const { createServer } = require("../dev-server.cjs");

function memory() {
  const map = new Map();
  return {
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => map.set(key, value),
    keys: () => [...map.keys()],
  };
}
function fixture(scenario = "", storage = memory()) {
  const environment = {
    Headers,
    Response,
    ReadableStream,
    TextEncoder,
    crypto,
    atob,
    btoa,
    setTimeout,
    clearTimeout,
    location: { origin: "http://127.0.0.1:4320" },
    fetch: () => {
      throw Error("Mock must never call the network");
    },
  };
  const transport = mock.create({
    storage,
    scenario,
    environment,
    replyDelay: 15,
  });
  return {
    client: create({ environment, mode: "mock", transport }),
    transport,
    storage,
  };
}
test("folder fallback verifies local authentication and retains the primary project instead of opening a remote host", async () => {
  const calls = [];
  let local = false;
  const environment = {
    location: { origin: "http://127.0.0.1:4321" },
    fetch: async (path, init) => {
      calls.push({ path, init });
      return Response.json(
        path === "/api/auth/session"
          ? { local, authenticated: true }
          : { opened: true },
      );
    },
  };
  const client = create({ environment });
  const context = { session_id: "leader-id", project: "普通文件夹" };
  assert.equal(
    (await client.desktop.openFolder("C:/Sample", context)).opened,
    false,
  );
  assert.equal(calls.length, 1);
  local = true;
  assert.equal(
    (await client.desktop.openFolder("C:/Sample", context)).opened,
    true,
  );
  assert.equal(
    new URL(calls[2].path, environment.location.origin).searchParams.get(
      "project",
    ),
    context.project,
  );
  assert.equal(JSON.parse(calls[2].init.body).session_id, context.session_id);
});
test("sample decisions return to the original task and preserve a real explicit answer without network calls", async () => {
  const { client } = fixture();
  const snapshot = await client.request("/api/state?project=Crew");
  const decision = snapshot.decisions[0];
  const attention = await client.request("/api/tasks/attention");
  assert.equal(
    attention.items.find((item) => item.kind === "decision").decision_id,
    decision.id,
  );
  await client.request("/api/decision/ready", { id: decision.id });
  const ready = await client.request("/api/state?project=Crew");
  assert.equal(ready.decisions[0].presentation_stage, "ready");
  assert.ok(ready.decisions[0].brief.options.every((item) => item.consequence));
  await client.request("/api/answer", { id: decision.id, text: "再轻一点" });
  assert.equal(
    (await client.request("/api/state?project=Crew")).decisions.length,
    0,
  );
});
const q = (id = mock.ids.TASK, session = mock.ids.LEADER) =>
  new URLSearchParams({
    project: "voice-demo",
    goal_id: id,
    session_id: session,
  });
const requestID = () => crypto.randomUUID();
const delay = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

test("real transport sends same-origin JSON once, retains HTTP details and never changes mode", async () => {
  const calls = [];
  const environment = Object.create(globalThis);
  environment.location = { origin: "http://127.0.0.1:4320" };
  environment.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(
      JSON.stringify({ error: "任务已变化", pending_id: "pending-1" }),
      { status: 409 },
    );
  };
  const client = create({ environment });
  await assert.rejects(
    client.request("/api/tasks/update", { task_id: "t1", version: 1 }),
    (error) => error.status === 409 && error.details.pending_id === "pending-1",
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/tasks/update");
  assert.equal(calls[0].options.headers.get("X-Crew"), "1");
  assert.equal(calls[0].options.credentials, "same-origin");
  assert.equal(JSON.parse(calls[0].options.body).version, 1);
  assert.equal(client.mode, "real");
  assert.throws(
    () => client.resourceURL("https://other.invalid/api/tasks"),
    /同源/,
  );
});

test("raw fetch returns the real HTTP response and text handles non-JSON failures", async () => {
  const environment = Object.create(globalThis);
  environment.location = { origin: "http://127.0.0.1:4320" };
  environment.fetch = async () =>
    new Response("upstream unavailable", { status: 502 });
  const client = create({ environment });
  assert.equal((await client.fetch("/api/state")).status, 502);
  await assert.rejects(
    client.text("/api/goals/report"),
    (error) => error.status === 502 && error.message === "upstream unavailable",
  );
});

test("discussion creation is persisted and idempotent without starting execution", async (t) => {
  const { client, transport, storage } = fixture();
  t.after(() => transport.close());
  const body = {
    project: "voice-demo",
    text: "讨论新的识别边界",
    request_id: requestID(),
  };
  const first = await client.request("/api/tasks/create", body);
  const second = await client.request("/api/tasks/create", body);
  assert.equal(first.task.id, second.task.id);
  assert.equal(first.task.state, "discussion");
  assert.equal(first.task.authorized, false);
  assert.equal(
    storage.keys().every((key) => key.startsWith(mock.NAMESPACE)),
    true,
  );
  const reloaded = fixture("", storage);
  t.after(() => reloaded.transport.close());
  const tasks = await reloaded.client.request("/api/tasks?include_inactive=1");
  assert.equal(
    tasks.tasks.filter((item) => item.id === first.task.id).length,
    1,
  );
  await assert.rejects(
    client.request("/api/tasks/create", { ...body, text: "changed" }),
    (error) => error.status === 409,
  );
});

test("task references preserve identity, pin affects display only, and CAS rejects stale mode changes", async (t) => {
  const { client, transport } = fixture();
  t.after(() => transport.close());
  const left = await client.request("/api/tasks?project=voice-demo");
  const right = await client.request("/api/tasks?project=evaluation-demo");
  assert.equal(left.tasks[0].id, right.tasks[0].id);
  const value = left.tasks[0];
  const pinned = await client.request("/api/tasks/pin", {
    task_id: value.id,
    pinned: false,
    request_id: requestID(),
  });
  assert.equal(pinned.task.state, value.state);
  assert.equal(pinned.task.pinned, false);
  assert.equal(pinned.task.version, value.version);
  const updated = await client.request("/api/tasks/mode", {
    task_id: value.id,
    version: pinned.task.version,
    mode: "automatic",
    request_id: requestID(),
  });
  assert.equal(updated.task.mode, "automatic");
  await assert.rejects(
    client.request("/api/tasks/mode", {
      task_id: value.id,
      version: value.version,
      mode: "manual",
      request_id: requestID(),
    }),
    (error) => error.status === 409,
  );
});

test("current conversation interruption leaves task and another member intact; task stop is separate", async (t) => {
  const { client, transport } = fixture();
  t.after(() => transport.close());
  const query = q();
  const memberQuery = q(mock.ids.TASK, mock.ids.MEMBER);
  await client.request(`/api/chat/history?${memberQuery}`);
  await client.request("/api/chat/send?project=voice-demo", {
    goal_id: mock.ids.TASK,
    session_id: mock.ids.LEADER,
    text: "补充要求",
    message_id: requestID(),
  });
  await client.request("/api/chat/send?project=voice-demo", {
    goal_id: mock.ids.TASK,
    session_id: mock.ids.MEMBER,
    text: "继续验证",
    message_id: requestID(),
  });
  await client.request("/api/chat/interrupt?project=voice-demo", {
    goal_id: mock.ids.TASK,
    session_id: mock.ids.LEADER,
  });
  const member = await client.request(`/api/chat/history?${memberQuery}`);
  assert.equal(member.busy, true);
  assert.equal(
    (await client.request("/api/task?task_id=" + mock.ids.TASK)).task.state,
    "running",
  );
  const task = (await client.request("/api/task?task_id=" + mock.ids.TASK))
    .task;
  await client.request("/api/tasks/stop", {
    task_id: task.id,
    version: task.version,
    request_id: requestID(),
  });
  assert.equal(
    (await client.request(`/api/chat/history?${memberQuery}`)).busy,
    false,
  );
  assert.equal(
    (await client.request(`/api/chat/history?${query}`)).events.at(-1).status,
    "idle",
  );
  assert.equal(
    (await client.request("/api/task?task_id=" + mock.ids.OTHER)).task.state,
    "waiting_user",
  );
});

test("queued messages wait for the current turn and stopped tasks never drain their queue", async (t) => {
  const { client, transport } = fixture();
  t.after(() => transport.close());
  await client.request("/api/chat/send?project=voice-demo", {
    goal_id: mock.ids.TASK,
    session_id: mock.ids.LEADER,
    text: "当前工作",
    message_id: requestID(),
  });
  const task = (await client.request("/api/task?task_id=" + mock.ids.TASK))
    .task;
  const result = await client.request("/api/tasks/schedule", {
    task_id: task.id,
    version: task.version,
    text: "下一步验证",
    evidence: "用户排队",
    request_id: requestID(),
  });
  assert.equal(result.task.queue[0].status, "queued");
  const before = await client.request(`/api/chat/history?${q()}`);
  assert.equal(
    before.events.some((item) => item.text === "下一步验证"),
    false,
  );
  await delay(40);
  const after = await client.request(`/api/chat/history?${q()}`);
  assert.equal(
    after.events.filter((item) => item.text === "下一步验证").length,
    1,
  );
  const current = (await client.request("/api/task?task_id=" + mock.ids.TASK))
    .task;
  const queued = await client.request("/api/tasks/schedule", {
    task_id: current.id,
    version: current.version,
    text: "停止后不可执行",
    evidence: "用户排队",
    request_id: requestID(),
  });
  await client.request("/api/tasks/stop", {
    task_id: current.id,
    version: queued.task.version,
    request_id: requestID(),
  });
  await delay(25);
  const stopped = await client.request(`/api/chat/history?${q()}`);
  assert.equal(
    stopped.events.some((item) => item.text === "停止后不可执行"),
    false,
  );
});

test("unknown receipt keeps one stable identity and never manufactures a reply", async (t) => {
  const { client, transport } = fixture("unknown");
  t.after(() => transport.close());
  const body = {
    goal_id: mock.ids.TASK,
    session_id: mock.ids.LEADER,
    text: "未确认的输入",
    message_id: requestID(),
  };
  const result = await client.request(
    "/api/chat/send?project=voice-demo",
    body,
  );
  assert.equal(result.status, "unknown");
  assert.equal(result.accepted, false);
  assert.deepEqual(
    await client.request("/api/chat/send?project=voice-demo", body),
    result,
  );
  const history = await client.request(`/api/chat/history?${q()}`);
  assert.equal(
    history.events.some((item) => item.id === body.message_id),
    false,
  );
});

test("SSE uses journal sequences, replays only after cursor, and closes without late events", async (t) => {
  const { client, transport } = fixture();
  t.after(() => transport.close());
  const history = await client.request(`/api/chat/history?${q()}`);
  const events = [],
    source = client.eventSource(`/api/chat/events?${q()}&after=3`);
  t.after(() => source.close());
  source.addEventListener("message", (event) =>
    events.push(JSON.parse(event.data)),
  );
  await new Promise((resolve) => {
    source.onopen = resolve;
  });
  await client.request("/api/chat/send?project=voice-demo", {
    goal_id: mock.ids.TASK,
    session_id: mock.ids.LEADER,
    text: "流式样例",
    message_id: requestID(),
  });
  await delay(30);
  assert.ok(
    events.some(
      (item) => item.type === "input_delivery" && item.status === "accepted",
    ),
  );
  assert.ok(events.some((item) => item.type === "assistant"));
  assert.ok(
    events.every(
      (item, index) =>
        item.seq > 3 &&
        item.execution_id === history.execution_id &&
        (!index || item.seq > events[index - 1].seq),
    ),
  );
  const length = events.length;
  source.close();
  await client.request("/api/chat/interrupt?project=voice-demo", {
    goal_id: mock.ids.TASK,
    session_id: mock.ids.LEADER,
  });
  assert.equal(events.length, length);
  assert.equal(source.readyState, 2);
});

test("fetch SSE is a real readable Response, supports Last-Event-ID and AbortSignal", async (t) => {
  const { client, transport } = fixture();
  t.after(() => transport.close());
  const controller = new AbortController();
  const response = await client.fetch(`/api/chat/events?${q()}&after=1`, {
    signal: controller.signal,
    headers: { "Last-Event-ID": "2" },
  });
  assert.match(response.headers.get("content-type"), /event-stream/);
  const reader = response.body.getReader();
  const first = new TextDecoder().decode((await reader.read()).value);
  assert.match(first, /id: 3\n/);
  assert.doesNotMatch(first, /id: 2\n/);
  controller.abort();
  assert.equal((await reader.read()).done, true);
});

test("settings and prompts expose effective inheritance and next-launch/next-turn boundaries", async (t) => {
  const { client, transport } = fixture();
  t.after(() => transport.close());
  const scoped = await client.request(
    "/api/settings/scope?scope=project&identity=voice-demo",
  );
  await client.request("/api/settings/scope", {
    scope: "project",
    identity: "voice-demo",
    version: scoped.version,
    ai: {
      default: { runtime: "codex", model: "synthetic-codex", effort: "high" },
    },
    request_id: requestID(),
  });
  const effective = await client.request(
    "/api/settings/effective?project=voice-demo",
  );
  assert.equal(effective.ai.runtime, "codex");
  assert.equal(effective.sources.ai.runtime.scope, "project");
  assert.equal(effective.applies, "next_launch");
  assert.equal(effective.current_execution_changed, false);
  const prompt = await client.request(
    "/api/prompts?key=leader&scope=project&identity=voice-demo",
  );
  const saved = await client.request("/api/prompts/save", {
    key: "leader",
    scope: "project",
    identity: "voice-demo",
    version: prompt.version,
    text: "按本项目要求推进",
    request_id: requestID(),
  });
  assert.equal(saved.effective.content, "按本项目要求推进");
  assert.equal(saved.applies, "next_turn");
  const reset = await client.request("/api/prompts/reset", {
    key: "leader",
    scope: "project",
    identity: "voice-demo",
    version: saved.version,
    request_id: requestID(),
  });
  assert.equal(reset.overridden, false);
  assert.equal(reset.effective.source.scope, "default");
});

test("report conflict preserves proposed text, version restoration requires exact preview", async (t) => {
  const { client, transport } = fixture();
  t.after(() => transport.close());
  const before = await client.request(
    "/api/tasks/report?task_id=" + mock.ids.TASK,
  );
  await assert.rejects(
    client.request("/api/tasks/report", {
      task_id: mock.ids.TASK,
      version: 0,
      markdown: "用户的报告草稿",
      request_id: requestID(),
    }),
    (error) => error.status === 409 && Boolean(error.details.pending_id),
  );
  const after = await client.request(
    "/api/tasks/report?task_id=" + mock.ids.TASK,
  );
  assert.equal(after.report.markdown, before.report.markdown);
  assert.equal(after.pending[0].markdown, "用户的报告草稿");
  const preview = await client.request("/api/tasks/versions/preview", {
    task_id: mock.ids.TASK,
    version_id: "version-02",
  });
  await assert.rejects(
    client.request("/api/tasks/versions/restore", {
      task_id: mock.ids.TASK,
      version_id: "version-02",
      expected_files: [],
      request_id: requestID(),
    }),
    (error) => error.status === 409,
  );
  const restored = await client.request("/api/tasks/versions/restore", {
    task_id: mock.ids.TASK,
    version_id: "version-02",
    expected_files: preview.expected_files,
    request_id: requestID(),
  });
  assert.equal(restored.restored, true);
});

test("attachments return exact uploaded bytes through blob and resourceURL", async (t) => {
  const { client, transport } = fixture();
  t.after(() => transport.close());
  const text = "中文附件\nexact bytes";
  const { attachment } = await client.request("/api/tasks/attachments", {
    task_id: mock.ids.TASK,
    filename: "说明.txt",
    mime_type: "text/plain",
    data: Buffer.from(text).toString("base64"),
    request_id: requestID(),
  });
  assert.equal(await (await client.blob(attachment.url)).text(), text);
  assert.match(client.resourceURL(attachment.url), /^data:text\/plain;base64,/);
  assert.equal(
    Buffer.from(
      client.resourceURL(attachment.url).split(",")[1],
      "base64",
    ).toString(),
    text,
  );
});

test("mock unknown APIs fail explicitly, empty/offline scenarios do not fall back, desktop reports limits", async (t) => {
  for (const scenario of ["", "empty", "offline"]) {
    const { client, transport } = fixture(scenario);
    t.after(() => transport.close());
    if (scenario === "offline")
      await assert.rejects(client.request("/api/tasks"), /样例断连/);
    else if (scenario === "empty")
      assert.equal((await client.request("/api/tasks")).tasks.length, 0);
    else
      await assert.rejects(
        client.request("/api/not-implemented", {}),
        (error) => error.status === 501,
      );
    assert.equal(
      (await client.desktop.openFolder("C:\\Samples")).opened,
      false,
    );
  }
});

test("attention paginates contract items and never duplicates the shared task", async (t) => {
  const { client, transport } = fixture();
  t.after(() => transport.close());
  const first = await client.request("/api/tasks/attention?limit=1");
  assert.equal(first.items.length, 1);
  assert.equal(first.offset, 0);
  assert.equal(first.next_offset, 1);
  assert.equal(first.items[0].task_id, mock.ids.TASK);
  const next = await client.request("/api/tasks/attention?limit=1&offset=1");
  assert.equal(next.items.length, 1);
  assert.equal(next.total, first.total);
  assert.equal(
    ["decision", "todo", "blocked", "review"].includes(next.items[0].kind),
    true,
  );
});

test("sample auth/remote settings remain visibly synthetic, persist no passphrase and cannot stop real service", async (t) => {
  const storage = memory(),
    { client, transport } = fixture("", storage);
  t.after(() => transport.close());
  const remote = await client.request("/api/remote");
  const passphrase = "a-real-looking-secret-never-store";
  const saved = await client.request("/api/remote/configure", {
    version: remote.version,
    enabled: true,
    origins: ["https://sample.invalid"],
    passphrase,
    request_id: requestID(),
  });
  assert.equal(saved.synthetic, true);
  assert.equal(saved.deployed, false);
  assert.equal(saved.password_configured, true);
  assert.equal(
    storage.getItem(transport.namespace).includes(passphrase),
    false,
  );
  assert.equal((await client.request("/api/admin/service")).can_stop, false);
  await assert.rejects(
    client.request("/api/admin/service/stop", {}),
    (error) => error.status === 501,
  );
  await client.request("/api/auth/logout", {});
  assert.equal(
    (await client.request("/api/auth/session")).authenticated,
    false,
  );
  await assert.rejects(
    client.request("/api/tasks"),
    (error) => error.status === 401 && error.details.code === "unauthorized",
  );
  const login = await client.request("/api/auth/login", {
    passphrase,
    request_id: requestID(),
  });
  assert.equal(login.authenticated, true);
  assert.equal(login.synthetic, true);
  assert.equal(
    storage.getItem(transport.namespace).includes(passphrase),
    false,
  );
  assert.ok((await client.request("/api/tasks")).tasks.length);
});

async function listening(server) {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return "http://127.0.0.1:" + server.address().port;
}
test("development server serves only safe static assets and has no implicit API backend", async (t) => {
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "crew-frontend-test-"),
  );
  fs.writeFileSync(
    path.join(directory, "index.html"),
    "<title>synthetic</title>",
  );
  fs.writeFileSync(path.join(directory, "private.cjs"), "secret");
  const server = createServer({ root: directory });
  const url = await listening(server);
  t.after(async () => {
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(directory, { recursive: true, force: true });
  });
  assert.match(await (await fetch(url + "/")).text(), /synthetic/);
  assert.equal((await fetch(url + "/private.cjs")).status, 404);
  assert.equal((await fetch(url + "/%2e%2e%5cprivate.cjs")).status, 403);
  const noBackend = await fetch(url + "/api/tasks");
  assert.equal(noBackend.status, 503);
  assert.match((await noBackend.json()).error, /显式传 --backend/);
});

test("explicit local backend proxy preserves SSE/bytes and rejects foreign origins and public upstreams", async (t) => {
  let observed;
  const upstream = http.createServer((request, response) => {
    observed = { host: request.headers.host, origin: request.headers.origin };
    if (request.url === "/api/events") {
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      response.end('id: 1\ndata: {"synthetic":true}\n\n');
    } else {
      response.writeHead(200, { "Content-Type": "application/octet-stream" });
      response.end(Buffer.from([0, 1, 128, 255]));
    }
  });
  const backend = await listening(upstream),
    server = createServer({ backend }),
    url = await listening(server);
  t.after(async () => {
    await new Promise((resolve) => server.close(resolve));
    await new Promise((resolve) => upstream.close(resolve));
  });
  const response = await fetch(url + "/api/events", {
    headers: { Origin: url },
  });
  assert.match(await response.text(), /id: 1/);
  assert.equal(observed.host, new URL(backend).host);
  assert.equal(observed.origin, backend);
  assert.deepEqual(
    [...new Uint8Array(await (await fetch(url + "/api/bytes")).arrayBuffer())],
    [0, 1, 128, 255],
  );
  assert.equal(
    (
      await fetch(url + "/api/bytes", {
        headers: { Origin: "https://untrusted.invalid" },
      })
    ).status,
    403,
  );
  assert.throws(
    () => createServer({ backend: "https://public.invalid" }),
    /loopback/,
  );
});
