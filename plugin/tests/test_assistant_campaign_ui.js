"use strict";
// The assistant page's "等你确认" panel shows an opening plan (campaign.launch) as a card read from campaign.preview,
// and a war-room budget raise (war_room.budget) with the room's spend; other requests keep the plain argument line.
const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm"), fs = require("node:fs"), path = require("node:path");
const web = file => fs.readFileSync(path.join(__dirname, "../../frontend", file), "utf8");
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };

function element(tag) {
  const node = {tag, hidden: false, open: false, modalOpens: 0, className: "", textContent: "", innerHTML: "", disabled: false, onclick: null, dataset: {}, children: [],
    append(...items) { node.children.push(...items); }, replaceChildren(...items) { node.children = items; },
    showModal() { node.open = true; node.modalOpens++; }, close() { node.open = false; },
    addEventListener() {}, querySelectorAll: selector => node.children.filter(child => child.tag === selector)};
  return node;
}
function assistantPage(requests, {preview, room, restructure, failPreview = false, wide = true, storage = new Map(),
  resolve = async () => ({ok: true, status: 200, json: async () => ({status: "approved"})}),
  search = "", android = false, desktop = false} = {}) {
  const nodes = new Map(), calls = [];
  const voiceButtons = [], nativeCalls = [];
  let nativeReply;
  const composer = {querySelector: selector => {
    if (selector === ".send") return {before: button => voiceButtons.push(button)};
    return voiceButtons.find(button => button.className.includes(selector.slice(1)));
  }};
  let refresh;
  const sandbox = {URLSearchParams, URL, Date, JSON, Promise, console,
    matchMedia: query => ({matches: query === "(min-width:761px)" ? wide : false, addEventListener() {}}),
    localStorage: {getItem: key => storage.has(key) ? storage.get(key) : null, setItem: (key, value) => storage.set(key, value)},
    location: {hash: "", pathname: "/assistant.html", search, href: "http://127.0.0.1/assistant.html" + search},
    history: {replaceState() {}}, setTimeout: (fn, delay) => { if (delay === 4000) refresh = fn; }, clearTimeout() {}, addEventListener() {},
    document: {documentElement: {dataset: {}}, body: {classList: {contains: name => name === "assistant-page"}},
      addEventListener() {}, createElement: tag => ({...element(tag), setAttribute() {}}), createTextNode: text => ({text}),
      getElementById: id => {
        if (!nodes.has(id)) nodes.set(id, element("div"));
        const node = nodes.get(id);
        if (id === "assistant-chat") node.querySelector = () => composer;
        return node;
      }},
    fetch: async (url, options) => {
      const body = options.body === undefined ? undefined : JSON.parse(options.body);
      calls.push({url, body});
      if (url === "/api/op/interaction.resolve") return resolve(body);
      if (url === "/api/op/campaign.preview" && failPreview) return {ok: false, status: 409, json: async () => ({error: "工作区不存在"})};
      const value = url.startsWith("/api/op/assistant.state") ? {assistant: {session_id: "assistant"}, projects: [], todos: [], goals: [], sounds: {}, now: "now"}
        : url.startsWith("/api/op/interaction.list") ? {requests}
        : url === "/api/op/campaign.preview" ? preview
        : url === "/api/op/domains.restructure.preview" ? restructure
        : url.startsWith("/api/op/war_room.get?") ? room
        : {};
      return {ok: true, status: 200, json: async () => value};
    },
    CrewSound: {observe() {}}, CrewPersonalNotes: {create: () => ({open() {}})},
    CrewChat: {initFiles() {}, unmount() {}, update() {}, mount() {}, setNote() {}, prefill: () => true}};
  sandbox.window = sandbox;
  sandbox.crewDesktopTheme = desktop;
  if (android) sandbox.CrewAndroid = {
    addEventListener: (type, listener) => { nativeReply = listener; },
    postMessage: raw => {
      const request = JSON.parse(raw);
      nativeCalls.push(request);
      nativeReply({data: JSON.stringify({id: request.id, ok: true, result: null})});
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(web("browser-utils.js"), sandbox);
  vm.runInContext(web("restructure-ui.js"), sandbox);
  vm.runInContext(web("mobile-shell.js"), sandbox);
  vm.runInContext(web("assistant.js"), sandbox);
  return {calls, voiceButtons, nativeCalls, refresh: () => refresh(), dialog: () => nodes.get("assistant-confirmation-dialog"),
    trigger: () => nodes.get("assistant-confirmation-trigger"), status: () => nodes.get("assistant-confirmation-status"),
    cards: () => nodes.get("assistant-confirmation-cards").children};
}
const detail = card => card.children.find(child => child.className === "ac-detail");
const text = card => card.children.map(child => child.textContent).join("\n");

const plan = {project: "fitness", note: "健身打卡开局：后端、App 两条线并行", start: true, board_version: 4, request_id: "r-1",
  parts: [{ref: "backend01", name: "后端", parent: null}, {ref: "api0001", name: "接口", parent: "backend01"}],
  leaders: [{name: "后端", runtime: "claude", model: "opus", effort: "high", part: "backend01", lead: true, brief: "负责打卡接口与数据表，<b>先</b>定接口"},
    {name: "App", runtime: "codex", model: "gpt-6", effort: null, part: null, brief: "做 App 打卡页"}],
  goals: [{leader: "后端", title: "打卡接口 v1", request: "实现打卡与查询接口", acceptance: ["有接口文档", "有单测"]}],
  room: {title: "开局", goal: "两周内跑通打卡", budget_tokens: 200000}};
const preview = (extra = {}) => ({project: "fitness", ok: true, errors: [], board_version: 4,
  parts: plan.parts, goals: plan.goals, room: plan.room, start: true,
  leaders: [{...plan.leaders[0], part_name: "后端"}, {...plan.leaders[1], lead: false, part_name: null}],
  cost: {start: true, new_leaders: 2, budget_tokens: 200000,
    carriers: [{runtime: "claude", model: "opus", effort: "high", count: 1}, {runtime: "codex", model: "gpt-6", effort: "默认强度", count: 1}],
    estimate: {low_usd: 0.6, high_usd: 15, priced_models: ["opus"]},
    note: "确认后立即启动 2 位 leader 并开始首批任务；作战室预算到了会暂停等你确认。"}, ...extra});
const launch = {id: "c1", op: "campaign.launch", role: "assistant", project: "fitness", summary: "执行开局方案", args: plan};
const plainRequest = id => ({id, op: "session.delete", role: "assistant", project: "fitness", summary: "删除会话",
  args: {name: "后端"}, arg_labels: {name: "会话名称"}});

test("a new request opens once, stays dismissed after reload, and a later request opens again", async () => {
  const storage = new Map(), pending = [plainRequest("first")];
  const first = assistantPage(pending, {storage});
  await settle();
  assert.equal(first.dialog().open, true);
  first.dialog().close();
  await first.refresh();
  assert.equal(first.dialog().modalOpens, 1);

  const reloaded = assistantPage(pending, {storage});
  await settle();
  assert.equal(reloaded.dialog().open, false);
  assert.equal(reloaded.trigger().textContent, "等你确认 (1)");
  reloaded.trigger().onclick();
  assert.equal(reloaded.dialog().open, true);
  reloaded.dialog().close();
  pending.push(plainRequest("second"));
  await reloaded.refresh();
  assert.equal(reloaded.dialog().open, true);
  assert.equal(reloaded.trigger().textContent, "等你确认 (2)");
  reloaded.dialog().close();
  await reloaded.refresh();
  assert.equal(reloaded.dialog().open, false);
});

test("a confirmation in flight submits once even when another card arrives and redraws the dialog", async () => {
  let complete;
  const pending = [plainRequest("first")];
  const page = assistantPage(pending, {resolve: () => new Promise(resolve => { complete = resolve; })});
  await settle();
  const buttons = () => page.cards()[0].children.find(child => child.className === "assistant-confirmation-actions").children;
  const confirming = buttons()[0].onclick();
  await buttons()[0].onclick();
  pending.push(plainRequest("second"));
  await page.refresh();
  assert(buttons().every(button => button.disabled));
  await buttons()[1].onclick();
  complete({ok: true, status: 200, json: async () => ({status: "approved"})});
  await confirming;
  assert.equal(page.calls.filter(call => call.url === "/api/op/interaction.resolve").length, 1);
  assert.equal(page.trigger().textContent, "等你确认 (1)");
  assert.equal(page.status().textContent, "已确认并执行");
});

test("a failed request is shown inside the dialog and can be retried manually", async () => {
  const page = assistantPage([plainRequest("first")], {resolve: async () => ({
    ok: false, status: 503, json: async () => ({error: "连接断开"})})});
  await settle();
  const confirm = () => page.cards()[0].children.find(child => child.className === "assistant-confirmation-actions").children[0];
  await confirm().onclick();
  assert.equal(page.dialog().open, true);
  assert.equal(page.status().textContent, "连接断开");
  assert.equal(confirm().disabled, false);
  assert.equal(page.calls.filter(call => call.url === "/api/op/interaction.resolve").length, 1);
});

test("an opening plan becomes a card read from campaign.preview: cost first, then parts, leaders, first tasks and the room", async () => {
  const page = assistantPage([launch], {preview: preview()});
  await settle();
  const previews = page.calls.filter(call => call.url === "/api/op/campaign.preview");
  assert.equal(previews.length, 1);
  const {board_version, request_id, ...sent} = plan;
  assert.deepEqual(previews[0].body, sent, "the plan with its project, without the launch-only board version");
  const [card] = page.cards(), html = detail(card).innerHTML;
  assert.match(text(card), /^开局方案\n由系统管理员发起 · 工作区 fitness\n方案说明：健身打卡开局：后端、App 两条线并行/);
  assert(!html.includes("ac-problems"));
  const cost = html.slice(html.indexOf('class="ac-cost"'), html.indexOf('class="ac-build"'));
  assert(html.indexOf('class="ac-cost"') < html.indexOf('class="ac-build"'), "what it costs comes before what it builds");
  assert(cost.includes("确认后立即启动 2 位 leader") && cost.includes("<b>2 位</b>"));
  assert(cost.includes('<span class="ac-rt" data-rt="claude">Claude</span><span>opus · high</span><b>× 1</b>'));
  assert(cost.includes('<span class="ac-rt" data-rt="codex">Codex</span><span>gpt-6 · 默认强度</span><b>× 1</b>'));
  assert(cost.includes("<b>20 万 tokens</b>") && cost.includes("<b>$0.6 – $15</b>") && cost.includes("按 opus 的价格估"));
  assert(!cost.includes("只建架构图") && !cost.includes("<details"), "the cost part never folds");
  assert(html.includes("<b>接口</b><small>挂在 后端 下</small>") && html.includes("<b>后端</b><small>顶层</small>"));
  assert(html.includes('<span class="ac-tag">负责</span><small>放进 后端</small>') && html.includes("<small>不放进领域</small>"));
  assert(html.includes("&lt;b&gt;先&lt;/b&gt;") && !html.includes("<b>先</b>"), "plan text is escaped");
  assert(html.includes("<b>打卡接口 v1</b><small>后端 负责 · 验收标准 2 条</small>") && html.includes("<li>有单测</li>"));
  assert(html.includes("<b>开局</b>") && html.includes("两周内跑通打卡") && html.includes("系统管理员牵头，新建的 2 位 leader 都是成员"));
  assert(html.includes('<details class="ac-sec" open>'), "sections start open on a wide screen");
  await settle();
  assert.equal(page.calls.filter(call => call.url === "/api/op/campaign.preview").length, 1);
});

test("problems in the plan, a structure-only plan, no price table, and a failed preview are all spelled out", async () => {
  const errors = ["第 2 位 leader：名字 App 重复", "作战室：至少要两位新 leader 一起推进"];
  const bad = assistantPage([launch], {preview: preview({ok: false, errors, cost: {...preview().cost, estimate: null}}), wide: false});
  await settle();
  const html = detail(bad.cards()[0]).innerHTML;
  assert(html.indexOf("ac-problems") < html.indexOf("ac-cost"));
  assert(html.includes("方案还有 2 处问题") && html.includes("<li>第 2 位 leader：名字 App 重复</li>") && html.includes("仍可点“确认执行”，后端会再校验"));
  assert(html.includes("没有价格表，无法估算金额"));
  assert(html.includes('<details class="ac-sec">') && !html.includes('<details class="ac-sec" open>'), "phones start folded");
  assert(!bad.cards()[0].children.find(child => child.className === "assistant-confirmation-actions").children[0].disabled);

  const quiet = {...preview().cost, start: false, note: "确认后只建架构图，不启动模型，leader 与首批任务留到之后再开。"};
  const structure = assistantPage([launch], {preview: preview({start: false, cost: quiet})});
  await settle();
  const only = detail(structure.cards()[0]).innerHTML;
  assert(only.includes("只建架构图，不启动模型") && only.includes("这次不开作战室") && !only.includes("估算金额") && !only.includes(">作战室<span"));

  const failed = assistantPage([launch], {failPreview: true});
  await settle();
  const raw = detail(failed.cards()[0]).innerHTML;
  assert(raw.includes("预览没读到，下面按 AI 提交的原始方案显示") && raw.includes("没能读取开局预览：工作区不存在"));
  assert(raw.includes("费用预览没读到") && raw.includes("<b>打卡接口 v1</b>"));
});

test("a budget raise shows the room's title, spend and old cap next to the new cap; other requests keep the argument line", async () => {
  const raise = {id: "b1", op: "war_room.budget", role: "assistant", project: "fitness", summary: "设置或追加作战室预算", args: {project: "fitness", room: 3, budget_tokens: 400000}};
  const other = {...plainRequest("o1"), args: {name: "后端", legacy: "旧值", request_id: "x"}};
  const page = assistantPage([raise, other], {room: {id: 3, title: "签名 v2", status: "open", budget: {tokens: 200000, spent: 203500, paused_at: "2026-11-27T10:00:00"}}});
  await settle();
  const get = page.calls.find(call => call.url.startsWith("/api/op/war_room.get?"));
  assert.equal(get.url, "/api/op/war_room.get?id=3&project=fitness&limit=1");
  const [card, plain] = page.cards(), html = detail(card).innerHTML;
  assert.match(text(card), /^追加作战室预算\n由系统管理员发起 · 工作区 fitness/);
  assert(html.includes("#3 《签名 v2》") && html.includes("20.4 万 / 20 万 tokens") && html.includes("已暂停") && html.includes("<b>40 万 tokens</b>"));
  assert.equal(plain.children[2].textContent, "会话名称：后端，legacy：旧值");
  assert.equal(plain.children[2].className, "assistant-confirmation-args");
  const [confirm] = card.children.find(child => child.className === "assistant-confirmation-actions").children;
  await confirm.onclick(); await settle();
  assert.deepEqual(page.calls.find(call => call.url === "/api/op/interaction.resolve").body, {id: "b1", approve: true});
});

test("a leader merge is shown as the board's before / after, its effects and wakes, read again from the preview", async () => {
  const before = {direction: "TB", parts: [{id: "be0001", parent_id: null, name: "后端接口"}, {id: "fe0001", parent_id: null, name: "前端页面"}],
    leaders: [{session_id: "be", domain_id: "be0001", lead: true, name: "后端"}, {session_id: "fe", domain_id: "fe0001", lead: true, name: "前端"}]};
  const after = {...before, leaders: [before.leaders[0]]};
  const restructure = {ok: true, errors: [], before, after,
    diff: {parts: {added: [], removed: [], renamed: [], moved: []}, leaders: {placed: [], moved: [], unplaced: [{session_id: "fe"}], lead_on: [], lead_off: [{session_id: "fe"}]}},
    leaders: [{name: "前端", effects: ["并入「后端」"]}, {name: "<后端>", effects: ["接手「前端」的交接稿"]}],
    goals: [{action: "transfer", title: "静态页", leader: {name: "前端"}, to: {name: "后端"}, wakes: false}],
    archived: [{name: "前端", role: "leader"}]};
  const merge = {id: "m1", op: "leader.merge", role: "assistant", project: "shop", summary: "合并",
    args: {project: "shop", leader: "fe", into: "be", board_version: 1, revision: "r", handoff: {text: "交接", wake: false}}};
  const page = assistantPage([merge], {restructure});
  await settle();
  const [card] = page.cards();
  const asked = page.calls.find(call => call.url === "/api/op/domains.restructure.preview");
  assert.deepEqual(JSON.parse(JSON.stringify(asked.body)), {project: "shop", changes: [{type: "leader.merge", leader: "fe", into: "be"}]});
  const html = detail(card).innerHTML;
  assert(html.indexOf("会动什么") < html.indexOf("改前 / 改后"));
  assert(html.includes("归档 前端（原会话保留）"));
  assert(html.includes("执行不会启动任何模型"));
  assert(html.includes("&lt;后端&gt;"));
  assert(html.includes("改派") && html.includes("前端 负责 → 后端"));
  const stale = assistantPage([{...merge, id: "m2"}], {restructure: {ok: false, errors: [{message: "本轮已提交交付、等待收尾"}]}});
  await settle();
  assert(detail(stale.cards()[0]).innerHTML.includes("方案现在执行不了（1 处问题）"));
});

test("each leader's own files from the plan are listed on the opening card, escaped, and absent when the plan has no split", async () => {
  const split = [{...preview().leaders[0], owns: ["server/", "data/<users>.json"]}, {...preview().leaders[1], owns: ["app/"]}];
  const page = assistantPage([launch], {preview: preview({leaders: split})});
  await settle();
  const html = detail(page.cards()[0]).innerHTML;
  assert(html.includes('<p class="ac-owns"><span>只改</span><code>server/</code><code>data/&lt;users&gt;.json</code></p>'));
  assert(html.includes('<p class="ac-owns"><span>只改</span><code>app/</code></p>'));
  assert(html.indexOf('<code>server/</code>') < html.indexOf("负责打卡接口与数据表"), "the split sits under the leader's name, before the brief");
  const plain = assistantPage([launch], {preview: preview({leaders: [{...preview().leaders[0], owns: null}, {...preview().leaders[1], owns: null}]})});
  await settle();
  assert(!detail(plain.cards()[0]).innerHTML.includes("ac-owns"));
});

test("a card a workspace assistant asked for names it 助手, never the internal role name", async () => {
  const page = assistantPage([{...launch, role: "workspace_assistant"}], {preview: preview()});
  await settle();
  const [card] = page.cards();
  assert.match(text(card), /^开局方案\n由助手发起 · 工作区 fitness\n/);
  assert(detail(card).innerHTML.includes("助手牵头，新建的 2 位 leader 都是成员"));
  assert(!text(card).includes("workspace_assistant"));
});

test("only an Android workspace assistant gets a voice button beside send", async () => {
  const workspace = "?project=%E7%94%B0%E9%87%8E";
  const page = assistantPage([], {android: true, search: workspace});
  await settle();
  assert.equal(page.voiceButtons.length, 1);
  await page.voiceButtons[0].onclick();
  assert.deepEqual(page.nativeCalls[0], {id: "1", method: "openVoice", params: {project: "田野"}});
  await page.refresh();
  assert.equal(page.voiceButtons.length, 1);
  for (const options of [{android: true}, {search: workspace},
    {android: true, desktop: true, search: workspace}]) {
    const hidden = assistantPage([], options);
    await settle();
    assert.equal(hidden.voiceButtons.length, 0);
  }
});
