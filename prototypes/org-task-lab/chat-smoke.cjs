"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { chromium } = require("playwright");

(async () => {
  const output = path.resolve(__dirname, "../../Temp/org-qa/chat-browser");
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const errors = [], checks = [];
  try {
    for (const width of [1440, 1024, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 940 }, colorScheme: "light" });
      const page = await context.newPage();
      page.on("pageerror", error => errors.push(error.message));
      page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
      await page.goto(pathToFileURL(path.join(__dirname, "index.html")).href);
      await page.getByRole("link", { name: "对话右侧架构 ↗" }).click();
      await page.waitForURL("**/chat.html");
      const shot = name => page.screenshot({ path: path.join(output, `${width}-${name}.png`), fullPage: true });
      const openPanel = async () => {
        if (!await page.locator(".cd-panel").isVisible()) await page.locator('[data-chat-action="toggle-panel"]').click();
      };
      const openLeaderTask = async taskId => {
        await openPanel();
        await page.locator('[data-chat-action="panel-scope"][data-scope="group"]').click();
        await page.locator(`[data-panel-action="chat"][data-task="${taskId}"]`).click();
      };
      const draft = page.locator("textarea");
      await openPanel();
      assert.equal(await page.getByRole("combobox", { name: "当前对话", exact: true }).count(), 0);
      assert.match(await page.locator(".cd-chat-person h1").innerText(), /林序[\s\S]*组织架构与任务归属/);
      await shot("initial");
      const panelWidth = await page.locator(".cd-panel").evaluate(node => node.getBoundingClientRect().width);
      if (width > 740) assert.ok(panelWidth >= 300 && panelWidth <= 340, "右侧面板应保持紧凑宽度");
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));

      if (width <= 740) await page.locator('[data-chat-action="close-panel"]').click();
      await draft.fill("leader 当前任务的未发送草稿");
      await openPanel();
      await page.locator('.ctp-person[data-person="zhi"][data-task="org"]').click();
      assert.match(await page.locator(".cd-chat-person h1").innerText(), /林序/);
      assert.equal(await draft.inputValue(), "leader 当前任务的未发送草稿");
      assert.ok(await page.locator('[data-panel-action="select-person"][data-person="zhi"][aria-pressed="true"]').count() >= 2);
      await shot("member-selected");

      await page.locator('.cd-related-tasks [data-task="chat"]').click();
      assert.equal(await draft.inputValue(), "leader 当前任务的未发送草稿");
      await page.locator('.cd-selection-actions [data-person="zhi"][data-task="chat"]').click();
      assert.match(await page.locator(".cd-chat-person h1").innerText(), /知衡[\s\S]*对话与任务上下文/);
      assert.equal(await draft.inputValue(), "");
      await shot("member-task-conversation");
      await draft.fill("知衡在另一个任务中的草稿");
      await openPanel();
      await page.locator('.cd-selection-actions [data-person="zhi"][data-task=""]').click();
      assert.match(await page.locator(".cd-chat-person h1").innerText(), /知衡[\s\S]*总会话/);
      assert.equal(await draft.inputValue(), "");
      await draft.fill("知衡的总会话草稿");
      await openPanel();
      await page.locator('.cd-tree-scroll [data-panel-action="select-person"][data-person="zhi"][data-task="chat"]').click();
      await page.locator('.cd-selection-actions [data-person="zhi"][data-task="chat"]').click();
      assert.match(await page.locator(".cd-chat-person h1").innerText(), /知衡[\s\S]*对话与任务上下文/);
      assert.equal(await draft.inputValue(), "知衡在另一个任务中的草稿");
      await openPanel();
      assert.equal(await page.locator(".ctp-chat-marker").count(), 1);
      await shot("scope-and-draft-restored");

      await page.locator('[data-chat-action="panel-scope"][data-scope="task"]').click();
      assert.equal(await page.locator(".ctp-branch").count(), 1);
      await shot("current-task-filter");
      await page.locator('[data-chat-action="panel-scope"][data-scope="group"]').click();
      await page.locator('[data-chat-action="expand-map"]').click();
      assert.equal(await page.locator(".cd-map-dialog").isVisible(), true);
      assert.equal(await draft.inputValue(), "知衡在另一个任务中的草稿");
      await shot("full-graph");
      const collapse = page.locator('.cd-map-dialog [data-action="collapse"][data-task="org"]');
      await collapse.click();
      assert.equal(await page.locator('.cd-map-dialog [data-action="member"][data-task="org"]').count(), 0);
      await collapse.click();
      await page.locator('[data-chat-action="close-map"]').click();
      assert.equal(await draft.inputValue(), "知衡在另一个任务中的草稿");
      await shot("returned-to-chat");

      await page.locator('[data-panel-action="chat"][data-task="move"]').click();
      assert.equal(await draft.getAttribute("readonly"), "");
      assert.equal(await page.locator(".cd-send").isDisabled(), true);
      await shot("paused-read-only");
      await openPanel();
      await page.locator('[data-panel-action="chat"][data-task="org"]').click();
      assert.match(await page.locator(".cd-chat-person h1").innerText(), /林序/);
      assert.equal(await draft.inputValue(), "leader 当前任务的未发送草稿");
      await openPanel();
      await page.locator('[data-chat-action="close-panel"]').click();
      assert.equal(await page.locator(".cd-panel").isVisible(), false);
      await shot("panel-closed");

      await draft.fill(Array.from({ length: 40 }, (_, index) => `本轮阅读记录 ${index + 1}`).join("\n"));
      await page.locator(".cd-send").click();
      await draft.fill("leader 当前任务的未发送草稿");
      const readingPosition = await page.locator(".cd-messages").evaluate(node => { node.scrollTop = 115; return node.scrollTop; });
      assert.ok(readingPosition > 0, "长记录应允许滚动阅读");
      await openLeaderTask("chat");
      await openLeaderTask("org");
      assert.match(await page.locator(".cd-chat-person h1").innerText(), /林序[\s\S]*组织架构与任务归属/);
      assert.equal(await page.locator(".cd-messages").evaluate(node => node.scrollTop), readingPosition);
      assert.equal(await draft.inputValue(), "leader 当前任务的未发送草稿");
      await shot("reading-position-restored");

      if (width === 1440) {
        await page.locator('[data-chat-control="leader"]').selectOption("jian");
        await openPanel();
        await page.locator('[data-panel-action="select-person"][data-person="xu"]').click();
        assert.match(await page.locator(".cd-selection").innerText(), /已分配 · 未启动/);
        await page.locator('.cd-selection-actions [data-task="search"]').click();
        assert.match(await page.locator(".cd-messages").innerText(), /还没有该人员的本任务执行记录/);
        await draft.fill("<img src=x onerror=alert(1)>");
        await page.locator(".cd-send").click();
        assert.equal(await page.locator(".cd-message img").count(), 0);
        assert.match(await page.locator(".cd-messages").innerText(), /<img src=x/);
        await shot("unstarted-and-local-message");
        await page.locator('[data-chat-control="leader"]').selectOption("shou");
        await openPanel();
        assert.match(await page.locator(".ctp-view").innerText(), /没有任务/);
        assert.equal(await page.locator('[data-chat-action="panel-scope"][data-scope="task"]').isDisabled(), true);
        await shot("empty-group");
      }
      checks.push(`${width}px 窄树、跨任务分工、独立对话草稿、暂停、完整图谱往返与关闭`);
      await context.close();
    }
    const dark = await browser.newPage({ viewport: { width: 1024, height: 940 }, colorScheme: "dark", reducedMotion: "reduce" });
    dark.on("pageerror", error => errors.push(error.message));
    await dark.goto(pathToFileURL(path.join(__dirname, "chat.html")).href);
    await dark.screenshot({ path: path.join(output, "dark-reduced-motion.png"), fullPage: true });
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, "evidence.json"), JSON.stringify({ ok: true, checks, errors }, null, 2));
    console.log(JSON.stringify({ ok: true, journeys: checks.length, errors: 0, screenshots: output }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
