"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { chromium } = require("playwright");

(async () => {
  const output = path.resolve(__dirname, "../../Temp/org-qa/browser");
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const errors = [];
  const passed = [];
  try {
    for (const [width, height] of [[1440, 900], [1024, 640], [390, 760]]) {
      const narrow = width <= 760;
      const compact = width <= 1180;
      const context = await browser.newContext({ viewport: { width, height }, colorScheme: "light" });
      const page = await context.newPage();
      page.on("pageerror", error => errors.push(`${width}: ${error.message}`));
      page.on("console", message => { if (message.type() === "error") errors.push(`${width}: ${message.text()}`); });
      await page.goto(pathToFileURL(path.join(__dirname, "index.html")).href);
      const shot = name => page.screenshot({ path: path.join(output, `${width}-${name}.png`) });
      const head = () => page.locator(".cl-head h1").innerText();
      const draft = page.locator("textarea");
      const isOpen = selector => page.locator(selector).evaluate(node => node.open);
      const noScroll = async label => assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${label} 不应横向滚动`);
      const openNav = async () => { if (narrow && !await isOpen(".cl-sheet-left")) await page.locator(".cl-nav-btn:visible").first().click(); };
      const openPanel = async () => {
        if (compact) { if (!await isOpen(".cl-sheet-right")) await page.locator(".cl-panel-btn").click(); }
        else if (!await page.locator(".cl-panel").isVisible()) await page.locator(".cl-panel-btn").click();
      };
      const rail = async leader => { await openNav(); await page.locator(`.rl-leader[data-leader="${leader}"]`).click(); };
      const side = async (task, person) => { await openNav(); await page.locator(`.nv-item[data-person="${person}"][data-task="${task}"]`).click(); };

      // 打开项目先到工作台，待你按 leader 汇总。
      assert.ok(await page.locator(".cl-page .wb-section").first().isVisible());
      assert.ok(Number(await page.locator('[data-act="workbench"] .rl-badge').innerText()) >= 10);
      assert.equal(await page.getByRole("combobox", { name: "当前对话" }).count(), 0);
      await noScroll("工作台");
      await shot("workbench");
      await page.locator('.wb-row[data-task="wendi-1"]').click();
      assert.match(await head(), /闻笛\s*\/\s*Windows 安装包签名/);
      passed.push(`${width} 工作台直达别组的待你任务`);

      // 跨 leader 切换：各对话草稿独立，回到 leader 时回到他上次的对话。
      await draft.fill("闻笛任务里的草稿");
      await rail("lin");
      assert.match(await head(), /林序\s*\/\s*组织架构与任务归属/);
      await draft.fill("林序 T-024 的草稿");
      await side("", "lin");
      assert.match(await head(), /林序\s*\/\s*总会话/);
      assert.equal(await draft.inputValue(), "");
      await rail("wendi");
      assert.equal(await draft.inputValue(), "闻笛任务里的草稿");
      await rail("lin");
      assert.match(await head(), /林序\s*\/\s*总会话/, "回到 leader 上次打开的对话");
      await side("org", "lin");
      assert.equal(await draft.inputValue(), "林序 T-024 的草稿");
      await openNav();
      assert.equal(await page.locator('.nv-item[data-task="org"] .nv-draft').count(), 1);
      if (narrow) await page.keyboard.press("Escape");
      await noScroll("对话");
      await shot("task");
      passed.push(`${width} 跨 leader 草稿与返回`);

      // 右栏随对话变化：任务里看本任务分工，组员没有总会话。
      await openPanel();
      assert.match(await page.locator(".cl-panel-head").innerText(), /本任务分工/);
      assert.equal(await page.locator('.cl-panel .tr-member[data-person="lin"]').count(), 0);
      await page.locator('.cl-panel .tr-member[data-person="zhi"] .tr-main').click();
      assert.equal(await page.locator('.cl-panel .dt .dt-go[data-task=""]').count(), 0, "组员没有总会话");
      await page.locator('.cl-panel .dt .dt-go[data-task="chat"]').click();
      assert.match(await head(), /知衡\s*\/\s*对话与任务上下文/);
      await page.locator(".hd-crumb.hd-context").click();
      assert.match(await head(), /林序\s*\/\s*对话与任务上下文/, "组员对话里点任务名回到任务主线");
      await page.locator(".hd-crumb.hd-person").click();
      assert.match(await head(), /林序\s*\/\s*总会话/, "点 leader 名回到总会话");
      await openPanel();
      assert.match(await page.locator(".cl-panel-head").innerText(), /林序组/);
      await page.locator('.cl-panel [data-act="roster"][data-person="zhi"]').click();
      assert.equal(await page.locator('.cl-panel .tr-roster [data-act="go"][data-person="zhi"]').count(), 2);
      await shot("general-roster");
      if (compact) await page.keyboard.press("Escape");
      passed.push(`${width} 右栏随对话切换与标题返回`);

      // 未开始的任务：打开不会启动，明确操作才开始；暂停只读。
      await openNav();
      await page.locator('.nv-all').click();
      await page.locator('.ls-search input').fill("设置页");
      await page.locator('.ls-row[data-task="lin-16"]').click();
      assert.match(await page.locator(".cl-head").textContent(), /未开始/);
      assert.ok(await page.locator(".cl-start").isVisible());
      await shot("queued");
      await page.locator('.cl-start button').click();
      assert.match(await page.locator(".cl-head").textContent(), /推进中/);
      await openNav();
      await page.locator('.nv-all').click();
      assert.equal(await page.locator('.ls-search input').inputValue(), "设置页", "清单保留上次的搜索词");
      await page.locator('.ls-search input').fill("");
      await page.locator('.ls-filters [data-filter="paused"]').click();
      await page.locator('.ls-row[data-task="move"]').click();
      assert.match(await head(), /普通文件夹工作区迁移/);
      assert.ok(await draft.isDisabled(), "暂停的任务只读");
      await openNav();
      assert.equal(await page.locator('.nv-item[data-task="move"]').count(), 1, "打开过的任务进入侧栏「在做」");
      if (narrow) await page.keyboard.press("Escape");
      passed.push(`${width} 未开始与暂停`);

      // 完整清单：搜索词保留，Esc 回到打开前的对话。
      await openNav();
      await page.locator('.nv-all').click();
      await page.locator('.ls-filters [data-filter="all"]').click();
      await page.locator('.ls-search input').fill("音效");
      assert.equal(await page.locator(".ls-row").count(), 1);
      await page.locator('.ls-search input').blur();
      await page.keyboard.press("Escape");
      assert.match(await head(), /普通文件夹工作区迁移/);
      await openNav();
      await page.locator('.nv-all').click();
      assert.equal(await page.locator('.ls-search input').inputValue(), "音效");
      await noScroll("清单");
      await shot("list");
      await page.keyboard.press("Escape");
      passed.push(`${width} 完整清单`);

      // 快速切换：Ctrl K 跨 leader 找任务，Esc 关闭后焦点回到输入框。
      await draft.click().catch(() => {});
      await page.keyboard.press("Control+k");
      assert.ok(await isOpen(".cl-palette"));
      await page.keyboard.type("断点续传");
      await shot("palette");
      await page.keyboard.press("Enter");
      assert.match(await head(), /闻笛\s*\/\s*自动更新的断点续传/);
      passed.push(`${width} 快速切换`);

      // 完整图谱：组员只出现一次。
      await rail("lin");
      await side("org", "lin");
      await openPanel();
      await page.locator('[data-act="open-map"]').click();
      assert.equal(await page.locator('.cl-map .mp-member[data-id="zhi"]').count(), 1);
      await page.locator('.cl-map .mp-task[data-id="org"]').click();
      assert.equal(await page.locator(".cl-map .mp-edge.is-on").count(), 4);
      await page.locator('[data-act="close-map"]').click();
      passed.push(`${width} 完整图谱`);

      // 长名称与空组。
      await rail("carrier");
      assert.match(await head(), /多载体适配/);
      await noScroll("长 leader 名");
      await shot("long-name");
      await rail("shou");
      await openNav();
      assert.match(await page.locator(".cl-side").innerText(), /还没有任务/);
      if (narrow) await page.keyboard.press("Escape");
      passed.push(`${width} 长名称与空组`);
      await context.close();
    }

    const dark = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", reducedMotion: "reduce" });
    const page = await dark.newPage();
    page.on("pageerror", error => errors.push(`dark: ${error.message}`));
    await page.goto(pathToFileURL(path.join(__dirname, "index.html")).href);
    await page.locator('.nv-item[data-task="org"]').click();
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
