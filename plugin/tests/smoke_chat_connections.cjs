'use strict';
// Real WorkspaceTabs, Electron renderers and HTTP. Every carrier and HOME is isolated.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const nativeFs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn, execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const desktop = path.join(root, 'desktop');
const oldCode = process.argv.includes('--old-code');
const output = path.join(root, 'Temp/chat-connections', oldCode ? 'old-code' : 'fixed');

if (!process.versions.electron) {
  const env = {...process.env};
  delete env.ELECTRON_RUN_AS_NODE;
  const electron = require(require.resolve('electron', {paths:[desktop]}));
  const child = spawn(electron, [__filename, ...process.argv.slice(2)], {
    cwd:root, env, windowsHide:true, stdio:'inherit'
  });
  const timeout = setTimeout(() => child.kill(), 180000);
  child.once('exit', code => { clearTimeout(timeout); process.exitCode = code ?? 1; });
} else {
  const {app, BrowserWindow, WebContentsView, session} = require('electron');
  const browserHome = nativeFs.mkdtempSync(path.join(os.tmpdir(), 'crew-chat-browser-'));
  app.setPath('userData', browserHome);
  app.setPath('sessionData', path.join(browserHome, 'sessions'));
  app.commandLine.appendSwitch('disable-backgrounding-occluded-windows');
  app.on('window-all-closed', () => {});
  const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  const evidence = {oldCode, browserHome, checks:[], screenshots:[], errors:[], timings:[]};
  let fixture, window, tabs;

  async function until(predicate, label, timeout = 8000) {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      if (await predicate()) return;
      await pause(30);
    }
    throw Error('Timed out waiting for ' + label);
  }

  app.whenReady().then(async () => {
    const views = [];
    const requests = [];
    const requestsById = new Map();
    const pendingStreams = new Set();
    let fixtureExited = false;
    try {
      await fs.mkdir(output, {recursive:true});
      let oldRef = null;
      let WorkspaceTabs;
      if (oldCode) {
        const introduced = execFileSync('git', ['log', '-1', '--format=%H', '-G', 'crew-tab-visibility', '--',
          'desktop/src/tabs.cjs'], {cwd:root, encoding:'utf8'}).trim();
        oldRef = introduced ? introduced + '^' : 'HEAD';
        evidence.oldRef = oldRef;
        const filename = path.join(desktop, 'src/tabs.cjs');
        const Module = require('node:module');
        const previous = new Module(filename, module);
        previous.filename = filename;
        previous.paths = Module._nodeModulePaths(path.dirname(filename));
        previous._compile(execFileSync('git', ['show', oldRef + ':desktop/src/tabs.cjs'],
          {cwd:root, encoding:'utf8'}), filename);
        WorkspaceTabs = previous.exports.WorkspaceTabs;
      } else ({WorkspaceTabs} = require(path.join(desktop, 'src/tabs.cjs')));

      const environment = {...process.env, PYTHONIOENCODING:'utf-8'};
      for (const name of ['CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID']) delete environment[name];
      fixture = spawn('python', ['-u', path.join(__dirname, 'chat_connections_fixture.py'), ...(oldRef ? [oldRef] : [])], {
        cwd:root, env:environment, windowsHide:true, stdio:['pipe', 'pipe', 'pipe']
      });
      let buffer = '', info;
      const replies = new Map();
      fixture.stdout.on('data', chunk => {
        buffer += chunk;
        let newline;
        while ((newline = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, newline);
          buffer = buffer.slice(newline + 1);
          try {
            const value = JSON.parse(line);
            if (value.url) info = value;
            if (value.request_id) replies.set(value.request_id, value);
          } catch { /* Test libraries may print a non-JSON diagnostic. */ }
        }
      });
      fixture.stderr.on('data', chunk => process.stderr.write(chunk));
      fixture.once('exit', () => { fixtureExited = true; });
      await until(() => info, 'isolated fixture', 25000);
      console.log('Isolated fixture ready');
      evidence.fixture = info;
      assert.notEqual(new URL(info.url).port, '47811');

      const browserSession = session.defaultSession;
      browserSession.webRequest.onBeforeRequest((request, callback) => {
        if (request.url.startsWith(info.url)) {
          const url = new URL(request.url);
          const record = {id:request.id, path:url.pathname, query:url.search, at:Date.now()};
          requests.push(record);
          requestsById.set(request.id, record);
          if (url.pathname === '/api/chat/events') pendingStreams.add(request.id);
        }
        callback({});
      });
      browserSession.webRequest.onCompleted(request => {
        pendingStreams.delete(request.id);
        Object.assign(requestsById.get(request.id) || {}, {status:request.statusCode});
      });
      browserSession.webRequest.onErrorOccurred(request => {
        pendingStreams.delete(request.id);
        Object.assign(requestsById.get(request.id) || {}, {error:request.error});
      });
      window = new BrowserWindow({show:false, x:-10000, y:-10000, skipTaskbar:true, width:1280, height:900,
        webPreferences:{nodeIntegration:false, contextIsolation:true, sandbox:true,
          backgroundThrottling:false}});
      await window.loadURL('data:text/html;charset=utf-8,<title>Isolated chat connections</title>');
      window.showInactive();
      tabs = new WorkspaceTabs(window, {origin:info.url, onNavigationError:error => evidence.errors.push(error.stack || String(error)),
        createView() {
          const view = new WebContentsView({webPreferences:{nodeIntegration:false, contextIsolation:true,
            sandbox:true, backgroundThrottling:false}});
          view.webContents.on('console-message', event => {
            if (event.level === 'error') evidence.errors.push(`${event.message} (${event.sourceId}:${event.lineNumber})`);
          });
          view.webContents.on('render-process-gone', (_event, detail) => evidence.errors.push('Renderer exited ' + detail.reason));
          views.push(view);
          return view;
        }});
      tabs.setBounds({x:0, y:0, width:1280, height:900});
      const evaluate = async (view, source) => {
        let timeout;
        try {
          return await Promise.race([view.webContents.executeJavaScript(source, true),
            new Promise((_, reject) => { timeout = setTimeout(() => reject(Error('Renderer evaluation timed out')), 5000); })]);
        } finally { clearTimeout(timeout); }
      };
      const contains = (view, marker) => evaluate(view,
        `Boolean(document.querySelector('.chat-inner')?.textContent.includes(${JSON.stringify(marker)}))`);
      async function capture(view, name) {
        await pause(80);
        const filename = path.join(output, name + '.png');
        const debuggerClient = view.webContents.debugger;
        debuggerClient.attach('1.3');
        let timeout;
        try {
          const image = await Promise.race([debuggerClient.sendCommand('Page.captureScreenshot', {
            format:'png', fromSurface:true, captureBeyondViewport:false
          }), new Promise((_, reject) => { timeout = setTimeout(() => reject(Error('Screenshot timed out ' + name)), 5000); })]);
          await fs.writeFile(filename, Buffer.from(image.data, 'base64'));
        } finally { clearTimeout(timeout); debuggerClient.detach(); }
        evidence.screenshots.push(filename);
      }
      const workspaceTabs = [];
      for (const project of info.projects) {
        const tab = await tabs.open(project, '#all');
        workspaceTabs.push(tab);
        try {
          await until(() => evaluate(tabs.activeView, 'Boolean(document.querySelector(".leader-row"))'), 'workspace sidebar');
        } catch (error) {
          evidence.initializationFailure = await evaluate(tabs.activeView, `({url:location.href,
            modules:{CrewGitSettings:typeof CrewGitSettings,...Object.fromEntries(['CrewMaintenance','CrewWorkspaceSettings','CrewSettings','CrewMembers','CrewGoals'].map(name=>[name,typeof window[name]]))},
            text:document.body.innerText})`);
          throw error;
        }
      }
      evidence.checks.push('Eight independent workspace pages load through the actual WorkspaceTabs owner');
      console.log('Eight workspace pages ready');

      for (let index = 0; index < workspaceTabs.length; index++) {
        const tab = workspaceTabs[index], view = views[index], project = info.projects[index];
        tabs.select(tab.id);
        const began = Date.now();
        await evaluate(view, `location.hash=${JSON.stringify('#leader=' + project.session_id)};true;`);
        try { await until(() => contains(view, project.marker), 'history in workspace ' + index, 3500); }
        catch (error) {
          evidence.blocked = {workspace:index, streams:pendingStreams.size,
            sidebar:await evaluate(view, 'Boolean(document.querySelector(".leader-row"))'),
            chat:await evaluate(view, 'document.querySelector(".chat-inner")?.textContent || ""')};
          await capture(view, 'blocked-history-' + index);
          throw error;
        }
        evidence.timings.push({workspace:index, historyMilliseconds:Date.now() - began});
        await evaluate(view, `document.querySelector('.composer textarea').value=${JSON.stringify('保留草稿 ' + index)};
          document.querySelector('.composer textarea').dispatchEvent(new Event('input',{bubbles:true}));
          document.querySelector('.chat-scroll').scrollTop=240;
          document.querySelector('.chat-scroll').dispatchEvent(new Event('scroll'));true;`);
        await capture(view, 'opened-' + index);
      }
      await until(() => pendingStreams.size <= 1, 'hidden workspaces release their SSE connections');
      evidence.streamsAfterEightWorkspaces = pendingStreams.size;
      evidence.checks.push('All eight histories appear promptly while hidden workspace event streams release their connections');
      console.log('Eight conversation histories ready, active streams ' + pendingStreams.size);

      const first = info.projects[0], firstView = views[0];
      const firstHistoryCount = requests.filter(request => request.path === '/api/chat/history' &&
        new URLSearchParams(request.query).get('session_id') === first.session_id).length;
      fixture.stdin.write(JSON.stringify({action:'append', request_id:'hidden-reply', session_id:first.session_id,
        id:'hidden-reply', text:'后台新增消息必须只出现一次'}) + '\n');
      await until(() => replies.has('hidden-reply'), 'hidden reply is persisted');
      console.log('Hidden reply persisted');
      for (let index = workspaceTabs.length - 1; index >= 0; index--) {
        console.log('Returning to workspace ' + index);
        tabs.select(workspaceTabs[index].id);
        const view = views[index];
        await until(() => contains(view, info.projects[index].marker), 'retained history on return');
        assert.equal(await evaluate(view, 'document.querySelector(".composer textarea").value'), '保留草稿 ' + index);
        assert(Math.abs(await evaluate(view, 'document.querySelector(".chat-scroll").scrollTop') - 240) < 3,
          'Returning to a workspace retains its reading position');
        await capture(view, 'returned-' + index);
      }
      await until(() => contains(firstView, '后台新增消息必须只出现一次'), 'resume from the last event cursor');
      assert.equal(await evaluate(firstView,
        `Array.from(document.querySelectorAll('.markdown')).filter(node=>node.textContent.includes('后台新增消息必须只出现一次')).length`), 1);
      assert.equal(requests.filter(request => request.path === '/api/chat/history' &&
        new URLSearchParams(request.query).get('session_id') === first.session_id).length, firstHistoryCount);
      assert(requests.some(request => request.path === '/api/chat/events' &&
        new URLSearchParams(request.query).get('session_id') === first.session_id &&
        Number(new URLSearchParams(request.query).get('after')) > 0));
      evidence.checks.push('Returning keeps drafts and reading positions, replays hidden events after the saved cursor exactly once, and retains loaded history');
      console.log('Drafts, reading positions and hidden reply restored');
      await capture(firstView, 'hidden-reply-restored');

      await evaluate(firstView, `location.hash=${JSON.stringify('#history=' + info.member)};true;`);
      await until(() => contains(firstView, '成员历史'), 'member conversation');
      await capture(firstView, 'member-history');
      await evaluate(firstView, `location.hash=${JSON.stringify('#leader=' + first.session_id + '&goal=' + info.goal)};true;`);
      await until(() => contains(firstView, '任务历史'), 'task conversation');
      await capture(firstView, 'goal-history');
      await tabs.openAssistant();
      await until(() => contains(tabs.activeView, '助手历史'), 'global assistant conversation');
      await capture(tabs.activeView, 'assistant-history');
      await until(() => pendingStreams.size <= 1, 'assistant releases workspace streams');
      evidence.checks.push('Member, independent task and global assistant conversations share the recovered connection lifecycle');
      console.log('Member, task and assistant histories ready');

      const assistantView = tabs.activeView;
      const watched = info.projects.slice(0, 6);
      await evaluate(assistantView, `(async()=>{window.compactFinishes=[];window.compactToasts=[];
        window.compactApi=async path=>{const response=await fetch(path,{headers:{'X-Crew':'1'}});const value=await response.json();if(!response.ok)throw Error(value.error);return value;};
        window.compactWatchers=await Promise.all(${JSON.stringify(watched)}.map(project=>CrewChat.watchCompact(project.session_id,{
          state:{project},api:compactApi,toast:message=>compactToasts.push(message),onFinish:event=>compactFinishes.push(event)
        })));return true;})();`);
      const readBegan = Date.now();
      const readable = await evaluate(assistantView, `compactApi(${JSON.stringify('/api/chat/history?session_id=' + info.assistant)}).then(value=>value.events.length)`);
      assert(readable > 0);
      evidence.historyWithSixCompactWatchersMilliseconds = Date.now() - readBegan;
      assert(evidence.historyWithSixCompactWatchersMilliseconds < 2000);
      fixture.stdin.write(JSON.stringify({action:'compact', request_id:'compact-results',
        session_ids:watched.map(project => project.session_id)}) + '\n');
      await until(() => replies.has('compact-results'), 'six compact receipts persisted');
      await until(() => evaluate(assistantView, 'compactFinishes.length===6'), 'six compact watchers finish');
      assert(await evaluate(assistantView, 'compactToasts.length===6&&compactToasts.every(message=>message.startsWith("压缩完成"))'));
      await until(() => pendingStreams.size <= 1, 'compact watchers retain no extra streams');
      evidence.checks.push('Six parallel compaction watchers leave other histories readable and receive all completion receipts');
      await capture(assistantView, 'six-compact-watchers');
      fixture.stdin.write(JSON.stringify({action:'inspect', request_id:'final-task'}) + '\n');
      await until(() => replies.has('final-task'), 'running task inspection');
      evidence.taskAfterSwitching = replies.get('final-task');
      assert.equal(evidence.taskAfterSwitching.task_alive, true);
      assert.equal(evidence.taskAfterSwitching.task_busy, true);
      assert.equal(evidence.taskAfterSwitching.model_launches, info.model_launches);
      evidence.checks.push('The original task stays alive and busy throughout navigation, with no additional carrier starts');
      assert.equal(evidence.errors.length, 0, evidence.errors.join('\n'));
      evidence.ok = true;
    } catch (error) {
      evidence.failure = error.stack || String(error);
      process.exitCode = 1;
    } finally {
      evidence.requests = requests;
      await fs.mkdir(output, {recursive:true});
      await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(evidence, null, 2));
      tabs?.dispose();
      if (fixture && !fixtureExited) {
        fixture.stdin.end(JSON.stringify({action:'shutdown'}) + '\n');
        try { await until(() => fixtureExited, 'isolated fixture cleanup', 10000); }
        catch { fixture.kill(); }
      }
      console.log(JSON.stringify({ok:evidence.ok || false, checks:evidence.checks.length,
        evidence:path.join(output, 'evidence.json'), failure:evidence.failure || null}));
      window?.destroy();
      app.exit(evidence.ok ? 0 : 1);
    }
  });
}
