'use strict';
// Real Chrome and HTTP/SQLite. All native model processes are injected fakes.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '../..');
const out = path.join(root, 'Temp/runtime-recovery');

(async () => {
  await fs.mkdir(out, { recursive: true });
  const env = { ...process.env, PYTHONIOENCODING: 'utf-8' };
  for (const name of ['CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID']) delete env[name];
  const fixture = spawn('python', ['plugin/tests/runtime_recovery_ui_fixture.py'], {
    cwd: root, env, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe']
  });
  let pending = '', stderr = '';
  const messages = [], waiters = [];
  fixture.stderr.on('data', chunk => { stderr += chunk; });
  fixture.stdout.on('data', chunk => {
    pending += chunk;
    let index;
    while ((index = pending.indexOf('\n')) >= 0) {
      const line = pending.slice(0, index);
      pending = pending.slice(index + 1);
      if (!line.trim()) continue;
      const value = JSON.parse(line);
      const waiter = waiters.shift();
      if (waiter) waiter(value);
      else messages.push(value);
    }
  });
  const next = () => messages.length ? Promise.resolve(messages.shift()) : new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(Error('Fixture timeout ' + stderr)), 15000);
    waiters.push(value => { clearTimeout(timeout); resolve(value); });
  });
  const command = async (runtime, action, seconds) => {
    fixture.stdin.write(JSON.stringify({ runtime, action, seconds }) + '\n');
    return next();
  };
  const evidence = { checks: [], screenshots: [], states: {}, page_errors: [], console_errors: [], requests: [], failed_requests: [] };
  let browser, page;
  try {
    const info = await next();
    assert(!['7865', '47811', '7122'].includes(new URL(info.url).port));
    evidence.fixture = info;
    browser = await chromium.launch({ headless: true, channel: 'chrome' });
    page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
    page.on('pageerror', error => evidence.page_errors.push(error.stack));
    page.on('requestfailed', request => evidence.failed_requests.push({ url: request.url(), error: request.failure() }));
    page.on('console', message => { if (message.type() === 'error') evidence.console_errors.push(message.text()); });
    page.on('request', request => {
      assert.notEqual(new URL(request.url()).port, '7865');
      if (request.method() === 'POST') evidence.requests.push(request.url());
    });
    const capture = async name => {
      const file = path.join(out, name + '.png');
      await page.screenshot({ path: file, fullPage: true });
      evidence.screenshots.push(file);
    };
    for (const runtime of ['claude', 'codex']) {
      const scope = info.scopes[runtime];
      await page.goto(info.url + '/?project=demo#leader=' + scope.sid);
      await page.locator('.chat-scroll').waitFor();
      const queued = await command(runtime, 'crash', 0);
      assert.equal(queued.running, false);
      assert.equal(queued.attempts, 0);
      await page.locator('.chat-error').filter({ hasText: '至少 5 秒后尝试第 1/3 次自动续回' }).waitFor();
      await capture(runtime + '-01-queued');
      evidence.checks.push(runtime + ' queued recovery is visible after confirmed native exit');

      const resumed = await command(runtime, 'poll', 10);
      assert.equal(resumed.running, true);
      assert.equal(resumed.attempts, 1);
      const continuation = page.locator('.mline').filter({ hasText: '原生载体意外退出，本轮未完成' });
      await continuation.waitFor();
      assert.match(await continuation.locator('.who').innerText(), /^Crew →/);
      assert.equal(await page.locator('.m-user').filter({ hasText: '原生载体意外退出，本轮未完成' }).count(), 0);
      await capture(runtime + '-02-resumed');
      evidence.checks.push(runtime + ' continuation uses the original native ID and displays Crew as sender');

      await command(runtime, 'crash', 20);
      assert.equal((await command(runtime, 'poll', 40)).attempts, 2);
      await command(runtime, 'crash', 50);
      assert.equal((await command(runtime, 'poll', 110)).attempts, 3);
      const exhausted = await command(runtime, 'crash', 120);
      assert.equal(exhausted.running, false);
      assert.equal(exhausted.attempts, 3);
      await page.locator('.chat-error').filter({ hasText: '3 次自动续回额度已用完' }).waitFor();
      await capture(runtime + '-03-exhausted');
      const later = await command(runtime, 'poll', 1000);
      assert.equal(later.running, false);
      assert.equal(later.attempts, 3);
      const state = await page.evaluate(() => fetch('/api/state?project=demo').then(response => response.json()));
      evidence.states[runtime] = state.leaders.find(person => person.session_id === scope.sid);
      assert.equal(evidence.states[runtime].status, 'offline');
      assert.equal(evidence.states[runtime].where, 'offline');
      assert.equal(evidence.states[runtime].running, false);
      assert.equal(evidence.states[runtime].busy, false);
      await page.waitForTimeout(3300);
      await capture(runtime + '-04-state-refreshed');
      evidence.checks.push(runtime + ' exhaustion is visible, state refresh is offline, and later polls never start a fourth recovery');
    }
    assert.deepEqual(evidence.page_errors, []);
    assert.deepEqual(evidence.console_errors, []);
    assert(evidence.requests.every(url => new URL(url).pathname === '/api/chat/read'));
  } catch (error) {
    evidence.failure = String(error);
    if (page) await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true });
    throw error;
  } finally {
    if (browser) await browser.close();
    fixture.stdin.write(JSON.stringify({ action: 'shutdown' }) + '\n');
    try { evidence.closed = await next(); } catch (error) { evidence.fixture_error = String(error); }
    fixture.stdin.end();
    await new Promise(resolve => fixture.once('exit', resolve));
    evidence.stderr = stderr;
    await fs.writeFile(path.join(out, 'evidence.json'), JSON.stringify(evidence, null, 2));
  }
  console.log(JSON.stringify({ checks: evidence.checks.length, screenshots: evidence.screenshots.length,
    page_errors: evidence.page_errors.length, console_errors: evidence.console_errors.length, evidence: out }));
})().catch(error => { console.error(error); process.exitCode = 1; });
