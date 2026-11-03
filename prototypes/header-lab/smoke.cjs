/* Real Chrome against the offline demos. This script never calls a Crew API. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {chromium} = require("playwright");

const project = path.resolve(__dirname, "../..");
const entry = path.join(__dirname, "demo.html");
const viewFilter = process.argv.find(value => value.startsWith("--view="))?.slice(7);
const widthFilter = process.argv.find(value => value.startsWith("--width="))?.slice(8);
const variants = viewFilter ? [viewFilter] : ["switcher", "sidebar", "document", "tabs"];
const widths = widthFilter ? [Number(widthFilter)] : [1440, 768, 390];
assert(variants.every(value => ["switcher", "sidebar", "document", "tabs"].includes(value)), "Unknown --view");
assert(widths.every(value => [1440, 768, 390].includes(value)), "Unknown --width");
const output = path.join(project, "Temp/header-lab-review", viewFilter || widthFilter ? `target-${viewFilter || "all"}-${widthFilter || "all"}` : "");
const evidence = {checks: [], failures: [], screenshots: [], consoleErrors: [], unexpectedRequests: [], headers: []};
let browser;

async function firstVisible(locator) {
  for (const candidate of await locator.all()) if (await candidate.isVisible()) return candidate;
  return null;
}

function check(label, condition) {
  const pass = Boolean(condition);
  evidence.checks.push({label, pass});
  assert(pass, label);
}

async function scenario(variant, width) {
  const context = await browser.newContext({viewport: {width, height: 900}, reducedMotion: "reduce", colorScheme: "light"});
  const page = await context.newPage();
  const prefix = `${variant}-${width}`;
  let sequence = 0;
  page.setDefaultTimeout(12000);
  page.on("pageerror", error => evidence.consoleErrors.push({variant, width, message: error.stack || String(error)}));
  page.on("console", message => {if (message.type() === "error") evidence.consoleErrors.push({variant, width, message: message.text()});});
  page.on("request", request => {
    const protocol = new URL(request.url()).protocol;
    const type = request.resourceType();
    if (["fetch", "xhr", "websocket", "eventsource"].includes(type) || !["file:", "data:", "blob:"].includes(protocol)) {
      evidence.unexpectedRequests.push({variant, width, type, method: request.method(), url: request.url()});
    }
  });
  async function shot(label) {
    const name = `${prefix}-${String(++sequence).padStart(2, "0")}-${label}.png`;
    await page.screenshot({path: path.join(output, name), fullPage: true, animations: "disabled"});
    evidence.screenshots.push(name);
  }
  async function layout(label) {
    const dimensions = await page.evaluate(() => ({page: document.documentElement.scrollWidth, viewport: innerWidth}));
    check(`${prefix}: ${label} has no page-level horizontal overflow`, dimensions.page <= dimensions.viewport + 1);
    const header = await page.locator("#demo-root [data-main-header]").evaluateAll(nodes => {
      const boxes = nodes.map(node => node.getBoundingClientRect()).filter(box => box.width && box.height);
      return boxes.length ? {height: Math.max(...boxes.map(box => box.bottom)) - Math.min(...boxes.map(box => box.top)), count: boxes.length} : null;
    });
    assert(header, `${prefix}: the rendered prototype must have a measurable main header`);
    evidence.headers.push({variant, width, label, ...header});
    check(`${prefix}: ${label} header stays compact`, header.height <= (width <= 760 ? 84 : 64) + 1);
  }
  async function openScopePicker() {
    let trigger = await firstVisible(page.locator('#demo-root [data-action="scope-switch"],#demo-root [data-action="task-list"]'));
    if (!trigger) {
      const menu = await firstVisible(page.locator('#demo-root [data-action="mobile-nav"]'));
      assert(menu, `${prefix}: task navigation must be reachable`);
      await menu.click();
      trigger = await firstVisible(page.locator('#demo-root [data-action="scope-switch"],#demo-root [data-action="task-list"]'));
    }
    assert(trigger, `${prefix}: a visible control must open the task list`);
    await trigger.click();
    const picker = page.locator('.scope-picker[role="dialog"]');
    await picker.waitFor({state: "visible"});
    return picker;
  }
  async function scope(id) {
    const direct = await firstVisible(page.locator(`#demo-root [data-action="scope"][data-scope="${id}"]`));
    if (direct) await direct.click();
    else {
      const picker = await openScopePicker();
      await picker.locator(`#scope-search`).fill("");
      await picker.locator(`[data-action="scope"][data-scope="${id}"]`).click();
      await picker.waitFor({state: "hidden"});
    }
    await page.locator("#message-input").waitFor({state: "visible"});
  }
  async function status(value) {
    await page.locator("#lab-status").selectOption(value);
    const patterns = {queued: /待规划|未启动|待开始/, running: /推进中|进行中|执行中/, waiting: /等待|拍板|确认/, delivered: /交付|验收/};
    const text = await page.locator("#demo-root .scope-status").evaluateAll(nodes => nodes.filter(node => node.getClientRects().length).map(node => node.textContent).join(" "));
    check(`${prefix}: ${value} is visible in the working interface`, patterns[value].test(text));
    await layout(value);
    await shot(value);
  }
  async function focusTrap(dialog, label) {
    const candidates = dialog.locator('button:not(:disabled),a[href],input:not(:disabled),textarea:not(:disabled),select:not(:disabled),[tabindex="0"]');
    const controls = [];
    for (const control of await candidates.all()) if (await control.isVisible()) controls.push(control);
    assert(controls.length >= 2, `${prefix}: ${label} needs at least two reachable controls`);
    await controls.at(-1).focus();
    await page.keyboard.press("Tab");
    check(`${prefix}: Tab wraps inside ${label}`, await controls[0].evaluate(node => document.activeElement === node));
    await page.keyboard.press("Shift+Tab");
    check(`${prefix}: Shift+Tab wraps inside ${label}`, await controls.at(-1).evaluate(node => document.activeElement === node));
    await shot(`${label}-focus-trap`);
  }
  try {
    const url = pathToFileURL(entry);
    url.searchParams.set("view", variant);
    await page.goto(url.href);
    await page.locator("#demo-root [data-main-header]").waitFor({state: "visible"});
    await page.locator("#message-input").waitFor({state: "visible"});
    await page.evaluate(() => document.fonts.ready);
    await layout("initial");
    await shot("initial");

    const picker = await openScopePicker();
    await picker.locator("#scope-search").fill("MiPS");
    check(`${prefix}: task search keeps the matching task reachable`, await picker.locator('[data-action="scope"][data-scope="mips"]').isVisible());
    await shot("task-search");
    await picker.locator('[data-action="scope"][data-scope="mips"]').click();
    await picker.waitFor({state: "hidden"});
    check(`${prefix}: opening a task does not start it`, await page.locator("#lab-status").inputValue() === "queued");
    const taskDraft = `${prefix}：任务内尚未发送的要求`;
    const generalDraft = `${prefix}：总会话的独立草稿`;
    const secondDraft = `${prefix}：延迟任务的独立草稿`;
    await page.locator("#message-input").fill(taskDraft);
    await shot("task-draft");
    await scope("general");
    check(`${prefix}: general conversation has no task status control`, await page.locator("#lab-status").isDisabled());
    await page.locator("#message-input").fill(generalDraft);
    await shot("general-draft");
    await scope("latency");
    await page.locator("#message-input").fill(secondDraft);
    await shot("second-task-draft");
    await scope("mips");
    check(`${prefix}: task draft survives navigation`, await page.locator("#message-input").inputValue() === taskDraft);
    check(`${prefix}: returning through conversations does not start a queued task`, await page.locator("#lab-status").inputValue() === "queued");
    await shot("return-task");
    await scope("latency");
    check(`${prefix}: second task draft stays independent`, await page.locator("#message-input").inputValue() === secondDraft);
    await scope("general");
    check(`${prefix}: general draft stays independent`, await page.locator("#message-input").inputValue() === generalDraft);
    await scope("mips");

    if (width === 1440) {
      const mode = await firstVisible(page.locator('#demo-root [data-action="mode"]'));
      assert(mode, `${prefix}: mode settings must be reachable`);
      await mode.click();
      const dialog = page.locator('.lab-panel[role="dialog"]');
      await focusTrap(dialog, "mode-dialog");
      await dialog.locator('[data-action="set-mode"][data-value="auto"]').click();
      check(`${prefix}: saving mode does not start a queued task`, await page.locator("#lab-status").inputValue() === "queued");
      check(`${prefix}: saved mode is visible in the composer`, (await page.locator(".composer-context").innerText()).includes("自主推进"));
      await shot("mode-saved-without-start");
      await scope("general");
      check(`${prefix}: task mode does not overwrite general mode`, (await page.locator(".composer-context").innerText()).includes("由我拍板"));
      const generalSent = `${prefix}：请先讨论整体安排，暂不启动任务。`;
      await page.locator("#message-input").fill(generalSent);
      await page.locator('#demo-root [data-action="send"]').click();
      check(`${prefix}: general message appears in general conversation`, (await page.locator("[data-conversation-content]").innerText()).includes(generalSent));
      await page.locator("#message-input").fill(generalDraft);
      await shot("general-send");
      await scope("mips");
      check(`${prefix}: sending to general does not start this task`, await page.locator("#lab-status").inputValue() === "queued");
      check(`${prefix}: sending to general does not replace the task draft`, await page.locator("#message-input").inputValue() === taskDraft);
      check(`${prefix}: general message is not routed into the task`, !(await page.locator("[data-conversation-content]").innerText()).includes(generalSent));
      await page.reload();
      await page.locator("#message-input").waitFor({state: "visible"});
      check(`${prefix}: reload preserves the current task draft`, await page.locator("#message-input").inputValue() === taskDraft);
      check(`${prefix}: reload preserves the queued state`, await page.locator("#lab-status").inputValue() === "queued");
      check(`${prefix}: reload preserves the saved mode`, (await page.locator(".composer-context").innerText()).includes("自主推进"));
      await scope("general");
      check(`${prefix}: reload preserves the independent general draft`, await page.locator("#message-input").inputValue() === generalDraft);
      await scope("mips");
      await shot("reload-preserved");
    }

    await page.locator("#lab-long-title").check();
    await layout("long-title");
    await shot("long-title");
    let detailsTrigger = await firstVisible(page.locator('#demo-root [data-main-header] [data-action="details"]'));
    if (!detailsTrigger) detailsTrigger = await firstVisible(page.locator('#demo-root [data-action="details"]'));
    assert(detailsTrigger, `${prefix}: task details must remain reachable`);
    const detailsSelector = await detailsTrigger.getAttribute("aria-label");
    await detailsTrigger.click();
    const details = page.locator('[data-panel="details"][role="dialog"]');
    await details.waitFor({state: "visible"});
    if (width === 1440) await focusTrap(details, "details-dialog");
    const title = details.locator("[data-detail-title]");
    check(`${prefix}: full long title is available in task details`, (await title.innerText()).length >= 55);
    check(`${prefix}: detail title wraps without clipping`, await title.evaluate(node => {
      const style = getComputedStyle(node);
      return style.whiteSpace !== "nowrap" && node.scrollWidth <= node.clientWidth + 1 && node.scrollHeight <= node.clientHeight + 1;
    }));
    await shot("details-long-title");
    await page.keyboard.press("Escape");
    await details.waitFor({state: "hidden"});
    check(`${prefix}: closing details returns focus to its opening control`, await page.evaluate(label => {
      const current = document.activeElement;
      return current?.dataset.action === "details" && (!label || current.getAttribute("aria-label") === label);
    }, detailsSelector));
    await shot("details-closed");
    await page.locator("#lab-long-title").uncheck();

    const start = await firstVisible(page.locator('#demo-root [data-action="start"]'));
    assert(start, `${prefix}: an unstarted task offers an explicit start`);
    await start.click();
    check(`${prefix}: explicit start advances this task`, await page.locator("#lab-status").inputValue() === "running");
    await shot("explicit-start");
    await page.locator("#lab-status").selectOption("queued");
    const sent = `${prefix}：请开始核对 NPU 部署与验收样本。`;
    await page.locator("#message-input").fill(sent);
    await page.locator('#demo-root [data-action="send"]').click();
    check(`${prefix}: an explicit send advances the queued task`, await page.locator("#lab-status").inputValue() === "running");
    check(`${prefix}: sending clears only the submitted draft`, await page.locator("#message-input").inputValue() === "");
    check(`${prefix}: the sent message appears in this conversation`, (await page.locator("#demo-root [data-conversation-content]").innerText()).includes(sent));
    await shot("message-sent");
    await scope("general");
    check(`${prefix}: sending a task message preserves the general draft`, await page.locator("#message-input").inputValue() === generalDraft);
    await scope("mips");

    for (const value of ["queued", "running", "waiting", "delivered"]) {
      await status(value);
      if (value === "waiting") {
        const request = page.locator("#demo-root .decision-request");
        await request.waitFor({state: "visible"});
        const action = request.locator('[data-action="decision"]');
        check(`${prefix}: pending decisions have a direct visible action`, await action.isVisible());
        await action.click();
        const dialog = await firstVisible(page.locator('[role="dialog"]'));
        assert(dialog, `${prefix}: direct decision action opens its choices`);
        await shot("decision-choices");
        await page.keyboard.press("Escape");
        await dialog.waitFor({state: "hidden"});
      }
    }
    if (width <= 760) {
      const menu = await firstVisible(page.locator('#demo-root [data-action="mobile-nav"]'));
      if (menu) {
        await menu.click(); await shot("mobile-navigation");
        await page.keyboard.press("Escape");
        check(`${prefix}: Escape returns mobile navigation focus to its menu`, await page.evaluate(() => document.activeElement?.dataset.action === "mobile-nav"));
      }
    }
    const lightSurface = await page.locator("#demo-root").evaluate(node => getComputedStyle(node).getPropertyValue("--surface").trim());
    await page.locator("#lab-theme").click();
    const darkSurface = await page.locator("#demo-root").evaluate(node => getComputedStyle(node).getPropertyValue("--surface").trim());
    check(`${prefix}: dark theme changes the actual surface tokens`, Boolean(darkSurface) && lightSurface !== darkSurface);
    await layout("dark"); await shot("dark");

    if (width === 1440) {
      await page.locator('#lab-toolbar [data-action="toggle-tools"]').click();
      check(`${prefix}: trial tools can be hidden`, await page.locator("#lab-toolbar").isHidden());
      await layout("tools-hidden"); await shot("tools-hidden");
      await page.locator('.prototype-footer [data-action="toggle-tools"]').click();
      check(`${prefix}: trial tools can be restored from the footer`, await page.locator("#lab-toolbar").isVisible());
      await shot("tools-restored");
      await page.locator('.prototype-footer [data-action="note"]').click();
      const note = `${prefix}：测试笔记，切换清晰；希望明天继续比较手机标题与任务资料。`;
      await page.locator("#lab-notes").fill(note);
      await focusTrap(page.locator('.lab-panel[role="dialog"]'), "notes-dialog");
      await page.reload();
      await page.locator('#demo-root [data-main-header]').waitFor({state: "visible"});
      await page.locator('.prototype-footer [data-action="note"]').click();
      check(`${prefix}: notes auto-save without pressing a save button`, await page.locator("#lab-notes").inputValue() === note);
      await shot("notes-restored");
      const downloadPromise = page.waitForEvent("download");
      await page.locator('[data-action="export-notes"]').click();
      const download = await downloadPromise;
      const downloaded = path.join(output, `${prefix}-exported-notes.txt`);
      await download.saveAs(downloaded);
      check(`${prefix}: notes export downloads readable content`, (await fs.readFile(downloaded, "utf8")).includes(note));
      check(`${prefix}: exported notes have a descriptive filename`, download.suggestedFilename().includes("Crew") && download.suggestedFilename().endsWith(".txt"));
      await page.keyboard.press("Escape");
      await shot("notes-exported");
    }
  } catch (error) {
    evidence.failures.push({variant, width, error: error.stack || String(error)});
    try {await shot("failure");} catch { /* The primary failure remains in the evidence. */ }
  } finally {
    await context.close();
  }
}

(async () => {
  await fs.access(entry); // Missing files are a setup error; never poll for a half-written page.
  await fs.mkdir(output, {recursive: true});
  browser = await chromium.launch({headless: true, channel: "chrome"});
  for (const variant of variants) for (const width of widths) await scenario(variant, width);
  assert.deepEqual(evidence.consoleErrors, [], "Browser console errors fail the review");
  assert.deepEqual(evidence.unexpectedRequests, [], "Offline demos cannot fetch data or call a network/API");
  assert.deepEqual(evidence.failures, [], "All four prototype journeys must pass");
})().catch(error => {evidence.error = error.stack || String(error); process.exitCode = 1;}).finally(async () => {
  await browser?.close();
  await fs.mkdir(output, {recursive: true});
  await fs.writeFile(path.join(output, "evidence.json"), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({output, checks: evidence.checks.length, failures: evidence.failures.length, consoleErrors: evidence.consoleErrors.length, unexpectedRequests: evidence.unexpectedRequests.length, error: evidence.error}));
});
