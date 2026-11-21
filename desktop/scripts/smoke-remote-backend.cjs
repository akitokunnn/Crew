'use strict';
// Real LAN journey: an isolated host Service (real HTTP / SQLite / LAN listener on
// 127.0.0.1, fake AI carriers) and the source Electron client in its own HOME and
// launch-settings folder. No installed Crew, production port or AI model is used.
// Phases: pair (client starts on its own local backend, adds the host and applies),
// remote (the relaunched client works on the host, is revoked and re-pairs) and
// verify (the re-paired client reconnects). Relaunch is recorded, not executed;
// this script starts the next phase with exactly the recorded arguments.
const fs = require('node:fs/promises'), path = require('node:path'), assert = require('node:assert/strict');
const { spawn } = require('node:child_process'), http = require('node:http');
const desktop = path.resolve(__dirname, '..'), root = path.dirname(desktop);
const { defaultPython } = require('../src/python.cjs');
const output = process.env.CREW_REMOTE_BACKEND_OUTPUT || path.join(root, 'Temp', 'remote-backend');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(test, label, timeout = 30000) { const end = Date.now() + timeout; while (Date.now() < end) { const value = await test(); if (value) return value; await pause(80); } throw Error('Timed out: ' + label); }
// Host-side owner actions go to the host's own local listener, as the host user would.
function hostRequest(base, route, body) {
  return new Promise((resolve, reject) => {
    const target = new URL(route, base), payload = body === undefined ? undefined : Buffer.from(JSON.stringify(body));
    const headers = { Origin:target.origin, ...(payload ? { 'X-Crew':'1', 'Content-Type':'application/json', 'Content-Length':payload.length } : {}) };
    const req = http.request(target, { method:payload ? 'POST' : 'GET', headers }, res => { const chunks = []; res.on('data', value => chunks.push(value)); res.on('end', () => { try { resolve({ status:res.statusCode, value:JSON.parse(Buffer.concat(chunks).toString('utf8')) }); } catch (error) { reject(error); } }); });
    req.on('error', reject); req.setTimeout(10000, () => req.destroy(Error('HTTP response timed out'))); req.end(payload);
  });
}

