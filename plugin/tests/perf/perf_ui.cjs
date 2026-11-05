'use strict';
// Opt-in Electron offscreen measurement against a real isolated Crew backend.
const fs = require('node:fs'), fsp = require('node:fs/promises'), path = require('node:path');
const os = require('node:os'), assert = require('node:assert/strict'), {spawn, execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '../../..'), desktop = path.join(root, 'desktop');
function option(name, defaultValue) {
  const item = process.argv.find(value => value.startsWith('--' + name + '='));
  return item === undefined ? defaultValue : item.slice(name.length + 3);
}
const output = path.resolve(option('output', path.join(root, 'Temp/tasks/perf-evidence/ui.json')));
const iterations = Number(option('iterations', '3'));
assert(Number.isInteger(iterations) && iterations > 0);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

if (!process.versions.electron) {
  (async () => {
    const link = path.join(desktop, 'node_modules'), shared = 'C:\\Tools\\Crew\\desktop\\node_modules';
    let createdLink = false;
    const electronHome = await fsp.mkdtemp(path.join(os.tmpdir(), 'crew-perf-electron-'));
    try {
      if (!fs.existsSync(link)) { await fsp.symlink(shared, link, 'junction'); createdLink = true; }
      // Copy the existing runtime, never alter shared node_modules ACLs. Windows
      // Electron checks this ACL even with --no-sandbox, before application code.
      const runtime = path.join(electronHome, 'runtime');
      await fsp.cp(path.dirname(require(require.resolve('electron', {paths:[desktop]}))), runtime, {recursive:true});
      execFileSync('icacls', [runtime, '/grant', '*S-1-15-2-1:(OI)(CI)(RX)',
        '*S-1-1-0:(OI)(CI)(RX)', '*S-1-5-12:(OI)(CI)(RX)', '/T', '/Q'], {windowsHide:true, stdio:'pipe'});
      const env = {...process.env};
      for (const key of ['ELECTRON_RUN_AS_NODE', 'CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID']) delete env[key];
      const routing = process.argv.includes('--routing-smoke');
      const child = spawn(path.join(runtime, 'electron.exe'),
        ['--no-sandbox', '--require', routing ? path.join(root, 'plugin/tests/smoke_conversation_routing.cjs') : __filename, desktop,
          ...process.argv.slice(2).filter(value => !value.startsWith('--output=')),
          '--output=' + output, '--electron-home=' + electronHome, '--crew-routing-home=' + electronHome],
        {cwd:desktop, env, windowsHide:true, stdio:['ignore', 'pipe', 'pipe']});
      console.log('perf-ui: spawned electron pid=' + child.pid);
      child.stdout.pipe(process.stdout); child.stderr.pipe(process.stderr);
      // Time limit is a failed run, with the original error retained.
      const timer = setTimeout(() => child.kill(), 300000);
      const code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('exit', resolve); });
      clearTimeout(timer);
      assert.equal(code, 0, 'Electron performance child failed');
      const report = routing ? path.join(desktop, 'smoke-output/conversation-routing/evidence.json') : output;
      const evidence = JSON.parse(await fsp.readFile(report, 'utf8'));
      assert.equal(routing ? evidence.home : evidence.electronHome, electronHome);
      assert.equal(evidence.errors.length, 0);
      assert.equal(evidence.ok, true);
      console.log(JSON.stringify({ok:true, output:report, measurements:routing ? evidence.checks.length : evidence.actions.length}));
    } finally {
      if (createdLink) {
        assert((await fsp.lstat(link)).isSymbolicLink());
        await fsp.rmdir(link); // Remove only the junction, never its target.
      }
      assert(path.resolve(electronHome).startsWith(path.resolve(os.tmpdir()) + path.sep));
      await fsp.rm(electronHome, {recursive:true});
    }
  })().catch(error => { console.error(error.name + ': ' + error.message, error.stack); process.exitCode = 1; });
} else {
  console.log('perf-ui: preload entered');
  require.cache[require.resolve(path.join(desktop, 'src/main.cjs'))] = {
    id:path.join(desktop, 'src/main.cjs'), filename:path.join(desktop, 'src/main.cjs'), loaded:true, exports:{}};
  const {app, BrowserWindow} = require('electron');
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch('in-process-gpu');
  app.on('window-all-closed', () => {});
  const electronHome = option('electron-home');
  app.setPath('userData', electronHome);
  const evidence = {electronHome, errors:[], actions:[], screenshots:[], iterations,
    versions:process.versions, rendering:'software rendering; in-process GPU; no-sandbox; offscreen 1280x900',
    instrumentation:'CDP response-only render wrapper; PerformanceObserver; MutationObserver; double rAF paint boundary'};
  let fixture, window, exited, instrumentationError;
  app.whenReady().then(async () => {
    try {
      console.log('perf-ui: electron ready');
      await fsp.mkdir(path.dirname(output), {recursive:true});
      const env = {...process.env, PYTHONIOENCODING:'utf-8'};
      for (const key of ['CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID']) delete env[key];
      const fixtureArgs = [];
      // Optional fixture scale uses exactly the Python script's options, no env overrides.
      for (const name of ['workspaces','sessions','goals','todos','decisions','runs','previous','journal-events','large-output-bytes']) {
        const value = option(name); if (value !== undefined) fixtureArgs.push('--' + name, value);
      }
      const tiers = option('tiers'); if (tiers !== undefined) fixtureArgs.push('--tiers', ...tiers.split(','));
      fixture = spawn('python', ['-u', path.join(__dirname, 'perf_fixture.py'), ...fixtureArgs],
        {cwd:root, env, windowsHide:true, stdio:['pipe','pipe','pipe']});
      exited = new Promise((resolve, reject) => { fixture.once('exit', resolve); fixture.once('error', reject); });
      fixture.stderr.on('data', chunk => process.stderr.write(chunk));
      let pending = '', bootstrap;
      const fixtureMessages = [];
      fixture.stdout.on('data', chunk => {
        pending += chunk;
        let index;
        while ((index = pending.indexOf('\n')) >= 0) {
          const line = pending.slice(0, index); pending = pending.slice(index + 1);
          const value = JSON.parse(line); fixtureMessages.push(value); if (value.url) bootstrap = value;
        }
      });
      async function until(check, label) {
        const deadline = Date.now() + 120000;
        while (Date.now() < deadline) {
          if (instrumentationError) throw instrumentationError;
          if (await check()) return;
          if (fixture.exitCode !== null) throw Error('Fixture exited: ' + fixture.exitCode);
          await delay(40);
        }
        throw Error('Timed out: ' + label);
      }
      await until(() => bootstrap, 'fixture ready');
      console.log('perf-ui: fixture ready home=' + bootstrap.home);
      assert(!['47811','7122'].includes(new URL(bootstrap.url).port));
      evidence.fixture = bootstrap;
      window = new BrowserWindow({width:1280, height:900, show:false,
        webPreferences:{sandbox:true, contextIsolation:true, nodeIntegration:false,
          backgroundThrottling:false, offscreen:true}});
      console.log('perf-ui: window created');
      await window.loadURL('about:blank');
      console.log('perf-ui: blank context ready');
      const wc = window.webContents;
      async function js(source) {
        const value = await wc.debugger.sendCommand('Runtime.evaluate', {
          expression:source, returnByValue:true, awaitPromise:true, userGesture:true});
        if (value.exceptionDetails) throw Error(JSON.stringify(value.exceptionDetails));
        return value.result.value;
      }
      wc.on('render-process-gone', (_event, detail) => { instrumentationError = Error('Renderer exited: ' + JSON.stringify(detail)); });
      wc.on('console-message', event => { if (event.level === 'error') evidence.errors.push({name:'ConsoleError', message:event.message}); });
      wc.debugger.attach('1.3');
      wc.debugger.on('message', (_event, method, params) => {
        if (method !== 'Fetch.requestPaused') return;
        console.log('perf-ui: instrumenting chat.js');
        (async () => {
          const body = await wc.debugger.sendCommand('Fetch.getResponseBody', {requestId:params.requestId});
          const original = body.base64Encoded ? Buffer.from(body.body, 'base64').toString('utf8') : body.body;
          const needle = '  function render(context) {';
          assert.equal(original.split(needle).length, 2, 'Exact render entry required');
          const instrumented = original.replace(needle,
            `  function render(context) { const start = performance.now(); try { return perfOriginalRender(context); } finally { performance.measure('crew.chat.render', {start, end:performance.now()}); } }\n  function perfOriginalRender(context) {`);
          await wc.debugger.sendCommand('Fetch.fulfillRequest', {requestId:params.requestId,
            responseCode:200, responseHeaders:[{name:'Content-Type', value:'text/javascript; charset=utf-8'}],
            body:Buffer.from(instrumented).toString('base64')});
          console.log('perf-ui: chat.js instrumented');
        })().catch(error => { instrumentationError = error; console.error(error.name + ': ' + error.message); });
      });
      await wc.debugger.sendCommand('Fetch.enable', {patterns:[{urlPattern:'*/chat.js', requestStage:'Response'}]});
      console.log('perf-ui: interception ready');
      const browserInstrumentation = `
        window.perfEvidence = {longtasks:[], actions:[], errors:[]};
        new PerformanceObserver(list => { for (const e of list.getEntries()) perfEvidence.longtasks.push({start:e.startTime, duration:e.duration}); }).observe({type:'longtask', buffered:true});
        addEventListener('error', e => perfEvidence.errors.push({name:e.error?.name || 'Error', message:e.message}));
        addEventListener('unhandledrejection', e => perfEvidence.errors.push({name:e.reason?.name || 'UnhandledRejection', message:String(e.reason)}));
        window.perfBegin = (label, mode, text) => {
          const item = {label, mode, text, start:performance.now(), blankIntervals:[], dom:null, visible:null, paint:null, assistantDom:null, assistantVisible:null, assistantPaint:null};
          const previous = document.querySelector('#chat-host .chat-inner');
          let blankStart = null, done = false;
          const probe = () => {
            const inner = document.querySelector('#chat-host .chat-inner');
            const count = inner ? inner.querySelectorAll('.m-user,.m-lead').length : 0;
            const now = performance.now();
            if (!count && blankStart === null) blankStart = now;
            if (count && blankStart !== null) { item.blankIntervals.push({start:blankStart, end:now}); blankStart = null; }
            const inViewport = node => {
              const bounds = node.getBoundingClientRect(), viewport = document.querySelector('#chat-host .chat-scroll').getBoundingClientRect();
              return bounds.width > 0 && bounds.height > 0 && bounds.bottom > viewport.top && bounds.top < viewport.bottom;
            };
            const nodes = inner ? [...inner.querySelectorAll(mode === 'send' ? '.m-user .message-text' : '.m-user,.m-lead')] : [];
            const present = mode === 'send' ? nodes.find(n => n.textContent === text) : inner !== previous && count > 0;
            if (present && item.dom === null) item.dom = now;
            const visible = present && (mode === 'send' ? inViewport(present) : nodes.some(inViewport));
            if (visible && item.visible === null) {
              item.visible = now;
              requestAnimationFrame(() => requestAnimationFrame(() => { item.paint = performance.now(); }));
            }
            if (mode === 'send') {
              const assistant = [...document.querySelectorAll('#chat-host .m-lead')].find(n => n.textContent.includes('PERF_ASSISTANT_'));
              if (assistant && item.assistantDom === null) item.assistantDom = now;
              if (assistant && item.assistantVisible === null && inViewport(assistant)) {
                item.assistantVisible = now;
                requestAnimationFrame(() => requestAnimationFrame(() => { item.assistantPaint = performance.now(); }));
              }
            }
            if (item.paint !== null && (mode !== 'send' || item.assistantPaint !== null)) { done = true; observer.disconnect(); }
          };
          const observer = new MutationObserver(probe);
          observer.observe(document.body, {subtree:true, childList:true, characterData:true});
          // Exactly one frame loop; mutation checks do not schedule extra loops.
          function tick() { probe(); if (!done) requestAnimationFrame(tick); }
          requestAnimationFrame(tick);
          perfEvidence.actions.push(item);
          return true;
        };
      `;
      window.loadURL(bootstrap.url + '/?project=' + bootstrap.projects[0]).catch(error => { instrumentationError = error; });
      await until(() => js(`location.origin === ${JSON.stringify(bootstrap.url)} && document.readyState === "complete"`), 'page ready');
      await js(browserInstrumentation);
      console.log('perf-ui: page loaded');
      await until(() => js(`!!document.querySelector('[data-act="leader"][data-id="${bootstrap.history_sessions[1]}"]')`), 'sidebar');
      async function capture(name) {
        const image = await wc.debugger.sendCommand('Page.captureScreenshot', {format:'png', fromSurface:true});
        const file = path.join(path.dirname(output), output.replace(/^.*[\\/]/, '').replace(/\.json$/, '') + '-' + name + '.png');
        await fsp.writeFile(file, Buffer.from(image.data, 'base64')); evidence.screenshots.push(file);
      }
      async function open(label, sid) {
        const selector = `[data-act="leader"][data-id="${sid}"]`;
        await js(`perfBegin(${JSON.stringify(label)}, 'open'); document.querySelector(${JSON.stringify(selector)}).click(); true;`);
        await until(() => js('perfEvidence.actions.at(-1).paint !== null'), label);
        await until(() => js('document.querySelector("#chat-host textarea") && !document.querySelector("#chat-host textarea").disabled'), 'composer');
        await delay(100);
      }
      await capture('00-home');
      await open('first-open-20k', bootstrap.history_sessions[1]);
      console.log('perf-ui: first conversation measured');
      await capture('01-first-open');
      for (let i = 0; i < iterations; i++) {
        await open('switch-out-' + i, bootstrap.history_sessions[0]);
        await open('switch-back-20k-' + i, bootstrap.history_sessions[1]);
      }
      await capture('02-switched-back');
      for (const [runtime, sid] of Object.entries(bootstrap.send_sessions)) {
        await open('before-send-' + runtime, sid);
        await js(`document.querySelector('#chat-host .jump').click(); true;`);
        await delay(200);
        const text = 'PERF_USER_' + runtime;
        await js(`(() => { const input = document.querySelector('#chat-host textarea'); input.value = ${JSON.stringify(text)}; input.dispatchEvent(new Event('input', {bubbles:true})); perfBegin('send-${runtime}', 'send', ${JSON.stringify(text)}); document.querySelector('#chat-host .send').click(); })();`);
        await until(() => js('perfEvidence.actions.at(-1).assistantPaint !== null && perfEvidence.actions.at(-1).paint !== null'), 'send ' + runtime);
        await capture('03-send-' + runtime);
      }
      await until(() => js('!document.querySelector("#chat-host .chat-activity")'), 'fake turn idle');
      const pollStart = await js('performance.now()');
      await delay(10000);
      const pollEnd = await js('performance.now()');
      evidence.browser = await js(`JSON.parse(JSON.stringify({ ...perfEvidence, renders:performance.getEntriesByName('crew.chat.render').map(e => ({start:e.startTime,duration:e.duration})), resources:performance.getEntriesByType('resource').filter(e => /\\/api\\/(state|chat\\/history|chat\\/send)/.test(e.name)).map(e => ({url:e.name,start:e.startTime,duration:e.duration,bytes:e.transferSize,responseStart:e.responseStart,responseEnd:e.responseEnd})) }))`);
      for (const action of evidence.browser.actions) {
        const renders = evidence.browser.renders.filter(e => e.start >= action.start && e.start <= action.paint);
        evidence.actions.push({...action, toDomMs:action.dom - action.start, toPaintMs:action.paint - action.start,
          blankMs:action.blankIntervals.reduce((sum, gap) => sum + gap.end - gap.start, 0) +
            (action.mode === 'open' ? action.paint - action.dom : 0),
          toAssistantDomMs:action.assistantDom === null ? null : action.assistantDom - action.start,
          toAssistantPaintMs:action.assistantPaint === null ? null : action.assistantPaint - action.start,
          renders, renderMs:renders.reduce((sum,e) => sum + e.duration, 0),
          longtasks:evidence.browser.longtasks.filter(e => e.start >= action.start && e.start < action.paint)});
      }
      const tasks = evidence.browser.longtasks.filter(e => e.start >= pollStart && e.start < pollEnd);
      evidence.polling = {start:pollStart,end:pollEnd,durationMs:pollEnd-pollStart,longtasks:tasks,
        longtaskCount:tasks.length,longtaskTotalMs:tasks.reduce((sum,e) => sum+e.duration,0),
        requests:evidence.browser.resources.filter(e => e.responseEnd >= pollStart && e.responseEnd < pollEnd && new URL(e.url).pathname === '/api/state')};
      evidence.errors.push(...evidence.browser.errors);
      assert.equal(evidence.errors.length, 0, JSON.stringify(evidence.errors));
      assert(evidence.browser.renders.length > 0, 'render instrumentation missing');
      assert(evidence.polling.requests.length >= 3, 'Need at least three actual poll requests');
      await capture('04-polling');
      // Behavior checks run after the unchanged measurement window.
      evidence.loadingChecks = [];
      const historyReads = await js(`performance.getEntriesByType('resource').filter(e => { const u = new URL(e.name); return u.pathname === '/api/chat/history' && u.searchParams.get('session_id') === ${JSON.stringify(bootstrap.history_sessions[1])}; }).map(e => e.name)`);
      assert.equal(historyReads.length, 1, 'Switch-back must reuse its transcript instead of refetching history');
      assert.equal(new URL(historyReads[0]).searchParams.get('tail'), '1500');
      evidence.loadingChecks.push('first load uses tail=1500; all measured switch-backs use only SSE');
      await js(`(() => {
        const originalFetch = window.fetch;
        window.fetch = async (url, options) => {
          if (String(url).startsWith('/api/chat/send')) {
            window.fetch = originalFetch;
            await new Promise(resolve => { window.releaseLoadingSend = resolve; });
          }
          return originalFetch(url, options);
        };
        const input = document.querySelector('#chat-host textarea'); input.value = 'PERF_PENDING_RECEIPT'; input.dispatchEvent(new Event('input', {bubbles:true}));
        document.querySelector('#chat-host .send').click();
      })()`);
      assert(await js(`document.querySelectorAll('#chat-host .m-user.pending').length === 1 && !!window.releaseLoadingSend && document.querySelector('#chat-host .m-user.pending .m-meta').textContent.includes('待确认') && getComputedStyle(document.querySelector('#chat-host .m-user.pending .m-meta')).opacity === '1'`), 'Pending input and its confirmation status must be visible before the request is delivered');
      await capture('05-pending-input');
      await js('releaseLoadingSend(); true;');
      await until(() => js(`!document.querySelector('#chat-host .m-user.pending') && !document.querySelector('#chat-host .chat-activity')`), 'optimistic receipt');
      assert.equal(await js(`[...document.querySelectorAll('#chat-host .message-text')].filter(node => node.textContent === 'PERF_PENDING_RECEIPT').length`), 1);
      evidence.loadingChecks.push('pending input is visible before HTTP delivery and merges into one confirmed message');
      await capture('06-confirmed-input');
      await js(`(() => {
        const scroll = document.querySelector('#chat-host .chat-scroll'); scroll.scrollTop = 250;
        const viewport = scroll.getBoundingClientRect();
        const node = [...document.querySelectorAll('#chat-host .chat-event')].find(node => { const r = node.getBoundingClientRect(); return r.top >= viewport.top && r.bottom <= viewport.bottom; });
        if (!node) throw Error('No visible paging anchor');
        window.loadingAnchor = {node, top:node.getBoundingClientRect().top};
      })()`);
      await capture('07-before-earlier');
      await js(`document.querySelector('[data-chat-earlier]').click(); true;`);
      await until(() => js(`document.querySelector('[data-chat-earlier]')?.disabled === false && performance.getEntriesByType('resource').filter(e => new URL(e.name).pathname === '/api/chat/history' && new URL(e.name).searchParams.has('before')).length === 1`), 'earlier page');
      assert(await js('loadingAnchor.node.isConnected && Math.abs(loadingAnchor.node.getBoundingClientRect().top - loadingAnchor.top) <= 2'), 'Prepending history must preserve the visible anchor');
      evidence.loadingChecks.push('earlier page loads via before and preserves the visible anchor within 2 px');
      await capture('08-after-earlier');
      await open('validation-leave', bootstrap.history_sessions[0]);
      fixture.stdin.write('replace-history\n');
      await until(() => fixtureMessages.some(value => value.replaced === bootstrap.history_sessions[1]), 'replacement journal');
      await open('validation-return', bootstrap.history_sessions[1]);
      await until(() => js(`document.querySelector('#chat-host .chat-inner')?.textContent.includes('PERF_REPLACEMENT_JOURNAL')`), 'short journal invalidation');
      assert(await js(`!document.querySelector('#chat-host [data-chat-earlier]') && document.querySelectorAll('#chat-host .m-user').length === 1`));
      evidence.loadingChecks.push('shortened journal invalidates cached records from SSE headers and reloads the tail');
      await capture('09-short-journal-reloaded');
      const errors = await js('perfEvidence.errors'); evidence.errors.push(...errors);
      assert.equal(evidence.errors.length, 0, JSON.stringify(evidence.errors));
      evidence.ok = true;
    } catch (error) {
      // Process boundary: retain error type/message/stack, write evidence, fail.
      evidence.errors.push({name:error.name, message:error.message, stack:error.stack});
      console.error(error);
      process.exitCode = 1;
    } finally {
      if (window) window.destroy();
      if (fixture && fixture.exitCode === null) fixture.stdin.end('shutdown\n');
      if (exited) {
        const code = await exited;
        if (code !== 0) { evidence.errors.push({name:'FixtureExit', message:'exit=' + code}); process.exitCode = 1; }
      }
      await fsp.mkdir(path.dirname(output), {recursive:true});
      await fsp.writeFile(output, JSON.stringify(evidence, null, 2));
      app.exit(process.exitCode || 0);
    }
  });
}
