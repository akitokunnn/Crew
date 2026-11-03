"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { chromium } = require("playwright");

async function run() {
  const output = path.resolve(__dirname, "../../Temp/org-qa/browser");
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const errors = [];
  const checks = [];
  try {
    for (const width of [1440, 768, 390]) {
      const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: "light" });
      const page = await context.newPage();
      page.on("pageerror", error => errors.push(error.message));
      page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
      await page.goto(pathToFileURL(path.join(__dirname, "index.html")).href);
      for (const variant of ["lanes", "matrix", "graph"]) {
        await page.locator(`[data-action="variant"][data-view="${variant}"]`).click();
        await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished)));
        await page.screenshot({ path: path.join(output, `${variant}-${width}.png`), fullPage: true });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${variant} @${width} 页面横向溢出`);

        const sharedMember = page.locator('.org-content [data-action="member"][data-person="zhi"][data-task="org"]').first();
        await sharedMember.click();
        const dialog = page.locator("dialog");
        await dialog.waitFor({ state: "visible" });
        assert.match(await dialog.innerText(), /关联 2 项当前任务/);
        await dialog.locator('[data-action="conversation"][data-task="chat"]').click();
        assert.match(await dialog.locator(".org-scope-label").innerText(), /T-025/);
        assert.match(await dialog.locator(".org-scope-identity").innerText(), /zhi[\s\S]*chat/);
        await dialog.locator('[data-action="member"]').click();
        await dialog.locator('[data-action="general"]').click();
        assert.match(await dialog.locator(".org-scope-identity").innerText(), /null · 总会话/);
        await dialog.locator('[data-action="close"]').click();
        await page.screenshot({ path: path.join(output, `${variant}-${width}-selected.png`), fullPage: true });

        const history = page.locator('[data-control="history"]');
        await history.check();
        assert.match(await page.locator(".org-content").innerText(), /导航文案整理/);
        await history.uncheck();
        assert.doesNotMatch(await page.locator(".org-content").innerText(), /导航文案整理/);

        const search = page.locator('[data-control="search"]');
        await search.fill("知衡");
        assert.match(await page.locator(".org-view-caption > span").innerText(), /2 项任务/);
        await search.fill("没有匹配的任务");
        assert.match(await page.locator(".org-view-caption > span").innerText(), /0 项任务/);
        await search.fill("");

        const filter = page.locator('[data-control="filter"]');
        await filter.selectOption("paused");
        assert.match(await page.locator(".org-content").innerText(), /普通文件夹工作区迁移/);
        assert.doesNotMatch(await page.locator(".org-content").innerText(), /组织架构与任务归属/);
        await filter.selectOption("all");

        if (variant !== "matrix") {
          const collapse = page.locator('.org-content [data-action="collapse"][data-task="org"]');
          await collapse.click();
          assert.equal(await collapse.getAttribute("aria-expanded"), "false");
          await collapse.click();
          assert.equal(await collapse.getAttribute("aria-expanded"), "true");
        }

        await page.locator('[data-control="leader"]').selectOption("jian");
        await page.locator('.org-content [data-action="member"][data-person="xu"]').first().click();
        await dialog.locator('[data-action="conversation"][data-task="search"]').click();
        assert.match(await dialog.innerText(), /尚未启动，没有执行记录/);
        assert.equal(await dialog.locator(".org-message").count(), 0);
        await dialog.locator('[data-action="close"]').click();
        await page.locator('[data-control="leader"]').selectOption("shou");
        assert.match(await page.locator(".org-view-caption > span").innerText(), /0 项任务/);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.locator('[data-control="leader"]').selectOption("lin");
        checks.push(`${variant} @${width} 多任务、作用域、历史、搜索、状态筛选、折叠、空组与未启动记录`);
      }
      await page.reload();
      assert.equal(await page.locator('[data-action="variant"][data-view="graph"]').getAttribute("aria-pressed"), "true");
      await context.close();
    }

    const darkContext = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: "dark", reducedMotion: "reduce" });
    const dark = await darkContext.newPage();
    dark.on("pageerror", error => errors.push(error.message));
    await dark.goto(pathToFileURL(path.join(__dirname, "index.html")).href);
    for (const variant of ["lanes", "matrix", "graph"]) {
      await dark.locator(`[data-action="variant"][data-view="${variant}"]`).click();
      await dark.screenshot({ path: path.join(output, `${variant}-dark.png`), fullPage: true });
    }
    await darkContext.close();
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, "evidence.json"), JSON.stringify({ ok: true, checks, errors, viewports: [1440, 768, 390], darkMode: true, reducedMotion: true }, null, 2));
    console.log(JSON.stringify({ ok: true, journeys: checks.length, errors: 0, evidence: output }));
  } finally {
    await browser.close();
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
