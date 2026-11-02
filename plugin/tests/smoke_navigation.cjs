/* Real HTTP, SQLite and Chromium; the shared fixture replaces only AI carriers. */
'use strict';
const {chromium} = require('playwright');
const {spawn, execFileSync} = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const baseline = process.argv.find(arg => arg.startsWith('--baseline='))?.slice('--baseline='.length);
const out = path.join(root, 'Temp/navigation-review', baseline ? 'before' : 'after');
const evidence = {baseline, checks: [], failures: [], consoleErrors: [], screenshots: [], modelRequests: []};
let fixture, browser;

async function main() {
  await fs.mkdir(out, {recursive: true});
  const env = {...process.env, PYTHONIOENCODING: 'utf-8', CREW_NAVIGATION_REVIEW_FIXTURE: '1'};
  for (const key of ['CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID']) delete env[key];
  fixture = spawn('python', ['-u', path.join(__dirname, 'conversation_routing_fixture.py')], {
    cwd: path.join(root, 'plugin'), env, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe']
  });
  let buffer = '', info;
  fixture.stderr.on('data', chunk => process.stderr.write(chunk));
  fixture.stdout.on('data', chunk => {
    buffer += chunk;
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const value = JSON.parse(buffer.slice(0, newline));
      if (value.url) info = value;
      buffer = buffer.slice(newline + 1);
    }
  });
  const deadline = Date.now() + 20000;
  while (!info && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 50));
  assert(info, 'isolated fixture bootstrap');
  evidence.fixture = info;
  browser = await chromium.launch({headless: true, channel: 'chrome'});
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, colorScheme: 'light'});
  page.on('pageerror', error => evidence.consoleErrors.push(error.stack || String(error)));
  page.on('console', message => { if (message.type() === 'error') evidence.consoleErrors.push(message.text()); });
  page.on('request', request => {
    if (request.method() === 'POST' && /\/api\/(chat\/send|goals\/(action|agent))/.test(request.url())) evidence.modelRequests.push(request.url());
  });
  if (baseline) {
    for (const name of ['app.js', 'goal-ui.js', 'goal-ui.css', 'style.css']) {
      const body = execFileSync('git', ['show', `${baseline}:plugin/crew/web/${name}`], {cwd: root, encoding: 'utf8'});
      await page.route(`**/${name}`, route => route.fulfill({status: 200, contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript', body}));
    }
  }
  const general = '#goal-head [data-goal-act="general"]';
  const list = '#goal-head [data-goal-act="list"]';
  const task = id => `#goal-head [data-goal-act="open"][data-goal-id="${id}"]`;
  async function check(label, pass) {
    evidence.checks.push({label, pass});
    if (!pass) evidence.failures.push(label);
  }
  async function capture(name) {
    await page.screenshot({path: path.join(out, name + '.png'), fullPage: true, animations: 'disabled'});
    evidence.screenshots.push(name + '.png');
  }
  async function position() {
    await page.locator(list).waitFor({state: 'visible'});
    return page.locator(list).evaluate(node => { const r = node.getBoundingClientRect(); return {x: r.x, y: r.y}; });
  }
  async function fixed(label, expected) {
    const actual = await position();
    (evidence.positions ||= []).push({label, expected, actual});
    await check(label, Math.abs(actual.x - expected.x) < 2 && Math.abs(actual.y - expected.y) < 2);
  }
  async function assistant(label) {
    await check(label, await page.locator('#sidebar .sb-foot a[href="/assistant.html"]').count() === 1 &&
      await page.locator('#sidebar > a[href="/assistant.html"]').count() === 0);
  }
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({width, height: 900});
    await page.goto(info.url + '/?project=demo#leader=' + info.leader);
    await page.locator('#chat-host textarea').waitFor();
    await page.evaluate(() => document.fonts.ready);
    await page.locator('#chat-host textarea').fill('总会话草稿 ' + width);
    const original = await position();
    await capture(width + '-general');
    await page.locator(list).click();
    await page.locator(task(info.goals[0])).waitFor();
    await fixed(width + ': list stays in its original position', original);
    await capture(width + '-list');
    await page.locator(task(info.goals[0])).click();
    await page.locator('#goal-head [data-goal-detail]').waitFor();
    await page.locator('#chat-host textarea').fill('任务草稿 ' + width);
    await fixed(width + ': returning from a task uses the same left navigation', original);
    await check(width + ': both navigation tabs remain available inside a task', await page.locator('#goal-head .goal-view-tabs [role="tab"]').count() === 2);
    await check(width + ': task heading and decision controls fit the viewport', await page.locator('#goal-head').evaluate(node => {
      const mode = node.querySelector('.goal-mode-control').getBoundingClientRect();
      return node.scrollWidth <= node.clientWidth && mode.left >= 0 && mode.right <= innerWidth;
    }));
    await capture(width + '-task');
    await page.locator(list).click();
    await page.locator(task(info.goals[1])).click();
    await check(width + ': switching tasks opens the selected task', await page.locator('#goal-head [data-goal-detail]').getAttribute('data-goal-detail') === info.goals[1]);
    await page.locator(list).click();
    await page.locator(task(info.goals[0])).click();
    await check(width + ': returning through the list preserves the task draft', await page.locator('#chat-host textarea').inputValue() === '任务草稿 ' + width);
    await page.locator(general).click();
    await check(width + ': general draft remains independent', await page.locator('#chat-host textarea').inputValue() === '总会话草稿 ' + width);
    await page.locator(general).focus();
    await page.keyboard.press('End');
    await check(width + ': keyboard reaches the task list', await page.locator(list).getAttribute('aria-selected') === 'true');
    await page.keyboard.press('Home');
    await check(width + ': keyboard returns to general conversation', await page.locator(general).getAttribute('aria-selected') === 'true');
    if (width <= 760) await page.locator('#mobile-menu').click();
    await assistant(width + ': assistant stays in the sidebar utility area');
    await check(width + ': workbench is the first project destination', await page.locator('#sidebar > button.nav-item').first().getAttribute('data-view') === 'all');
    await capture(width + '-sidebar');
    if (width <= 760) await page.locator('[data-act="navigation-close"]').click();
    await check(width + ': page has no horizontal overflow', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  await page.setViewportSize({width: 1440, height: 900});
  for (const view of ['all', 'channel', 'todos', 'usage']) {
    await page.locator(`#sidebar [data-act="view"][data-view="${view}"]`).first().click();
    await assistant('Assistant remains available on ' + view);
  }
  for (const person of info.navigation) {
    for (const member of [false, true]) {
      const hash = member ? `history=${person.member}&leader=${person.leader}` : `leader=${person.leader}`;
      await page.goto(`${info.url}/?project=${person.project}#${hash}&goal=${person.goal}`);
      await page.locator('#goal-head [data-goal-detail]').waitFor();
      const label = person.runtime + (member ? ' member' : ' leader');
      await check(label + ': task navigation stays in the common tab group', await page.locator('#goal-head .goal-view-tabs [role="tab"]').count() === 2);
      await assistant(label + ': assistant is available in this project');
      await capture(label.replaceAll(' ', '-') + '-task');
      await page.locator(list).click();
      await page.locator(task(person.goal)).waitFor();
      await page.locator(general).click();
      await check(label + ': list and general navigation preserve the selected person', new URLSearchParams(new URL(page.url()).hash.slice(1)).get(member ? 'history' : 'leader') === (member ? person.member : person.leader));
    }
  }
  await page.emulateMedia({colorScheme: 'dark'});
  await page.reload();
  await page.locator(general).waitFor();
  await capture('dark-general');
  await page.setViewportSize({width: 390, height: 640});
  await page.locator('#mobile-menu').click();
  await check('Short mobile sidebar keeps the assistant visible', await page.locator('#sidebar a[href="/assistant.html"]').evaluate(node => {
    const box = node.getBoundingClientRect();
    return box.top >= 0 && box.bottom <= innerHeight;
  }));
  await capture('short-mobile-sidebar');
  await page.locator('[data-act="navigation-close"]').click();
  await page.setViewportSize({width: 1440, height: 900});
  await page.locator('#sidebar a[href="/assistant.html"]').click();
  await page.waitForURL('**/assistant.html');
  await page.locator('#assistant-chat textarea').waitFor();
  await check('Utility entry opens the shared assistant page', new URL(page.url()).pathname === '/assistant.html');
  await capture('assistant');
  assert.equal(evidence.modelRequests.length, 0, 'navigation must not send messages or start tasks');
  assert.deepEqual(evidence.consoleErrors, []);
  assert.deepEqual(evidence.failures, []);
}
main().catch(error => { evidence.error = error.stack || String(error); process.exitCode = 1; }).finally(async () => {
  await browser?.close();
  if (fixture) {
    fixture.stdin.end('shutdown\n');
    await new Promise(resolve => { fixture.once('exit', resolve); setTimeout(() => { fixture.kill(); resolve(); }, 5000).unref(); });
  }
  await fs.writeFile(path.join(out, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({out, checks: evidence.checks.length, failures: evidence.failures, consoleErrors: evidence.consoleErrors, error: evidence.error}));
});
