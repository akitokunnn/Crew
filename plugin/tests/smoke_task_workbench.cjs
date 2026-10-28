'use strict';
// Real Electron renderer + real HTTP API, with only the opt-in synthetic carrier.
// Run: node plugin/tests/smoke_task_workbench.cjs. Every created directory is retained.
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const desktop = path.join(root, 'desktop');
const out = path.join(desktop, 'smoke-output/task-workbench');
const forbiddenPorts = new Set(['47811', '7122', '2957', '4320', '4321', '3347']);
function isolatedEnv() {
  const env = {...process.env, PYTHONIOENCODING: 'utf-8'};
  for (const key of ['ELECTRON_RUN_AS_NODE', 'CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_GOAL_ID', 'CREW_EXECUTION_ID']) delete env[key];
  return env;
}

if (!process.versions.electron) {
  (async () => {
    await fs.mkdir(path.join(desktop, '.cache'), {recursive: true});
    const runRoot = await fs.mkdtemp(path.join(desktop, '.cache/task-workbench-'));
    const electron = require(require.resolve('electron', {paths: [desktop]}));
    const child = spawn(electron, ['--require', __filename, desktop, '--crew-workbench-run=' + runRoot],
      {cwd: desktop, env: isolatedEnv(), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
    child.stdout.pipe(process.stdout); child.stderr.pipe(process.stderr);
    const timer = setTimeout(() => { child.kill(); process.exitCode = 1; }, 180000);
    child.once('error', error => { clearTimeout(timer); console.error(error); process.exitCode = 1; });
    child.once('exit', async code => {
      clearTimeout(timer); child.stdout.destroy(); child.stderr.destroy();
      try {
        const evidence = JSON.parse(await fs.readFile(path.join(out, 'evidence.json'), 'utf8'));
        assert.equal(evidence.runRoot, runRoot); assert.equal(code, 0); assert.equal(evidence.ok, true);
        console.log(JSON.stringify({ok: true, checks: evidence.checks.length, seconds: evidence.elapsedSeconds,
          evidence: path.join(out, 'evidence.json'), retainedRunRoot: runRoot}));
      } catch (error) { console.error(error); process.exitCode = 1; }
    });
  })().catch(error => { console.error(error); process.exitCode = 1; });
} else {
  // This harness owns one direct renderer, not the desktop shell or its backend.
  const main = path.join(desktop, 'src/main.cjs');
  require.cache[require.resolve(main)] = {id: main, filename: main, loaded: true, exports: {}};
  const {app, BrowserWindow} = require('electron');
  const runRoot = process.argv.find(value => value.startsWith('--crew-workbench-run=')).slice('--crew-workbench-run='.length);
  const home = path.join(runRoot, 'home');
  require('node:fs').mkdirSync(path.join(runRoot, 'electron'), {recursive: true});
  app.setPath('userData', path.join(runRoot, 'electron'));
  const started = Date.now();
  const evidence = {runRoot, home, harness: 'direct real Electron BrowserWindow', checks: [], screenshots: [],
    errors: [], consoleErrors: [], sse: [], syntheticCarrier: 'serve_task_api.DemoCarrier', realAIStarted: false,
    cleanup: 'Only this script\'s fixture process is stopped; all directories and files are retained.'};
  let fixture, window, metadata, fixtureExited = false;
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function until(check, label, milliseconds = 15000) {
    const end = Date.now() + milliseconds;
    while (Date.now() < end) { if (await check()) return; await pause(40); }
    throw Error('Timed out: ' + label);
  }
  app.whenReady().then(async () => {
    let code = 0;
    try {
      await fs.mkdir(out, {recursive: true});
      fixture = spawn('python', ['-u', path.join(__dirname, 'serve_task_api.py'), '--home', home, '--port', '0'],
        {cwd: path.join(root, 'plugin'), env: isolatedEnv(), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
      fixture.once('exit', exitCode => { fixtureExited = true; evidence.fixtureExitCode = exitCode; });
      let pending = '';
      fixture.stdout.on('data', chunk => {
        pending += chunk;
        let newline;
        while ((newline = pending.indexOf('\n')) >= 0) {
          const line = pending.slice(0, newline); pending = pending.slice(newline + 1);
          try { const value = JSON.parse(line); if (value.url) metadata = value; } catch (_) { /* non-bootstrap diagnostics */ }
        }
      });
      fixture.stderr.on('data', chunk => { evidence.fixtureStderr = (evidence.fixtureStderr || '') + chunk; });
      await until(() => metadata || fixtureExited, 'fresh synthetic API fixture');
      assert(metadata, evidence.fixtureStderr);
      assert(metadata.synthetic); assert.equal(metadata.real_ai_started, false);
      assert(!forbiddenPorts.has(new URL(metadata.url).port), 'A fixture may not borrow a live development or formal port');
      assert.equal(path.resolve(metadata.home), path.resolve(home));
      assert(!path.resolve(metadata.project_root).startsWith(path.resolve(home) + path.sep));
      evidence.fixture = metadata;
      evidence.checks.push('fresh API fixture has an external ordinary project and an independent random port');
      window = new BrowserWindow({width: 1280, height: 900, show: false,
        webPreferences: {sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false, offscreen: true}});
      const wc = window.webContents;
      wc.setWindowOpenHandler(() => ({action: 'deny'}));
      wc.on('console-message', event => { if (event.level === 'error') evidence.consoleErrors.push(event.message); });
      wc.session.webRequest.onHeadersReceived({urls: [metadata.url + '/api/chat/events*']}, (details, callback) => {
        const contentType = Object.entries(details.responseHeaders || {}).find(([name]) => name.toLowerCase() === 'content-type')?.[1];
        evidence.sse.push({url: details.url, status: details.statusCode, contentType});
        callback({});
      });
      const js = source => wc.executeJavaScript(source, true);
      const wait = (source, label = source) => until(() => js(source), label);
      const click = selector => js(`document.querySelector(${JSON.stringify(selector)}).click();true`);
      async function input(selector, value) {
        await js(`(()=>{const node=document.querySelector(${JSON.stringify(selector)});node.value=${JSON.stringify(value)};node.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
      }
      async function api(route, data) {
        return js(`CrewClient.current.request(${JSON.stringify(route)}${data === undefined ? '' : ',' + JSON.stringify(data)})`);
      }
      async function check(label, source) { await wait(source, label); evidence.checks.push(label); }
      async function screenshot(name) {
        await js('document.fonts.ready.then(()=>true)'); await pause(120);
        const attached = wc.debugger.isAttached(); if (!attached) wc.debugger.attach('1.3');
        try {
          const image = await wc.debugger.sendCommand('Page.captureScreenshot', {format: 'png', fromSurface: true});
          const file = path.join(out, name + '.png'), bytes = Buffer.from(image.data, 'base64');
          await fs.writeFile(file, bytes);
          evidence.screenshots.push(file);
          evidence.viewports ||= [];
          evidence.viewports.push({name, pngWidth: bytes.readUInt32BE(16), pngHeight: bytes.readUInt32BE(20),
            ...await js('({width:innerWidth,height:innerHeight,documentWidth:document.documentElement.scrollWidth,bodyWidth:document.body.scrollWidth})')});
        } finally { if (!attached) wc.debugger.detach(); }
      }
      const noOverflow = `document.documentElement.scrollWidth<=innerWidth+1&&document.body.scrollWidth<=innerWidth+1`;
      const fits = selector => `(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el)return false;const r=el.getBoundingClientRect();return r.width>0&&r.left>=-1&&r.right<=innerWidth+1})()`;
      await window.loadURL(metadata.url + '/');
      await check('PC homepage uses the real API and loads all declared scripts and styles', `(async()=>CrewClient.current.mode==='real'&&!!document.querySelector('#wb-capture')&&(await Promise.all([...document.querySelectorAll('script[src],link[rel=stylesheet]')].map(el=>fetch(el.src||el.href).then(r=>r.status)))).every(status=>status===200))()`);
      await screenshot('00-pc-home');
      const before = (await api('/api/tasks')).tasks.length;
      const original = '合成 UI 验收：讨论普通目录中的报告效果，尚未授权施工。';
      await input('#wb-new-text', original);
      await js(`document.querySelector('#wb-capture select').value='api-demo';document.querySelector('#wb-capture').requestSubmit();true`);
      await wait(`location.hash.startsWith('#task?')&&!!document.querySelector('.tw-chat textarea')`);
      const taskId = await js(`new URLSearchParams(location.hash.split('?')[1]).get('taskId')`);
      let detail = await api('/api/task?task_id=' + taskId);
      evidence.taskId = taskId; evidence.leaderSessionId = detail.task.leader_session_id;
      assert.equal((await api('/api/tasks')).tasks.length, before + 1);
      assert.equal(detail.task.request, original); assert.equal(detail.task.authorized, false);
      evidence.checks.push('homepage starts exactly one stable task and retains the original request without authorizing work');
      await check('task conversation receives the explicit synthetic reply over real SSE', `document.querySelector('.tw-chat')?.textContent.includes('没有调用真实 AI')`);
      await input('.tw-chat textarea', '第二条 UI 合成输入，用于验证实际 SSE 推送。');
      await js(`document.querySelector('.tw-chat form.composer').requestSubmit();true`);
      await check('a second UI input receives its reply in the same task stream', `document.querySelector('.tw-chat')?.textContent.split('没有调用真实 AI').length>=3`);
      assert(evidence.sse.some(item => item.status === 200 && JSON.stringify(item.contentType).includes('text/event-stream')));
      await click('.tw-controls summary'); await click('[data-act="tw-mode"]');
      await wait(`document.querySelector('[data-act="tw-mode"]')?.getAttribute('aria-checked')==='true'`);
      detail = await api('/api/task?task_id=' + taskId);
      assert.equal(detail.task.mode, 'automatic'); assert.equal(detail.task.authorized, false);
      assert.equal(detail.task.id, taskId); assert.equal(detail.task.leader_session_id, evidence.leaderSessionId);
      evidence.checks.push('mode changes through the UI while the same discussion stays unauthorized');
      await click('.tw-controls summary');
      const draft = '尚未发送的任务草稿：返回后仍应保留。';
      await input('.tw-chat textarea', draft);
      await click('[data-act="wb-navigate"][data-route="#home"]');
      await wait(`!!document.querySelector('#wb-capture')`);
      await click(`.wb-main [data-act="wb-task"][data-id="${taskId}"]`);
      await check('leaving and returning through task navigation preserves the unsent conversation draft', `document.querySelector('.tw-chat textarea')?.value===${JSON.stringify(draft)}`);
      const related = path.join(runRoot, 'associated-project'); await fs.mkdir(related);
      await api('/api/project/init', {name: 'api-associated', root: related});
      await click('[data-act="tw-panel"][data-panel="toggle"]');
      await wait(`!!document.querySelector('.tw-details-form')`);
      const requirementsBefore = (await api('/api/task?task_id=' + taskId)).task;
      await input('.tw-details-form textarea[name="projects"]', 'api-associated');
      await js(`document.querySelector('.tw-details-form').requestSubmit();true`);
      await wait(`document.querySelector('#toast-root')?.textContent.includes('任务详情已保存')`);
      const updated = (await api('/api/task?task_id=' + taskId)).task;
      assert.equal(updated.request, original); assert.equal(updated.requirement_version, requirementsBefore.requirement_version);
      assert(updated.projects.includes('api-associated')); assert.equal(updated.authorized, false);
      evidence.checks.push('saving only a related workspace retains request bytes and requirement version');
      await api('/api/tasks/report', {task_id: taskId, version: 0, markdown: '# 真实合成报告\n\n报告对象来自此任务的真实 API。\n\n## 验证\n\n界面验收不调用真实模型。', request_id: 'smoke-report'});
      const attachment = (await api('/api/tasks/attachments', {task_id: taskId, filename: 'fixture-evidence.txt', mime_type: 'text/plain',
        data: Buffer.from('actual fixture attachment bytes').toString('base64'), request_id: 'smoke-attachment'})).attachment;
      await click('[data-act="tw-panel"][data-panel="deliverables"]');
      await check('materials render the report stored by the real task API', `document.querySelector('.td-report')?.textContent.includes('报告对象来自此任务的真实 API')`);
      const bytes = await js(`fetch('/api/tasks/attachment?task_id=${taskId}&attachment_id=${attachment.id}').then(r=>r.text())`);
      assert.equal(bytes, 'actual fixture attachment bytes'); evidence.attachmentId = attachment.id;
      evidence.checks.push('task attachment download returns its actual stored bytes');
      await screenshot('01-pc-materials');
      await click('[data-act="tw-panel"][data-panel="close"]');
      // Resize the actual renderer viewport. A detached CDP metrics override
      // would silently revert to desktop size and would not prove mobile fit.
      window.setContentSize(390, 844);
      wc.debugger.attach('1.3');
      await wc.debugger.sendCommand('Emulation.setDeviceMetricsOverride',
        {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
      await check('390px task viewport fits without horizontal page overflow', `innerWidth===390&&${noOverflow}&&${fits('.tw-material-toggle')}&&${fits('.tw-controls summary')}`);
      await screenshot('02-mobile-task');
      await click('.tw-controls summary');
      await check('390px task control menu and mode action remain within the viewport', `${noOverflow}&&${fits('.tw-control-menu')}&&${fits('[data-act="tw-mode"]')}`);
      await screenshot('03-mobile-controls'); await click('.tw-controls summary');
      await click('[data-act="tw-panel"][data-panel="toggle"]');
      await click('[data-act="tw-panel"][data-panel="deliverables"]');
      await check('390px materials and report remain usable without horizontal page overflow', `${noOverflow}&&${fits('.tw-drawer')}&&!!document.querySelector('.td-report')`);
      await screenshot('04-mobile-materials');
      await click('[data-act="tw-panel"][data-panel="close"]');
      await click('.tw-controls summary'); await click('[data-act="tw-settings"]');
      await check('390px scoped settings load real values and fit the viewport', `location.hash.startsWith('#settings?')&&!!document.querySelector('.ss-shell')&&${noOverflow}&&${fits('.ss-shell')}`);
      await screenshot('05-mobile-settings');
      await fs.access(path.join(metadata.project_root));
      await assert.rejects(fs.access(path.join(metadata.project_root, '.git')));
      assert.equal(evidence.consoleErrors.length, 0, JSON.stringify(evidence.consoleErrors));
      evidence.checks.push('ordinary project remains outside HOME without Git and the renderer reports no console errors');
      evidence.ok = true;
    } catch (error) {
      code = 1; evidence.ok = false; evidence.errors.push(error.stack || String(error)); console.error(error);
      if (window && !window.isDestroyed()) {
        try { evidence.failureState = await window.webContents.executeJavaScript(`({url:location.href,width:innerWidth,height:innerHeight,documentWidth:document.documentElement.scrollWidth,bodyWidth:document.body.scrollWidth,text:document.querySelector('#main')?.textContent.slice(-1800)})`); } catch (_) { /* retain original failure */ }
        try { await fs.writeFile(path.join(out, '99-failure.png'), (await window.webContents.capturePage()).toPNG()); } catch (_) { /* retain original failure */ }
      }
    } finally {
      if (fixture && !fixtureExited) fixture.kill();
      if (fixture) await until(() => fixtureExited, 'stop owned fixture', 10000).catch(error => { code = 1; evidence.errors.push(String(error)); });
      evidence.fixtureStopped = fixtureExited; evidence.elapsedSeconds = (Date.now() - started) / 1000;
      await fs.mkdir(out, {recursive: true}); await fs.writeFile(path.join(out, 'evidence.json'), JSON.stringify(evidence, null, 2));
      window?.destroy(); app.exit(code);
    }
  });
}
