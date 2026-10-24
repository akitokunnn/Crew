'use strict';
// Test-only hook loaded by smoke-workspaces.cjs through Electron's real app entry.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { app, ipcMain, dialog, Menu, webContents } = require('electron');
const desktop = path.resolve(__dirname, '..');
const core = require(path.join(desktop, 'src/core.cjs'));
const { ToolUpdates } = require(path.join(desktop, 'src/tool-updates.cjs'));
const output = path.join(desktop, 'smoke-output/workspaces');
const evidence = { checks:[], errors:[], screenshots:[], screenshotMethods:[], modelTurnsStarted:0, directoryPicker:'test stub returning isolated folders', toolUpdates:'real controller and IPC; version/installer helper results are simulated; no CLI is updated' };
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const toolFixture = { checks:0, attempts:{}, checkFailure:false, current:false, unsupported:false };
const toolVersions = () => ['claude', 'codex'].map(id => ({ id, installed_version:toolFixture.current ? '9.9.2' : '9.9.1', latest_version:'9.9.2', installation:'npm', update_available:!toolFixture.current, can_update:id !== 'claude', can_queue:true, running_count:id === 'claude' ? 2 : 0, blocked_reason:id === 'claude' ? '2 个会话正在运行，等待空闲后更新' : null, error:null, manual_update:null,
  ...(toolFixture.unsupported && id === 'codex' ? {installation:'unknown', can_update:false, can_queue:false, update_available:true, blocked_reason:'安装来源未识别，请使用原安装方式更新', manual_update:'请运行原来的安装器更新 Codex'} : {}) }));
ToolUpdates.prototype._helper = async function(operation, id) {
  await pause(300);
  if (operation === 'check') {
    toolFixture.checks++;
    if (toolFixture.checkFailure) throw new Error('模拟：版本源暂时不可用');
    return { operation, ok:true, checked_at:new Date().toISOString(), runtimes:toolVersions() };
  }
  toolFixture.attempts[id] = (toolFixture.attempts[id] || 0) + 1;
  const before = toolVersions().find(tool => tool.id === id);
  if (id === 'claude') return {operation, runtime:id, ok:false, status:'blocked', before, running_count:2, blocked_reason:'2 个会话正在运行，等待空闲后更新'};
  if (toolFixture.attempts[id] === 1) return {operation, runtime:id, ok:false, status:'failed', before, error:'模拟：安装器暂时不可用'};
  return {operation, runtime:id, ok:true, status:'updated', before, after:{...before, installed_version:before.latest_version, update_available:false}};
};
async function bounded(promise, label) {
  let timer;
  try { return await Promise.race([promise, new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error('Timed out: ' + label)), 8000); })]); }
  finally { clearTimeout(timer); }
}

function seedLeaders(home, projects) {
  // board.join and empty journals only: no notice, message, working status or launcher.
  const source = `import json,sys\nfrom pathlib import Path\nfrom contextlib import closing\nsys.path.insert(0,sys.argv[1])\nfrom crew import board,store\nhome=Path(sys.argv[2])\nwith closing(store.connect(home)) as conn:\n for i,p in enumerate(json.loads(sys.argv[3])):\n  sid='tab-qa-leader-'+str(i)\n  board.join(conn,sid,p['name'],'标签验收 '+str(i+1),p['root'],p['root'],'main',role='leader')\n  (home/'chat').mkdir(exist_ok=True)\n  (home/'chat'/(sid+'.jsonl')).touch()\n`;
  const result = spawnSync('python', ['-', path.join(desktop, '../plugin'), home, JSON.stringify(projects)], { input:source, encoding:'utf8', windowsHide:true });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
}

