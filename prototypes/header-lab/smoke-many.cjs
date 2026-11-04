/* Real Chrome against the offline 36-task sidebar prototype. */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {chromium} = require("playwright");

const entry = path.join(__dirname, "demo.html");
const output = path.resolve(__dirname, "../../Temp/sidebar-many-review");
const expectedCounts = {queued: 10, running: 8, waiting: 6, delivered: 12};
const viewports = [
  {width: 1440, height: 900},
  {width: 1024, height: 600},
  {width: 390, height: 844}
];
const evidence = {
  entry: "file://demo.html?view=sidebar&tasks=many",
  checks: [],
  failures: [],
  screenshots: [],
  consoleErrors: [],
  unexpectedRequests: [],
  taskSamples: []
};
let browser;

async function scenario(viewport) {
  const {width, height} = viewport;
  const prefix = `sidebar-many-${width}x${height}`;
  const mobile = width <= 760;
  const context = await browser.newContext({viewport, reducedMotion: "reduce", colorScheme: "light"});
  const page = await context.newPage();
  const root = page.locator("#demo-root");
  const navigation = root.locator(".sidebar-pane");
  const list = root.locator("[data-sidebar-scroll]");
  const rows = navigation.locator(".sidebar-task:visible");
  let sequence = 0;
  page.setDefaultTimeout(12000);

  page.on("pageerror", error => evidence.consoleErrors.push({viewport, message: error.stack || String(error)}));
  page.on("console", message => {
    if (message.type() === "error") evidence.consoleErrors.push({viewport, message: message.text()});
  });
  page.on("request", request => {
    const protocol = new URL(request.url()).protocol;
    const type = request.resourceType();
    if (["fetch", "xhr", "websocket", "eventsource"].includes(type) || !["file:", "data:", "blob:"].includes(protocol)) {
      evidence.unexpectedRequests.push({viewport, type, method: request.method(), url: request.url()});
    }
  });

  function check(label, condition, detail) {
    const pass = Boolean(condition);
    evidence.checks.push({viewport, label, pass, detail});
    assert(pass, `${prefix}: ${label}${detail === undefined ? "" : ` (${JSON.stringify(detail)})`}`);
  }

  async function screenshot(label) {
    const name = `${prefix}-${String(++sequence).padStart(2, "0")}-${label}.png`;
    await page.screenshot({path: path.join(output, name), fullPage: true, animations: "disabled"});
    evidence.screenshots.push(name);
  }

  async function openNavigation() {
    if (mobile && !(await navigation.isVisible())) await root.locator(".sidebar-menu").click();
    await navigation.waitFor({state: "visible"});
  }

  async function closeNavigation() {
    if (mobile && await navigation.isVisible()) {
      await page.keyboard.press("Escape");
      await navigation.waitFor({state: "hidden"});
      check("Escape restores the drawer opener's focus", await root.locator(".sidebar-menu").evaluate(node => document.activeElement === node));
    }
  }

  async function setGroup(status, expanded) {
    const button = navigation.locator(`[data-action="sidebar-toggle-group"][data-status="${status}"]`);
    if ((await button.getAttribute("aria-expanded") === "true") !== expanded) await button.click();
    check(`${status} group exposes its expanded state`, await button.getAttribute("aria-expanded") === String(expanded));
  }

  async function countGroup(status, expected) {
    const button = navigation.locator(`[data-action="sidebar-toggle-group"][data-status="${status}"]`);
    const text = (await button.innerText()).trim();
    check(`${status} group shows ${expected} tasks`, new RegExp(`(?:^|\\D)${expected}(?:\\D|$)`).test(text), text);
  }

  async function layout(label) {
    const dimensions = await page.evaluate(() => ({page: document.documentElement.scrollWidth, viewport: innerWidth}));
    check(`${label} has no page-level horizontal overflow`, dimensions.page <= dimensions.viewport + 1, dimensions);
    const box = await root.locator("[data-main-header]").boundingBox();
    check(`${label} keeps one compact main header`, box && box.height <= (mobile ? 84 : 64) + 1, box?.height);
  }

  async function withinViewport(locator) {
    const box = await locator.boundingBox();
    return Boolean(box && box.width > 0 && box.height > 0 && box.x >= -1 && box.y >= -1 && box.x + box.width <= width + 1 && box.y + box.height <= height + 1);
  }

  async function scrollListToBottom() {
    await list.hover();
    await page.mouse.wheel(0, 5000);
    await page.waitForFunction(() => {
      const node = document.querySelector("[data-sidebar-scroll]");
      return node && node.scrollHeight - node.clientHeight - node.scrollTop <= 2;
    });
    return list.evaluate(node => node.scrollTop);
  }

  async function checkListPosition(label, savedScroll) {
    const position = await list.evaluate(node => ({scroll: node.scrollTop, maximum: node.scrollHeight - node.clientHeight}));
    const expected = Math.min(savedScroll, position.maximum);
    check(label, Math.abs(position.scroll - expected) <= 2, {savedScroll, ...position});
  }

  async function closeDialog() {
    await page.locator('#overlay-root [role="dialog"]').waitFor({state: "visible"});
    await page.keyboard.press("Escape");
    await page.locator('#overlay-root [role="dialog"]').waitFor({state: "hidden"});
  }

  async function search(value) {
    await openNavigation();
    await navigation.locator("#sidebar-task-search").fill(value);
  }

  async function selectTask(id) {
    await openNavigation();
    await navigation.locator(`.sidebar-task[data-scope="${id}"]`).click();
    await root.locator("#message-input").waitFor({state: "visible"});
  }

  async function allGroups() {
    await openNavigation();
    await navigation.locator("#sidebar-task-search").fill("");
    await navigation.locator("#sidebar-status-filter").selectOption("all");
    for (const status of Object.keys(expectedCounts)) await setGroup(status, true);
  }

  try {
    const url = pathToFileURL(entry);
    url.searchParams.set("view", "sidebar");
    url.searchParams.set("tasks", "many");
    await page.goto(url.href);
    await root.locator("[data-main-header]").waitFor({state: "visible"});
    await page.evaluate(() => document.fonts.ready);
    check("many-task tool selects the 36-task fixture", await page.locator("#lab-task-count").inputValue() === "many");
    check("initial conversation is the running latency task", await page.locator("#lab-status").inputValue() === "running" && (await root.locator(".sidebar-title-wrap h1").innerText()).includes("延迟"));
    await layout("initial");
    await screenshot("initial");

    const longMessage = Array.from({length: 45}, (_, index) => `滚动验收第 ${index + 1} 行。任务列表与当前对话分别保留阅读位置。`).join("\n");
    await root.locator("#message-input").fill(longMessage);
    await root.locator('[data-action="send"]').click();
    await root.locator(".conversation-scroll").evaluate(node => {node.scrollTop = 120;});
    check("the conversation has a real scroll range", await root.locator(".conversation-scroll").evaluate(node => node.scrollTop > 0 && node.scrollHeight > node.clientHeight));
    await openNavigation();
    for (const [status, count] of Object.entries(expectedCounts)) await countGroup(status, count);
    check("delivered tasks begin collapsed", await navigation.locator('[data-action="sidebar-toggle-group"][data-status="delivered"]').getAttribute("aria-expanded") === "false");
    check("the initial list exposes 24 active tasks", await rows.count() === 24, await rows.count());
    check("general conversation is fixed and reachable", await withinViewport(navigation.locator('[data-action="scope"][data-scope="general"]')));
    check("global assistant is fixed and reachable", await withinViewport(navigation.locator('[data-action="assistant"]')));

    const beforeScroll = await page.evaluate(() => {
      const bounds = selector => {
        const rect = document.querySelector(selector).getBoundingClientRect();
        return {top: rect.top, left: rect.left};
      };
      return {
        header: bounds("#demo-root [data-main-header]"),
        general: bounds('.sidebar-pane [data-action="scope"][data-scope="general"]'),
        assistant: bounds('.sidebar-pane [data-action="assistant"]'),
        mainScroll: document.querySelector(".conversation-scroll").scrollTop,
        paneScroll: document.querySelector(".sidebar-pane").scrollTop,
        pageScroll: scrollY
      };
    });
    check("task results have an independent overflow range", await list.evaluate(node => node.scrollHeight > node.clientHeight + 100));
    const firstBottom = await scrollListToBottom();
    check("wheel scroll moves the task results", firstBottom > 100, firstBottom);
    const afterScroll = await page.evaluate(() => {
      const bounds = selector => {
        const rect = document.querySelector(selector).getBoundingClientRect();
        return {top: rect.top, left: rect.left};
      };
      return {
        header: bounds("#demo-root [data-main-header]"),
        general: bounds('.sidebar-pane [data-action="scope"][data-scope="general"]'),
        assistant: bounds('.sidebar-pane [data-action="assistant"]'),
        mainScroll: document.querySelector(".conversation-scroll").scrollTop,
        paneScroll: document.querySelector(".sidebar-pane").scrollTop,
        pageScroll: scrollY
      };
    });
    check("scrolling task results leaves chat, header, general conversation and assistant in place", JSON.stringify(beforeScroll) === JSON.stringify(afterScroll), {beforeScroll, afterScroll});
    await screenshot("independent-list-scroll");

    await setGroup("delivered", true);
    check("expanding delivered makes all 36 tasks available", await rows.count() === 36, await rows.count());
    const manifest = await rows.evaluateAll(nodes => nodes.map(node => ({
      id: node.dataset.scope,
      title: node.getAttribute("title") || "",
      shortTitle: node.querySelector(".sidebar-task-copy > span")?.textContent || "",
      status: node.dataset.status
    })));
    check("36 task rows have distinct conversation identities", new Set(manifest.map(item => item.id)).size === 36);
    for (const [status, count] of Object.entries(expectedCounts)) check(`all ${count} ${status} tasks expose their real status`, manifest.filter(item => item.status === status).length === count);
    const rowHeights = await rows.evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().height));
    check("many-task rows keep a compact 38–42px height", rowHeights.every(value => value >= 38 && value <= 42), {minimum: Math.min(...rowHeights), maximum: Math.max(...rowHeights)});
    const lastTask = manifest.at(-1);
    const longTask = manifest.filter(item => item.status === "delivered").sort((left, right) => right.title.length - left.title.length)[0];
    check("a delivered task has a complete name beyond its short label", longTask.title.length >= 40 && longTask.title.length > longTask.shortTitle.length + 8, longTask);
    const longQuery = longTask.title.slice(-12);
    check("the long-title search term is absent from the short label", !longTask.shortTitle.includes(longQuery), longQuery);
    evidence.taskSamples.push({viewport, lastTask, longTask, longQuery});

    for (const [status, count] of Object.entries(expectedCounts)) {
      await navigation.locator("#sidebar-status-filter").selectOption(status);
      await setGroup(status, true);
      const filteredStatuses = await rows.evaluateAll(nodes => nodes.map(node => node.dataset.status));
      check(`${status} filter returns exactly ${count} matching tasks`, filteredStatuses.length === count && filteredStatuses.every(value => value === status), filteredStatuses);
      await countGroup(status, count);
    }
    await navigation.locator("#sidebar-status-filter").selectOption("all");
    await setGroup("delivered", false);
    await search("");
    await navigation.locator("#sidebar-task-search").focus();
    await page.keyboard.type(longQuery);
    check("typing a complete search keeps focus and every entered character", await navigation.locator("#sidebar-task-search").inputValue() === longQuery && await navigation.locator("#sidebar-task-search").evaluate(node => document.activeElement === node));
    check("search finds a full-name match in the collapsed delivered group", await navigation.locator(`.sidebar-task[data-scope="${longTask.id}"]`).isVisible());
    check("a matching search reveals the delivered group", await navigation.locator('[data-action="sidebar-toggle-group"][data-status="delivered"]').getAttribute("aria-expanded") === "true");
    const searchResults = await rows.evaluateAll(nodes => nodes.map(node => node.getAttribute("title") || ""));
    check("search results match the complete title", searchResults.length > 0 && searchResults.every(value => value.includes(longQuery)), searchResults);
    await screenshot("long-title-search");
    await search("__crew_missing_task_71f91__");
    check("an unmatched search has no task rows", await rows.count() === 0);
    const clear = navigation.locator('[data-action="sidebar-clear"]:visible').first();
    check("empty results offer a visible recovery", await clear.isVisible());
    await clear.click();
    check("recovery clears search and status filtering", await navigation.locator("#sidebar-task-search").inputValue() === "" && await navigation.locator("#sidebar-status-filter").inputValue() === "all");
    check("recovery restores task results", await rows.count() >= 24);

    await navigation.locator("#sidebar-status-filter").selectOption("waiting");
    const locate = navigation.locator('[data-action="sidebar-locate"]:visible').first();
    check("a filtered-out current task offers a locate action", await locate.isVisible());
    await locate.click();
    check("locating the current task restores all statuses", await navigation.locator("#sidebar-status-filter").inputValue() === "all");
    check("locating reveals the running current conversation", await navigation.locator('.sidebar-task[data-scope="latency"][aria-current="page"]').isVisible());
    await setGroup("running", false);
    check("a collapsed current group offers a locate action", await locate.isVisible());
    await locate.click();
    check("locating expands the current group", await navigation.locator('[data-action="sidebar-toggle-group"][data-status="running"]').getAttribute("aria-expanded") === "true");
    check("locating scrolls the current task inside the task viewport", await navigation.locator('.sidebar-task[data-scope="latency"]').evaluate(node => {
      const row = node.getBoundingClientRect();
      const viewport = document.querySelector("[data-sidebar-scroll]").getBoundingClientRect();
      return row.top >= viewport.top - 1 && row.bottom <= viewport.bottom + 1;
    }));
    await screenshot("current-task-located");

    await allGroups();
    const endScroll = await scrollListToBottom();
    check("the final task is visible at the end of the list", await withinViewport(navigation.locator(`.sidebar-task[data-scope="${lastTask.id}"]`)));
    await selectTask(lastTask.id);
    check("switching to the final task preserves its existing status", await page.locator("#lab-status").inputValue() === lastTask.status);
    check("the final task has its own full conversation title", await root.locator(".sidebar-title-wrap h1").getAttribute("title") === lastTask.title);
    const draft = `${prefix} 末尾任务独立草稿，先保存输入。`;
    await root.locator("#message-input").fill(draft);
    await root.locator('.sidebar-header [data-action="details"]').click();
    await page.locator('[data-panel="details"]').waitFor({state: "visible"});
    check("the final task's details show the same complete title", await page.locator("[data-detail-title]").innerText() === lastTask.title);
    await closeDialog();
    await openNavigation();
    await checkListPosition("opening task details preserves the list's reading position", endScroll);
    await navigation.locator('[data-action="scope"][data-scope="general"]').click();
    await root.locator("#message-input").fill(`${prefix} 总会话草稿`);
    await openNavigation();
    await checkListPosition("visiting general conversation preserves the task list's reading position", endScroll);
    await selectTask(lastTask.id);
    check("returning to the final task restores its independent draft", await root.locator("#message-input").inputValue() === draft);
    check("returning without sending leaves the task's status intact", await page.locator("#lab-status").inputValue() === lastTask.status);
    await screenshot("final-task-draft-restored");

    const queuedTask = manifest.find(item => item.status === "queued");
    await search(queuedTask.title);
    await selectTask(queuedTask.id);
    check("entering a queued task does not start it", await page.locator("#lab-status").inputValue() === "queued");
    await page.locator("#lab-status").selectOption("running");
    await openNavigation();
    await navigation.locator("#sidebar-task-search").fill("");
    await navigation.locator("#sidebar-status-filter").selectOption("all");
    await setGroup("running", true);
    check("changing task status updates its current sidebar row", await navigation.locator(`.sidebar-task[data-scope="${queuedTask.id}"]`).getAttribute("data-status") === "running");
    await navigation.locator('[data-action="scope"][data-scope="general"]').click();
    await openNavigation();
    check("the changed status stays true when the task is unselected", await navigation.locator(`.sidebar-task[data-scope="${queuedTask.id}"]`).getAttribute("data-status") === "running");
    await countGroup("queued", 9);
    await countGroup("running", 9);
    await navigation.locator("#sidebar-status-filter").selectOption("running");
    check("status filtering uses the updated task state", await rows.count() === 9 && await navigation.locator(`.sidebar-task[data-scope="${queuedTask.id}"]`).isVisible());
    await navigation.locator("#sidebar-status-filter").selectOption("all");
    await screenshot("status-updated-in-general");
    await closeNavigation();

    await search(manifest.find(item => item.id === "mips").title);
    await selectTask("mips");
    await page.locator("#lab-long-title").check();
    await layout("long title");
    await root.locator('.sidebar-header [data-action="details"]').click();
    check("the long task title remains readable in details", await page.locator("[data-detail-title]").evaluate(node => node.textContent.length >= 55 && node.scrollWidth <= node.clientWidth + 1));
    await screenshot("long-title-details");
    await closeDialog();
    const lightSurface = await root.evaluate(node => getComputedStyle(node).getPropertyValue("--surface").trim());
    await page.locator("#lab-theme").click();
    const darkSurface = await root.evaluate(node => getComputedStyle(node).getPropertyValue("--surface").trim());
    check("dark mode changes the actual interface palette", lightSurface !== darkSurface && Boolean(darkSurface));
    await openNavigation();
    await navigation.locator('[data-action="sidebar-clear"]:visible').first().click();
    await layout("dark navigation");
    check("global assistant remains reachable in dark mode", await withinViewport(navigation.locator('[data-action="assistant"]')));
    await screenshot("dark-many-tasks");
    await closeNavigation();

    await Promise.all([
      page.waitForURL(url => url.searchParams.get("tasks") === "few"),
      page.locator("#lab-task-count").selectOption("few")
    ]);
    await root.locator("[data-main-header]").waitFor({state: "visible"});
    await openNavigation();
    check("the task-count tool can switch back to three tasks", await root.locator(".sidebar-total-count").innerText() === "3");
    await closeNavigation();
    await Promise.all([
      page.waitForURL(url => url.searchParams.get("tasks") === "many"),
      page.locator("#lab-task-count").selectOption("many")
    ]);
    await root.locator("[data-main-header]").waitFor({state: "visible"});
    await openNavigation();
    await allGroups();
    check("the task-count tool returns to 36 task rows", await rows.count() === 36);
    await layout("many fixture restored");
    await screenshot("many-fixture-restored");
  } catch (error) {
    evidence.failures.push({viewport, error: error.stack || String(error)});
    try { await screenshot("failure"); } catch { /* Preserve the first failure. */ }
  } finally {
    await context.close();
  }
}

(async () => {
  await fs.access(entry);
  await fs.mkdir(output, {recursive: true});
  browser = await chromium.launch({headless: true, channel: "chrome"});
  for (const viewport of viewports) await scenario(viewport);
  assert.deepEqual(evidence.consoleErrors, [], "Browser errors fail the many-task review");
  assert.deepEqual(evidence.unexpectedRequests, [], "The offline prototype must not make network or API requests");
  assert.deepEqual(evidence.failures, [], "All many-task sidebar journeys must pass");
})().catch(error => {
  evidence.error = error.stack || String(error);
  process.exitCode = 1;
}).finally(async () => {
  await browser?.close();
  await fs.mkdir(output, {recursive: true});
  await fs.writeFile(path.join(output, "evidence.json"), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({output, checks: evidence.checks.length, failures: evidence.failures.length, consoleErrors: evidence.consoleErrors.length, unexpectedRequests: evidence.unexpectedRequests.length, error: evidence.error}));
});
