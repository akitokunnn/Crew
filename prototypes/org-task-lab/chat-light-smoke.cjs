"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { chromium } = require("playwright");

(async () => {
  const output = path.resolve(__dirname, "../../Temp/org-qa/chat-light-browser");
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const errors = [];
  const passed = [];
  try {
    for (const [width, height] of [[1440, 900], [1024, 640], [390, 760]]) {
      const narrow = width <= 760;
      const context = await browser.newContext({ viewport: { width, height }, colorScheme: "light" });
      const page = await context.newPage();
      page.on("pageerror", error => errors.push(`${width}: ${error.message}`));
      page.on("console", message => { if (message.type() === "error") errors.push(`${width}: ${message.text()}`); });
      await page.goto(pathToFileURL(path.join(__dirname, "chat-light.html")).href);
      const shot = name => page.screenshot({ path: path.join(output, `${width}-${name}.png`) });
      const head = () => page.locator(".cl-head h1").innerText();
      const draft = page.locator("textarea");
      const tree = page.locator(".cl-panel");
      const openPanel = async () => { if (narrow && !await page.locator(".cl-sheet-right").evaluate(node => node.open)) await page.locator(".cl-panel-btn").click(); };
      const openNav = async () => { if (narrow) await page.locator(".cl-nav-btn").click(); };

      // 首排就是归属，不再有“当前对话”下拉。
      assert.match(await head(), /林序\s*\/\s*组织架构与任务归属/);
      assert.equal(await page.getByRole("combobox", { name: "当前对话" }).count(), 0);
      assert.equal(await page.locator(".cl-compose label").count(), 0, "输入框不再重复显示归属标签");
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "页面不应横向滚动");
      if (!narrow) {
        const panelWidth = await tree.evaluate(node => node.getBoundingClientRect().width);
        assert.ok(panelWidth >= 296 && panelWidth <= 324, `右栏宽度 ${panelWidth}`);
        const taskRows = await page.locator(".tr-task").count();
        assert.equal(taskRows, 4, "四个当前任务都应在树里");
      }
      await shot("initial");

      // 草稿与阅读位置按 (人员, 任务) 分开保存。
      await draft.fill("leader 在 T-024 的草稿");
      const log = page.locator(".cl-log");
      const scrollable = await log.evaluate(node => node.scrollHeight > node.clientHeight + 20);
      if (scrollable) await log.evaluate(node => { node.scrollTop = 24; });
      await openPanel();
      await page.locator('.tr-member[data-person="zhi"] .tr-act').click();
      assert.match(await head(), /知衡\s*\/\s*组织架构与任务归属/);
      assert.equal(await draft.inputValue(), "");
      await draft.fill("知衡在 T-024 的草稿");
      await openNav();
      await page.locator('.nv-item[data-person="lin"][data-task="org"]').click();
      assert.equal(await draft.inputValue(), "leader 在 T-024 的草稿");
      if (scrollable) assert.equal(await log.evaluate(node => node.scrollTop), 24, "回到原对话时恢复阅读位置");
      await openNav();
      assert.equal(await page.locator('.nv-sub[data-person="zhi"][data-task="org"] .nv-draft').count(), 1, "导航里的成员对话带草稿标记");
      if (narrow) await page.keyboard.press("Escape");
      passed.push(`${width} 草稿${scrollable ? "与阅读位置" : "（对话不足一屏，未测滚动）"}`);

      // 选中成员：行内展开跨任务入口，其他任务里的同一人同时高亮。
      await openPanel();
      await page.locator('.tr-member[data-person="zhi"] .tr-main').click();
      const detail = page.locator(".tr-member .dt");
      assert.equal(await detail.locator(".dt-go").count(), 2, "组员只有两项任务对话，没有总会话");
      assert.equal(await detail.locator('.dt-go[data-task=""]').count(), 0);
      assert.equal(await page.locator('.tr-member[data-person="lin"]').count(), 0, "leader 不作为组员出现在任务下");
      assert.equal(await page.locator(".tr-standby-item button").count(), 0, "待命组员没有对话入口");
      assert.equal(await page.locator('.tr-nm.is-focus[data-person="zhi"]').count(), 1, "收起的 T-025 中标出知衡");
      assert.ok(await page.locator(".tr-task.hl").count() >= 2, "知衡所在的两项任务连线着色");
      await shot("member-detail");
      await detail.locator('.dt-go[data-task="chat"]').click();
      assert.match(await head(), /知衡\s*\/\s*对话与任务上下文/);
      assert.match(await page.locator(".cl-head").innerText(), /排队中/);
      await openPanel();
      await page.locator(".ph-general").click();
      assert.match(await head(), /林序\s*\/\s*总会话/);
      passed.push(`${width} 跨任务分工与入口`);

      // 暂停任务只读。
      await openPanel();
      await page.locator('.tr-task-row [data-act="go"][data-task="move"]').click();
      assert.ok(await draft.isDisabled(), "暂停任务的输入框只读");
      assert.match(await draft.getAttribute("placeholder"), /已暂停/);
      await shot("paused");
      passed.push(`${width} 暂停只读`);

      // 完整图谱：成员只出现一次，聚焦后显示该任务分工，能直接进入对话。
      await openPanel();
      await page.locator('[data-act="open-map"]').click();
      const map = page.locator(".cl-map");
      assert.ok(await map.evaluate(node => node.open));
      assert.equal(await map.locator('.mp-member[data-id="zhi"]').count(), 1);
      await map.locator('.mp-task[data-id="org"]').click();
      assert.equal(await map.locator('.mp-member[data-id="zhi"] .mp-sub').innerText(), "组织视图与成员交互");
      assert.equal(await map.locator(".mp-edge.is-on").count(), 4, "leader 一条加三位成员");
      await shot("map-task");
      await map.locator('.mp-member[data-id="ting"]').click();
      await map.locator('.cl-map-detail .dt-go[data-task="org"]').click();
      assert.ok(!await map.evaluate(node => node.open));
      assert.match(await head(), /听澜\s*\/\s*组织架构与任务归属/);
      passed.push(`${width} 完整图谱`);

      // 空组。
      await openNav();
      await page.locator('.nv-leader select').selectOption("shou");
      assert.match(await head(), /守望\s*\/\s*总会话/);
      if (narrow) await page.keyboard.press("Escape");
      await openPanel();
      assert.match(await tree.innerText(), /还没有任务/);
      passed.push(`${width} 空组`);
      await context.close();
    }

    const dark = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", reducedMotion: "reduce" });
    const page = await dark.newPage();
    page.on("pageerror", error => errors.push(`dark: ${error.message}`));
    await page.goto(pathToFileURL(path.join(__dirname, "chat-light.html")).href);
    await page.locator('.tr-member[data-person="ting"] .tr-main').click();
    await page.screenshot({ path: path.join(output, "1440-dark.png") });
    await dark.close();
  } finally {
    await browser.close();
  }
  assert.deepEqual(errors, []);
  console.log(`${passed.length} 项通过，控制台错误 0`);
  passed.forEach(item => console.log(`- ${item}`));
})().catch(error => { console.error(error); process.exit(1); });