app.once('browser-window-created', (_event, win) => {
  const main = win.webContents;
  main.setBackgroundThrottling(false);
  ipcMain.removeHandler('desktop:ready'); ipcMain.handle('desktop:ready', () => {});
  const observe = contents => {
    contents.setBackgroundThrottling(false);
    contents.on('console-message', event => { if (event.level === 'error') evidence.errors.push(event.message); });
  };
  observe(main);
  app.on('web-contents-created', (_event, contents) => observe(contents));
  main.once('did-finish-load', async () => {
    const js = source => main.executeJavaScript(source, true);
    async function until(callback, label) {
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline) { if (await callback()) return; await pause(40); }
      throw new Error('Timed out: ' + label);
    }
    const wait = (source, label) => until(() => js(source), label);
    const click = selector => js(`document.querySelector(${JSON.stringify(selector)}).click()`);
    async function check(label, source) { assert(await js(source), label); evidence.checks.push(label); }
    async function capture(name, contents = main) {
      await contents.executeJavaScript('Promise.race([document.fonts.ready.then(()=>true),new Promise(resolve=>setTimeout(resolve,2000))])');
      await pause(400);
      const file = path.join(output, name + '.png');
      let bytes, method = 'capturePage', debug = false;
      if (contents === main) await main.executeJavaScript('Promise.race([new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))),new Promise(resolve=>setTimeout(resolve,1000))])');
      try { bytes = (await bounded(contents.capturePage(undefined, { stayHidden:true }), 'capturePage')).toPNG(); }
      catch (error) { if (contents === main) throw error; debug = true; }
      if (debug) {
        if (contents.debugger.isAttached()) throw new Error('Cannot capture while a separate debugger is attached');
        contents.debugger.attach('1.3');
        try {
          const result = await bounded(contents.debugger.sendCommand('Page.captureScreenshot', { format:'png', fromSurface:true, captureBeyondViewport:false }), 'hidden project screenshot');
          bytes = Buffer.from(result.data, 'base64'); method = 'Page.captureScreenshot (actual hidden ' + (contents === main ? 'shell' : 'project') + ' renderer)';
        } finally { contents.debugger.detach(); }
      }
      await fs.writeFile(file, bytes); evidence.screenshots.push(file); evidence.screenshotMethods.push({ file, method });
    }
    function projectContents(name) {
      return webContents.getAllWebContents().find(contents => {
        try { const url = new URL(contents.getURL()); return url.origin === evidence.service.url && url.searchParams.get('project') === name; }
        catch (_) { return false; }
      });
    }
    async function opened(name) {
      await until(() => Boolean(projectContents(name)), 'workspace view ' + name);
      const contents = projectContents(name);
      await until(() => contents.executeJavaScript('Boolean(document.querySelector("#sidebar"))'), 'workspace renderer ' + name);
      return contents;
    }
    const tabs = () => js('[...document.querySelectorAll("#crew-desktop-tabs [role=tab][data-tab-id]")].map(node=>({id:node.dataset.tabId,project:node.dataset.project,text:node.textContent,selected:node.getAttribute("aria-selected")==="true"}))');
    async function tabFor(name) {
      const tab = (await tabs()).find(tab => tab.project === name);
      assert(tab, 'Tab exists for ' + name); return tab;
    }
    async function select(id) {
      await click(`#crew-desktop-tabs [role=tab][data-tab-id="${id}"]`);
      await until(async () => (await tabs()).some(tab => tab.id === id && tab.selected), 'selected tab ' + id);
    }
    async function home() {
      await select('home');
      await wait('document.querySelector("#projects").getAttribute("aria-busy")==="false" && !document.querySelector("#refresh").disabled', 'home ready');
    }
    async function key(contents, keyCode, modifiers) {
      contents.focus();
      contents.sendInputEvent({ type:'keyDown', keyCode, modifiers });
      contents.sendInputEvent({ type:'keyUp', keyCode, modifiers });
      await pause(100);
    }
    let originalDialog;
    try {
      await fs.mkdir(output, { recursive:true });
      const initialUrl = main.getURL();
      await wait('document.querySelector("#service").textContent==="已连接" && document.querySelector("#crew-desktop-tabs [role=tab]")', 'home bootstrap and tab strip');
      const state = await js('window.crewDesktop.bootstrap()'); evidence.service = state.service;
      assert.notEqual(new URL(state.service.url).port, '47811');
      const homeDirectory = process.argv[process.argv.indexOf('--home') + 1]; evidence.home = homeDirectory;
      const parent = path.join(homeDirectory, 'qa-workspaces'); await fs.mkdir(parent, { recursive:true });
      for (const name of ['Crew 视觉验收', '长路径与中文名称的工作区', '工具接入']) await core.newWorkspace(parent, name, state.service.url);
      const projects = await core.projects(state.service.url); seedLeaders(homeDirectory, projects.slice(0, 2));
      await click('#refresh'); await wait('document.querySelectorAll(".project").length===3', 'workspace list');
      await check('real workspace list shows name, path, count and neutral folder icons', 'document.querySelector("#project-count").textContent==="3" && document.querySelectorAll(".project-avatar svg").length===3 && document.querySelector(".project small").textContent.includes("qa-workspaces")');
      await check('home fits the desktop shell without horizontal overflow', 'document.documentElement.scrollWidth<=innerWidth && document.querySelector("main").getBoundingClientRect().right<=innerWidth');
      await capture('01-workspaces');
      originalDialog = dialog.showOpenDialog;
      dialog.showOpenDialog = async () => ({ canceled:false, filePaths:[projects[0].root] });
      await click('#existing'); const first = await opened(projects[0].name);
      assert.equal(main.getURL(), initialUrl);
      evidence.checks.push('open existing folder uses directory IPC and opens a real project view while home remains mounted');
      await home(); await click('#new');
      await check('new form focuses name and requires a nonempty name', 'document.activeElement.id==="name" && !document.querySelector("#create-form").checkValidity()');
      dialog.showOpenDialog = async () => ({ canceled:false, filePaths:[parent] });
      await click('#choose-parent'); await wait('document.querySelector("#location").textContent.includes("qa-workspaces")', 'choose parent');
      await js('document.querySelector("#name").value="新工作区验证"'); await capture('02-new-workspace');
      await js('document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}))');
      await check('Escape closes creation and returns focus', 'document.querySelector("#create-panel").hidden && document.activeElement.id==="new"');
      win.setSize(win.getMinimumSize()[0], 700); await capture('03-narrow-workspaces');
      await check('minimum-size layout keeps actions and workspace list inside content', 'document.documentElement.scrollWidth<=innerWidth && document.querySelector(".actions").getBoundingClientRect().right<=innerWidth');
      win.setSize(1220, 850); await click('#new'); await js('document.querySelector("#create-form").requestSubmit()');
      const created = await opened('新工作区验证'); await fs.access(path.join(parent, '新工作区验证', '.git'));
      await wait('document.querySelectorAll(".project").length===4 && document.querySelector("#create-panel").hidden', 'created project refreshes persistent home');
      evidence.checks.push('creation creates a real isolated Git repository, opens its tab and refreshes the persistent home');
      const menu = Menu.getApplicationMenu();
      menu.getMenuItemById('workspace-new').click();
      await wait('!document.querySelector("#create-panel").hidden && document.activeElement.id==="name"', 'native new action');
      await check('native New from a project focuses a cleared form on persistent home', 'document.querySelector("#name").value==="" && document.querySelector("#location").textContent==="尚未选择文件夹"');
      await capture('04-menu-new');
      await js('document.querySelector("#name").value="不应复用旧位置";document.querySelector("#create-form").requestSubmit()');
      await wait('!document.querySelector("#error").hidden', 'missing location rejects creation');
      await check('native New cannot silently reuse the previous parent directory', 'document.querySelector("#error").textContent.includes("请先选择")');
      await js('document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}))');
      await click('#refresh'); await wait('!document.querySelector("#refresh").disabled', 'manual refresh');
      await check('manual refresh does not replay the consumed New action', 'document.querySelector("#create-panel").hidden');
      await select((await tabFor(projects[0].name)).id);
      let openDialogs = 0;
      dialog.showOpenDialog = async () => { openDialogs++; return { canceled:false, filePaths:[path.join(parent, '新工作区验证')] }; };
      const beforeOpen = (await tabs()).length;
      menu.getMenuItemById('workspace-open').click();
      await until(async () => openDialogs === 1 && (await tabs()).some(tab => tab.project === '新工作区验证' && tab.selected), 'native Open chooses existing tab');
      assert.equal((await tabs()).length, beforeOpen); assert.equal(projectContents('新工作区验证').id, created.id);
      evidence.checks.push('native Open uses one picker and deduplicates an already open project');

      // Actual leader/chat UI: different project, selected leader and unsent draft in each document.
      await home(); await js(`window.crewDesktop.openProject(${JSON.stringify(projects[1].name)})`);
      const second = await opened(projects[1].name);
      const live = [first, second], identities = [];
      for (let index = 0; index < live.length; index++) {
        const contents = live[index], sid = 'tab-qa-leader-' + index;
        await select((await tabFor(projects[index].name)).id);
        await until(() => contents.executeJavaScript(`Boolean(document.querySelector('[data-act="leader"][data-id="${sid}"]'))`), 'fixture leader in project');
        await contents.executeJavaScript(`document.querySelector('[data-act="leader"][data-id="${sid}"]').click()`);
        await until(() => contents.executeJavaScript('Boolean(document.querySelector(".composer textarea"))'), 'leader composer');
        identities.push(await contents.executeJavaScript(`(() => { const input=document.querySelector('.composer textarea'); input.value=${JSON.stringify('仅属项目 ')}+${index}; input.dispatchEvent(new Event('input',{bubbles:true})); window.__tabQaToken='project-'+${index}; return {origin:performance.timeOrigin,hash:location.hash}; })()`));
        assert(await contents.executeJavaScript('typeof require==="undefined" && typeof process==="undefined" && typeof window.crewDesktop==="undefined"'), 'project has no filesystem bridge or Node');
        const preferences = contents.getLastWebPreferences();
        assert(!preferences.preload); assert.equal(preferences.nodeIntegration, false); assert.equal(preferences.sandbox, true); assert.equal(preferences.contextIsolation, true); assert.equal(preferences.webSecurity, true);
      }
      const firstTab = await tabFor(projects[0].name), secondTab = await tabFor(projects[1].name);
      await home();
      await capture('05-two-project-tabs');
      await select(secondTab.id);
      await capture('06-second-project-chat', second);
      for (let index = 0; index < live.length; index++) {
        const contents = live[index]; await select((await tabFor(projects[index].name)).id);
        const preserved = await contents.executeJavaScript('({text:document.querySelector(".composer textarea")?.value,leader:document.querySelector("#chat-head .title")?.textContent,token:window.__tabQaToken,origin:performance.timeOrigin,hash:location.hash})');
        assert.equal(preserved.text, '仅属项目 ' + index); assert(preserved.leader.includes('标签验收 ' + (index + 1)));
        assert.equal(preserved.token, 'project-' + index); assert.equal(preserved.origin, identities[index].origin); assert.equal(preserved.hash, identities[index].hash);
      }
      evidence.checks.push('two real projects retain their own selected leader and different unsent chat drafts after switching');
      evidence.checks.push('each project keeps the same renderer and timeOrigin, proving switching does not reload');
      evidence.checks.push('project renderers have neither Node nor the privileged home filesystem bridge');
      await capture('07-first-project-chat', first);
      const count = (await tabs()).length;
      await home(); await js(`window.crewDesktop.openProject(${JSON.stringify(projects[0].name)})`);
      assert.equal((await tabs()).length, count); assert.equal(projectContents(projects[0].name).id, first.id);
      evidence.checks.push('opening an existing workspace selects its original renderer without creating another tab');
      const order = await tabs(), next = order[(order.findIndex(tab => tab.id === firstTab.id) + 1) % order.length];
      await key(first, 'Tab', ['control']);
      await until(async () => (await tabs()).some(tab => tab.id === next.id && tab.selected), 'CtrlTab next project');
      const nextContents = next.id === 'home' ? main : projectContents(next.project);
      assert(nextContents, 'Next tab has its own renderer');
      await key(nextContents, 'Tab', ['control', 'shift']);
      await until(async () => (await tabs()).some(tab => tab.id === firstTab.id && tab.selected), 'CtrlShiftTab previous project');
      evidence.checks.push('CtrlTab and CtrlShiftTab switch the real active project view');
      await key(first, 'T', ['control']);
      await until(async () => (await tabs()).some(tab => tab.id === 'home' && tab.selected), 'CtrlT opens home picker');
      assert.equal(main.getURL(), initialUrl);
      evidence.checks.push('CtrlT selects the persistent home workspace picker');
      await select(secondTab.id); await key(second, 'W', ['control']);
      await until(() => second.isDestroyed(), 'CtrlW closes only current renderer');
      assert(!first.isDestroyed()); assert((await core.projects(state.service.url)).length === 4);
      assert(!(await tabs()).some(tab => tab.id === secondTab.id));
      evidence.checks.push('CtrlW closes only its project renderer; other views and the same backend service remain available');
      await select(firstTab.id); assert.equal(await first.executeJavaScript('document.querySelector(".composer textarea").value'), '仅属项目 0');
      let closeEvents = 0; const onClose = () => { closeEvents++; }; win.on('close', onClose);
      await home(); await key(main, 'W', ['control']);
      assert.equal(closeEvents, 0); assert((await tabs()).some(tab => tab.id === 'home'));
      evidence.checks.push('CtrlW on the fixed home picker does nothing while project tabs remain open');
      for (const tab of (await tabs()).filter(tab => tab.id !== 'home')) await click(`[data-close-tab-id="${tab.id}"]`);
      await until(async () => (await tabs()).length === 1, 'close remaining tabs');
      await key(main, 'W', ['control']);
      await until(() => closeEvents === 1, 'home CtrlW follows close-to-tray path'); win.removeListener('close', onClose);
      assert(!win.isDestroyed()); assert(!win.isVisible()); assert((await tabs()).some(tab => tab.id === 'home'));
      assert((await core.projects(state.service.url)).length === 4);
      evidence.checks.push('CtrlW on home follows close-to-tray while preserving the window, picker and backend service');
      for (let index = 0; index < 2; index++) assert.equal((await fs.stat(path.join(homeDirectory, 'chat', 'tab-qa-leader-' + index + '.jsonl'))).size, 0);
      evidence.checks.push('fixture chat journals remain empty: no chat was sent and no model turn was started');

      // Exercise the actual home panel through its production bridge/controller.
      menu.getMenuItemById('tools-updates').click();
      await wait('!document.querySelector("#tools-page").hidden && document.querySelectorAll(".tool-card").length===2', 'native menu opens tools panel');
      await check('native tools menu opens a two-tool panel with automatic updating initially off', '!document.querySelector("#tools-auto").checked && document.querySelector("#nav-tools").getAttribute("aria-current")==="page"');
      const checksBefore = toolFixture.checks;
      await js('document.querySelector("#tools-check").click();document.querySelector("#tools-check").click();document.querySelector("#refresh").click()');
      await wait('!document.querySelector("#refresh").disabled', 'workspace refresh during tool check');
      await check('workspace refresh cannot reenable the tools check button during an ongoing check', 'document.querySelector("#tools-check").disabled');
      await wait('!document.querySelector("#tools-check").disabled && document.querySelector(".tool-versions").textContent.includes("9.9.1")', 'simulated version check');
      assert.equal(toolFixture.checks, checksBefore + 1);
      evidence.checks.push('double check clicks issue one helper check and display current/latest versions');
      await capture('08-tools-versions');
      await check('a verified busy tool with can_update false and can_queue true can request an update queue', '!document.querySelector("[data-tool-update=claude]").disabled && document.querySelector("[data-tool-update=claude]").textContent.includes("等待会话结束") && document.querySelector("[data-tool-id=claude] .tool-phase").textContent==="会话使用中"');
      await click('[data-tool-update="claude"]');
      await wait('Boolean(document.querySelector("[data-tool-cancel=claude]"))', 'busy update queues');
      await check('a busy update shows its queue and readable running-session reason', 'document.querySelector("[data-tool-id=claude]").textContent.includes("2 个会话正在运行") && document.querySelector("[data-tool-update=claude]").disabled');
      await capture('09-tools-queued');
      await click('[data-tool-cancel="claude"]');
      await wait('!document.querySelector("[data-tool-cancel=claude]") && document.querySelector("[data-tool-id=claude]").textContent.includes("已取消")', 'cancel update queue');
      evidence.checks.push('cancel waiting removes the actual controller queue and displays cancellation');
      await click('[data-tool-update="codex"]');
      await wait('document.querySelector("[data-tool-id=codex] .tool-phase").textContent==="正在更新"', 'update progress');
      await check('an ongoing update reports progress and prevents duplicate update requests', 'document.querySelector("[data-tool-update=codex]").disabled');
      await wait('document.querySelector("[data-tool-id=codex]").textContent.includes("模拟：安装器暂时不可用") && !document.querySelector("[data-tool-update=codex]").disabled', 'simulated installer failure');
      await capture('10-tools-update-failed');
      await click('[data-tool-update="codex"]');
      await wait('document.querySelector("[data-tool-id=codex] .tool-phase").textContent==="已更新"', 'simulated installer retry');
      await check('an installer failure remains visible and can be retried to a verified new version', 'document.querySelector("[data-tool-id=codex] .tool-versions").textContent.includes("9.9.2") && document.querySelector("[data-tool-update=codex]").disabled');
      toolFixture.current = true;
      await click('#tools-auto');
      await wait('document.querySelector("#tools-auto").checked && !document.querySelector("#tools-auto").disabled && !document.querySelector("#tools-check").disabled', 'enable automatic updates');
      let settings = JSON.parse(await fs.readFile(path.join(homeDirectory, 'desktop/tool-update-settings.json'), 'utf8')); assert.equal(settings.autoUpdate, true);
      await click('#tools-auto');
      await wait('!document.querySelector("#tools-auto").checked && !document.querySelector("#tools-auto").disabled', 'disable automatic updates');
      settings = JSON.parse(await fs.readFile(path.join(homeDirectory, 'desktop/tool-update-settings.json'), 'utf8')); assert.equal(settings.autoUpdate, false);
      evidence.checks.push('the automatic-update switch persists explicit opt-in and can be switched off');
      toolFixture.checkFailure = true;
      await click('#tools-check');
      await wait('!document.querySelector("#tools-global-error").hidden && !document.querySelector("#tools-check").disabled', 'version check error');
      await check('a failed version check keeps known versions and gives a readable retryable error', 'document.querySelector("#tools-global-error").textContent.includes("版本源暂时不可用") && document.querySelectorAll(".tool-card").length===2');
      await capture('11-tools-check-failed');
      toolFixture.checkFailure = false; toolFixture.unsupported = true;
      await click('#tools-check');
      await wait('document.querySelector("[data-tool-id=codex]").textContent.includes("安装来源未识别") && !document.querySelector("#tools-check").disabled', 'unknown installation source');
      await check('an unknown installation source disables update and shows its manual recovery instructions', 'document.querySelector("[data-tool-update=codex]").disabled && document.querySelector("[data-tool-id=codex]").textContent.includes("原来的安装器")');
      win.setSize(win.getMinimumSize()[0], 700); await capture('12-tools-narrow-manual');
      await check('tools controls and failure instructions fit the minimum window width', 'document.documentElement.scrollWidth<=innerWidth && document.querySelector(".tools-content").getBoundingClientRect().right<=innerWidth');
      win.setSize(1220, 850);
      await click('#nav-workspaces');
      await check('sidebar returns from tools to the persistent workspace list', '!document.querySelector("#workspaces-page").hidden && document.querySelector("#tools-page").hidden && document.querySelectorAll(".project").length===4');
      assert.equal(evidence.errors.length, 0, JSON.stringify(evidence.errors)); evidence.ok = true;
      await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(evidence, null, 2));
      app.quit();
    } catch (error) {
      evidence.errors.push(error.stack || String(error));
      evidence.renderers = [];
      for (const contents of webContents.getAllWebContents().filter(contents => contents !== main)) {
        try { evidence.renderers.push({ url:contents.getURL(), body:await contents.executeJavaScript('document.body.innerText'), state:await contents.executeJavaScript('fetch("/api/state"+location.search).then(response=>response.json()).then(state=>({project:state.project,leaders:state.leaders,members:state.members,agents_error:state.agents_error}))') }); }
        catch (diagnosticError) { evidence.renderers.push({ error:String(diagnosticError) }); }
      }
      try { await capture('failure'); } catch (_) { /* Preserve the original assertion. */ }
      await fs.mkdir(output, { recursive:true }); await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(evidence, null, 2));
      console.error(error.stack || String(error)); process.exitCode = 1; app.quit();
    } finally { if (originalDialog) dialog.showOpenDialog = originalDialog; }
  });
});