if (!process.versions.electron) {
  (async () => {
    await fs.mkdir(output, { recursive:true });
    await fs.mkdir(path.join(desktop, '.cache'), { recursive:true });
    const work = await fs.mkdtemp(path.join(desktop, '.cache', 'remote-backend-'));
    const clientHome = path.join(work, 'client-home'), settingsDir = path.join(work, 'client-settings'), settingsFile = path.join(settingsDir, 'launch-settings.json');
    const env = { ...process.env, PYTHONIOENCODING:'utf-8' };
    for (const key of ['ELECTRON_RUN_AS_NODE', 'CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_GOAL_ID', 'CREW_EXECUTION_ID']) delete env[key];
    const fixtureSource = String.raw`
import json, os, subprocess, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path('plugin').resolve()))
sys.path.insert(0, str(Path('plugin/tests').resolve()))
from test_remote_integration import RemoteIntegrationTests
case = RemoteIntegrationTests('test_login_protects_pages_business_events_files_and_local_only_management')
case.setUp()
(case.home / 'runtime.json').write_text(json.dumps({'plugin_root': str(Path('plugin').resolve())}), encoding='utf-8')
files = case.root / '主机文件'
for name in ('客户项目', '资料', '.cache', '仓库'):
    (files / name).mkdir(parents=True)
subprocess.run(['git', 'init', '-q', str(files / '仓库')], check=True)
# The folder picker starts at the host user's home; keep it inside this fixture.
home = patch('pathlib.Path.home', return_value=files)
home.start()
case.addCleanup(home.stop)
case.server.service.version = '9.9.9-host'
case.start_remote()
try:
    with patch('crew.codex_runtime._ObservedClient', side_effect=AssertionError('This smoke must not launch a model')):
        print(json.dumps({'url': case.url, 'remote': case.remote_url, 'home': str(case.home), 'files': str(files), 'pid': os.getpid()}), flush=True)
        for line in sys.stdin:
            command = json.loads(line)
            if command['action'] == 'audit':
                print(json.dumps({'audit': True, 'process_count': len(case.processes)}), flush=True)
            elif command['action'] == 'shutdown':
                break
finally:
    case.doCleanups()
    print(json.dumps({'closed': True}), flush=True)
`;
    let fixture, info, buffer = '', fixtureError = '';
    const records = [], phases = [];
    try {
      fixture = spawn(defaultPython(), ['-u', '-c', fixtureSource], { cwd:root, env, windowsHide:true, stdio:['pipe', 'pipe', 'pipe'] });
      fixture.stderr.on('data', data => { fixtureError += data; process.stderr.write(data); });
      fixture.stdout.on('data', data => { buffer += data; let end; while ((end = buffer.indexOf('\n')) >= 0) { const line = buffer.slice(0, end); buffer = buffer.slice(end + 1); try { const item = JSON.parse(line); records.push(item); if (item.url) info = item; } catch { /* Python diagnostics */ } } });
      await until(() => { if (fixture.exitCode !== null) throw Error('Fixture exited: ' + fixtureError); return info; }, 'isolated host Service');
      const runPhase = async (phase, args) => {
        const childEnv = { ...env, CREW_REMOTE_BACKEND_OUTPUT:output, CREW_REMOTE_BACKEND_PHASE:phase, CREW_HOST_URL:info.url, CREW_HOST_REMOTE:info.remote,
          CREW_HOST_FILES:info.files, CREW_CLIENT_SETTINGS:settingsFile };
        const electron = spawn(require('electron'), args, { cwd:desktop, env:childEnv, windowsHide:false, stdio:['ignore', 'pipe', 'pipe'] });
        let text = '';
        electron.stdout.on('data', data => { text += data; process.stdout.write(data); }); electron.stderr.on('data', data => { text += data; process.stderr.write(data); });
        const exit = await new Promise((resolve, reject) => { const timer = setTimeout(() => { electron.kill(); reject(Error('Electron phase timed out: ' + phase)); }, 240000); electron.once('error', error => { clearTimeout(timer); reject(error); }); electron.once('exit', code => { clearTimeout(timer); resolve(code); }); });
        const evidence = JSON.parse(await fs.readFile(path.join(output, 'evidence-' + phase + '.json'), 'utf8'));
        phases.push({ phase, exit, ok:evidence.ok, checks:evidence.checks.length, errors:evidence.errors });
        await fs.writeFile(path.join(output, 'output-' + phase + '.log'), text);
        assert.equal(exit, 0, 'Electron phase failed: ' + phase); assert.equal(evidence.ok, true, phase); assert.deepEqual(evidence.errors, [], phase);
        return evidence;
      };
      const paired = await runPhase('pair', ['--require', __filename, desktop, '--isolated-sources', '--home', clientHome, '--launch-settings', settingsFile]);
      const remote = await runPhase('remote', paired.relaunch.args);
      await runPhase('verify', remote.relaunch.args);
      fixture.stdin.write(JSON.stringify({ action:'audit' }) + '\n');
      const audit = await until(() => records.find(item => item.audit), 'no-model audit');
      assert.equal(audit.process_count, 0, 'No AI carrier may be started by this journey');
      await assert.rejects(fs.access(path.join(clientHome, 'web.json')), { code:'ENOENT' }, 'the client local backend must have stopped');
      // The client HOME and settings are only kept when a phase fails, for diagnosis.
      await fs.rm(work, { recursive:true, force:true });
      const summary = { ok:true, phases, noModelProcesses:true, output };
      await fs.writeFile(path.join(output, 'summary.json'), JSON.stringify(summary, null, 2));
      console.log(JSON.stringify(summary));
    } finally {
      if (fixture && fixture.exitCode === null) {
        fixture.stdin.write(JSON.stringify({ action:'shutdown' }) + '\n');
        await new Promise((resolve, reject) => { const timer = setTimeout(() => { fixture.kill(); reject(Error('Fixture did not shut down gracefully')); }, 10000); fixture.once('exit', () => { clearTimeout(timer); resolve(); }); });
      }
    }
  })().catch(error => { console.error(error); process.exitCode = 1; });
} else {
  const { app, dialog, nativeTheme, webContents } = require('electron');
  const phase = process.env.CREW_REMOTE_BACKEND_PHASE, hostUrl = process.env.CREW_HOST_URL, hostRemote = process.env.CREW_HOST_REMOTE;
  const evidence = { phase, checks:[], errors:[], screenshots:[], dialogs:[], scope:'Source Electron main/preload/home and host pages; isolated host Service on 127.0.0.1 with fake carriers; no AI.' };
  // Native dialogs are answered by the journey: save into the evidence folder, accept history upgrades.
  dialog.showSaveDialog = async (_window, options) => { evidence.dialogs.push({ type:'save', title:options.title }); return { canceled:false, filePath:path.join(output, 'exported-demo.crewpack') }; };
  dialog.showMessageBox = async (_window, options) => {
    evidence.dialogs.push({ type:'message', title:options?.title, message:options?.message });
    if (options?.type === 'error') evidence.errors.push('error dialog: ' + options.message + ' ' + options.detail);
    return { response:0 };
  };
  app.relaunch = options => { evidence.relaunch = { args:options.args }; };
  // An uncaught main-process error must fail the journey with its stack, not block it behind a native error box.
  for (const event of ['uncaughtException', 'unhandledRejection']) process.on(event, error => {
    evidence.errors.push(event + ': ' + (error?.stack || String(error)));
    require('node:fs').appendFileSync(path.join(output, 'progress-' + phase + '.log'), new Date().toISOString() + ' ' + event + ' ' + (error?.stack || String(error)) + '\n');
  });
  // Quitting waits until this phase's evidence is written.
  let evidenceWritten; const written = new Promise(resolve => { evidenceWritten = resolve; }), quit = app.quit.bind(app);
  app.quit = () => { void written.then(quit); };
  app.once('browser-window-created', (_event, win) => {
    const main = win.webContents;
    app.on('web-contents-created', (_event, contents) => contents.on('console-message', event => { if (event.level === 'error') evidence.errors.push(contents.getURL() + ' ' + event.message); }));
    main.on('console-message', event => { if (event.level === 'error') evidence.errors.push('home ' + event.message); });
    main.once('did-finish-load', async () => {
      // A page that stops answering fails the journey instead of hanging it.
      const js = (source, contents = main) => { let timer; return Promise.race([contents.executeJavaScript(source, true), new Promise((_resolve, reject) => { timer = setTimeout(() => reject(Error('Page did not answer: ' + source.slice(0, 120))), 15000); })]).finally(() => clearTimeout(timer)); };
      // Progress is appended synchronously so a hung phase still shows its last step.
      const log = text => require('node:fs').appendFileSync(path.join(output, 'progress-' + phase + '.log'), new Date().toISOString() + ' ' + text + '\n');
      const step = label => log('… ' + label);
      const wait = (source, label, contents = main, timeout) => { step(label); return until(() => js(source, contents), label, timeout); };
      const click = async (selector, contents = main) => { await wait(`(()=>{const node=document.querySelector(${JSON.stringify(selector)});return Boolean(node&&!node.disabled&&node.getClientRects().length);})()`, 'clickable ' + selector, contents); await js(`document.querySelector(${JSON.stringify(selector)}).click()`, contents); };
      const fill = (selector, value) => js(`(()=>{const node=document.querySelector(${JSON.stringify(selector)});node.focus();node.value=${JSON.stringify(value)};node.dispatchEvent(new Event('input',{bubbles:true}));node.dispatchEvent(new Event('change',{bubbles:true}));})()`);
      const key = (keyCode, contents = main) => { contents.sendInputEvent({ type:'keyDown', keyCode }); contents.sendInputEvent({ type:'keyUp', keyCode }); };
      const check = (label, ok) => { assert(ok, label); evidence.checks.push(label); log('✓ ' + label); };
      const tabContents = name => webContents.getAllWebContents().find(contents => contents.getURL().startsWith(hostRemote + '/?project=' + encodeURIComponent(name)));
      async function settle() {
        for (const contents of webContents.getAllWebContents().filter(item => !item.isDestroyed() && /^(file|http):/.test(item.getURL())))
          await js('(async()=>{await document.fonts.ready;for(const a of document.getAnimations()){if(a.effect?.getTiming().iterations!==Infinity)a.finish();}if(document.visibilityState==="visible")await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));})()', contents);
        await pause(450);
      }
      // The whole native window, child workspace views and title bar included, as the OS composes it.
      // A native child view sometimes has no compositor surface for capturePage; DevTools then asks
      // that renderer for its own frame (the same fallback the desktop visual smoke uses).
      async function capture(contents) {
        try { const image = await contents.capturePage(); if (!image.isEmpty()) return image.toDataURL(); }
        catch (error) { log('capturePage fallback: ' + error.message); }
        contents.debugger.attach('1.3');
        try { return 'data:image/png;base64,' + (await contents.debugger.sendCommand('Page.captureScreenshot', { format:'png', fromSurface:true })).data; }
        finally { contents.debugger.detach(); }
      }
      async function shot(name) {
        log('screenshot ' + name);
        await settle();
        // The shell page plus the visible workspace view, composed where the window shows it.
        // Only this application's own pages are captured, never other windows on the desktop.
        const base = await capture(main);
        const area = await js('(()=>{const node=document.getElementById("crew-workspace-view-area");if(!node?.classList.contains("crew-view-active"))return null;const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};})()');
        let layer = null;
        if (area) for (const contents of webContents.getAllWebContents().filter(item => !item.isDestroyed() && item.getURL().startsWith(hostRemote + '/')))
          if (await js('window.crewTabVisible===true', contents)) layer = await capture(contents);
        const data = await js(`(async()=>{const load=src=>new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=reject;image.src=src;});
          const canvas=document.createElement('canvas');canvas.width=innerWidth;canvas.height=innerHeight;const g=canvas.getContext('2d');
          g.drawImage(await load(${JSON.stringify(base)}),0,0,innerWidth,innerHeight);
          const area=${JSON.stringify(area)},layer=${JSON.stringify(layer)};if(area&&layer)g.drawImage(await load(layer),area.x,area.y,area.width,area.height);
          return canvas.toDataURL('image/png');})()`);
        const file = path.join(output, name + '.png'); await fs.writeFile(file, Buffer.from(data.split(',')[1], 'base64')); evidence.screenshots.push(file);
      }
      async function size(width) {
        win.setContentSize(width, 850);
        // Display scaling (200% on one Windows host) can round the content width by one pixel.
        try { await until(() => js(`Math.abs(innerWidth-${width})<=1`), 'window width ' + width, 10000); }
        catch (error) { throw Error(error.message + ' (content ' + win.getContentSize() + ', inner ' + await js('innerWidth+"x"+innerHeight+" dpr "+devicePixelRatio') + ', maximized ' + win.isMaximized() + ')'); }
      }
      async function shots(name) {
        for (const theme of ['light', 'dark']) for (const width of [1220, 760]) {
          nativeTheme.themeSource = theme; await size(width); await shot(`${phase}-${name}-${theme}-${width}`);
        }
        nativeTheme.themeSource = 'light'; await size(1220);
      }
      try {
        await fs.mkdir(output, { recursive:true });
        // A pulse shows whether the main process stays responsive; a stalled phase still leaves its evidence.
        const pulse = setInterval(() => log('pulse'), 10000); pulse.unref();
        setTimeout(() => { log('watchdog: phase still running after 200 s'); evidence.errors.push('watchdog: phase still running after 200 s'); require('node:fs').writeFileSync(path.join(output, 'evidence-' + phase + '.json'), JSON.stringify(evidence, null, 2)); app.exit(1); }, 200000).unref();
        win.setMinimumSize(700, 560); win.show(); nativeTheme.themeSource = 'light'; await size(1220);
        if (phase === 'pair') {
          await wait('document.querySelector("#service")?.textContent==="已连接"', 'client local backend');
          check('The client first runs on its own isolated local backend', true);
          await click('#nav-settings'); await click('[data-settings-category="backend"]'); await click('[data-operations-tab="launch"]');
          await click('input[data-launch-field="mode"][value="remote"]');
          await wait('Boolean(document.querySelector(".cl-remote .cl-inline-hint"))', 'empty LAN device list');
          await click('[data-launch-action="pair-open"]');
          const pair = await hostRequest(hostUrl, '/api/remote-access', { action:'pair' }); assert.equal(pair.status, 200);
          await fill('[data-pair-field="url"]', hostRemote.replace('http://', '')); await fill('[data-pair-field="code"]', pair.value.code);
          check('Device name defaults to this computer name', await js('document.querySelector("[data-pair-field=device_name]").value.length>0'));
          await shots('add-device');
          await click('[data-launch-action="pair-submit"]');
          await wait('document.querySelector("[data-launch-remote]")?.checked===true', 'saved device selected');
          const saved = await fs.readFile(path.join(path.dirname(process.env.CREW_CLIENT_SETTINGS), 'remote-backends.json'), 'utf8'), [record] = JSON.parse(saved).backends;
          check('The saved device file holds the host origin and an OS-encrypted token, never the token text', record.url === hostRemote && !/^[A-Za-z0-9_-]{43}$/.test(record.token) && Buffer.from(record.token, 'base64').length > 43);
          const devices = (await hostRequest(hostUrl, '/api/remote-access')).value.devices;
          check('The host lists the client as a Crew desktop client', devices.length === 1 && devices[0].kind === 'desktop');
          await click('[data-launch-action="check"]');
          await wait('Boolean(document.querySelector(".cl-confirm"))', 'apply preview');
          check('Preview names the host and warns about the version difference', await js('/9\\.9\\.9-host/.test(document.querySelector(".cl-confirm").textContent)&&/不一致/.test(document.querySelector(".cl-confirm").textContent)'));
          await shots('apply-preview');
          await click('.cl-confirm [data-launch-action="apply"]');
          await until(() => evidence.relaunch, 'relaunch after applying the LAN host', 120000);
          const settings = JSON.parse(await fs.readFile(process.env.CREW_CLIENT_SETTINGS, 'utf8'));
          check('Launch settings now select the saved LAN host', settings.mode === 'remote' && settings.remote_backend === JSON.parse(saved).backends[0].id);
        }
        if (phase === 'remote') {
          await wait('document.querySelector("#service")?.textContent.startsWith("已连接 · ")', 'connected to the LAN host');
          await wait('[...document.querySelectorAll(".project strong")].some(node=>node.textContent==="demo")', 'host workspaces listed');
          check('Version banner names the host and both versions', await js('/已连接到 .*（版本 9\\.9\\.9-host），本机版本 .*部分功能可能不一致/.test(document.querySelector("#remote-banner").textContent)'));
          check('Host-only entries are absent', await js('document.querySelector("#import-workspace").hidden&&document.querySelector("#nav-tools").hidden&&!document.querySelector("#nav-admin").hidden'));
          await shots('home');
          // New workspace on the host through the in-page folder picker, driven by keyboard.
          await click('#new'); await fill('#name', '远程新建'); await click('#choose-parent');
          await wait('document.querySelectorAll(".host-folders .hf-entry").length===3', 'host folders listed');
          check('Hidden folders stay out of the list and Git folders are marked', await js('[...document.querySelectorAll(".hf-entry")].map(n=>n.textContent).join("|")==="仓库Git|客户项目|资料"'));
          await shots('host-folders');
          await js('document.querySelector(".hf-list").focus()');
          for (let index = 0; index < 2; index++) key('Down');
          await wait('document.querySelector(".hf-entry[aria-selected=true] .hf-name")?.textContent==="客户项目"', 'keyboard selection');
          key('Return');
          await wait('document.querySelector(".hf-path").textContent.endsWith("客户项目")&&!document.querySelector(".hf-entry")', 'opened empty folder');
          await shots('host-folders-empty');
          await click('.hf-choose');
          await wait('document.querySelector("#location").textContent.endsWith("客户项目")', 'host parent chosen');
          await click('#create');
          await until(() => tabContents('远程新建'), 'new workspace tab');
          await fs.stat(path.join(process.env.CREW_HOST_FILES, '客户项目', '远程新建'));
          check('The new workspace folder was created on the host, under the folder chosen in the picker', true);
          // A workspace chat page from the host, opened in a tab; no message is sent.
          await click('.crew-chrome-brand'); await wait('!document.querySelector("#workspaces-page").hidden&&!document.querySelector("#refresh").disabled', 'home idle again');
          await js('[...document.querySelectorAll(".project")].find(n=>n.querySelector("strong").textContent==="demo").click()');
          const tab = await until(() => tabContents('demo'), 'demo workspace tab', 60000);
          await wait('Boolean(document.querySelector("#sidebar [data-act=presence-toggle]"))', 'workspace page ready', tab, 60000);
          await js('location.hash="#leader=s3"', tab);
          await wait('Boolean(document.querySelector("textarea"))', 'leader chat composer', tab);
          check('Workspace page knows it runs in a Crew desktop client', await js('document.documentElement.dataset.accessClient==="desktop"&&document.documentElement.dataset.accessLocalControls==="true"', tab));
          await shots('workspace-chat');
          // Adopting native sessions reads the host's session library, which a browser device cannot reach.
          await js('location.hash="#adopt"', tab);
          await wait('document.querySelector("#main .title")?.textContent==="会话库"&&!document.querySelector("#main [aria-busy=true]")', 'session library', tab);
          check('The host session library for adopting native sessions opens in the desktop client', await js('getComputedStyle(document.querySelector("#main")).display!=="none"', tab));
          await js('location.hash="#leader=s3"', tab); await wait('Boolean(document.querySelector("textarea"))', 'leader chat again', tab);
          await js('document.querySelector("#sidebar [data-act=presence-toggle]").click()', tab);
          await js('document.querySelector("[data-act=connection-settings]").click()', tab);
          await wait('Boolean(document.querySelector("[data-settings-category=backend]"))', 'workspace settings', tab);
          check('Workspace settings offer no LAN access card', await js('!document.querySelector("[data-settings-category=remote]")', tab));
          await js('document.querySelector("[data-settings-category=backend]").click()', tab);
          await wait('/所连主机的后台/.test(document.querySelector(".cs-content").textContent)', 'host backend card', tab);
          check('Workspace settings offer no operations chat', await js('!document.querySelector(".co-tabs")', tab));
          await shots('workspace-settings');
          await js('document.querySelector("[data-settings-action=close]").click()', tab);
          // Home settings and the read-only administrator.
          await click('.crew-chrome-brand'); await wait('!document.querySelector("#workspaces-page").hidden&&!document.querySelector("#refresh").disabled', 'home idle again');
          await click('#nav-settings'); await click('[data-settings-category="ai"]');
          await wait('Boolean(document.querySelector("[data-ai-key=default][data-ai-field=runtime]"))', 'host AI settings through HTTP');
          check('Home settings read the host settings and hide the LAN access card', await js('!document.querySelector("[data-settings-category=remote]")'));
          await click('[data-settings-category="backend"]');
          await wait('Boolean(document.querySelector("[data-operations-tab=status]"))', 'host status tab');
          check('Operations chat and repair policy stay on the host', await js('!document.querySelector("[data-operations-tab=chat]")&&!document.querySelector("[data-operations-tab=config]")'));
          await wait('/运行正常/.test(document.querySelector("#admin-state")?.textContent||"")', 'host health inside settings');
          await shots('settings-backend');
          await click('[data-operations-tab="launch"]'); await wait('document.querySelector("[data-launch-remote]")?.checked===true', 'current device selected');
          await shots('settings-launch');
          await click('#nav-workspaces'); await click('#nav-admin');
          await wait('/运行正常/.test(document.querySelector("#admin-state").textContent)', 'read-only host health');
          check('The administrator only reads host health', await js('document.querySelector(".admin-actions").hidden&&document.querySelector("#admin-conversation").hidden&&/9\\.9\\.9-host/.test(document.querySelector("#admin-recoveries").textContent)'));
          await shots('admin');
          // Export downloads the host package to this computer.
          await click('#nav-workspaces'); await wait('!document.querySelector("#refresh").disabled', 'home idle before export');
          await js('[...document.querySelectorAll(".project-row")].find(r=>r.querySelector("strong")?.textContent==="demo").querySelector(".project-action").click()');
          const exported = path.join(output, 'exported-demo.crewpack');
          await until(() => evidence.dialogs.some(item => item.title === '工作区已导出'), 'downloaded package', 60000);
          const header = (await fs.readFile(exported)).subarray(0, 2).toString('latin1');
          check('The exported .crewpack was downloaded to this computer', header === 'PK');
          // The host forces this device offline.
          const device = (await hostRequest(hostUrl, '/api/remote-access')).value.devices.find(item => item.kind === 'desktop');
          assert.equal((await hostRequest(hostUrl, '/api/remote-access', { action:'revoke', device_id:device.id })).status, 200);
          await wait('document.querySelector("#remote-banner")?.dataset.tone==="warning"', 'authorization expired banner', main, 45000);
          check('Revocation shows the expired authorization with re-pair and switch-back actions', await js('/授权已失效，请在主机上重新生成配对码/.test(document.querySelector("#remote-banner").textContent)&&[...document.querySelectorAll("#remote-banner button")].map(b=>b.textContent).join("|")==="重新配对|切回本机"'));
          await shots('auth-expired');
          await click('#remote-banner .primary');
          await wait('Boolean(document.querySelector(".remote-repair input[name=code]"))', 're-pair form');
          await shots('repair-form');
          const pair = await hostRequest(hostUrl, '/api/remote-access', { action:'pair' });
          await fill('.remote-repair input[name=code]', pair.value.code);
          await js('document.querySelector(".remote-repair").requestSubmit()');
          await until(() => evidence.relaunch, 'relaunch after re-pairing', 60000);
          check('Re-pairing replaced the device on the host', (await hostRequest(hostUrl, '/api/remote-access')).value.devices.filter(item => item.kind === 'desktop').length === 1);
        }
        if (phase === 'verify') {
          await wait('document.querySelector("#service")?.textContent.startsWith("已连接 · ")', 'reconnected after re-pairing');
          await wait('["demo","远程新建"].every(name=>[...document.querySelectorAll(".project strong")].some(node=>node.textContent===name))', 'both host workspaces listed');
          check('The re-paired client reconnects and lists the host workspaces', true);
          await shot('verify-home-light-1220');
          app.quit();
        }
        evidence.ok = true;
      } catch (error) { evidence.errors.push(error.stack || String(error)); try { await shot(phase + '-failure'); } catch (caught) { evidence.errors.push(String(caught)); } console.error(error); app.quit(); }
      await fs.writeFile(path.join(output, 'evidence-' + phase + '.json'), JSON.stringify(evidence, null, 2));
      evidenceWritten();
    });
  });
}
