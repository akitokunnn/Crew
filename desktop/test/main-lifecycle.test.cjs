'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');
const http = require('node:http');
const { listen } = require('./fixtures/loopback.cjs');
const python = require('../src/python.cjs');
// A native absolute path on every platform, so path.dirname sees real separators.
const FIXTURE_APP_DATA = process.platform === 'win32' ? 'C:\\fixture\\app-data' : '/fixture/app-data';

const source = fs.readFileSync(process.env.CREW_MAIN_LIFECYCLE_SOURCE || path.join(__dirname, '../src/main.cjs'), 'utf8');
const tick = async () => { for (let index = 0; index < 80; index++) await Promise.resolve(); };
// Values created inside the sandboxed main process belong to another realm.
const plain = value => JSON.parse(JSON.stringify(value));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

// Execute the application entry point, but replace all native/process/file IO.
// The destroyed-object checks mirror Electron's native lifetime boundary.
function harness(options = {}) {
  const ready = deferred(), windows = [], trays = [], handlers = new Map(), logs = [], shutdownErrors = [];
  const calls = { backendStart:0, backendStop:0, toolsClose:0, updatesClose:0, tabsDispose:0, previewDispose:0, reportClose:0, tabsOpen:0, uiCalls:0, windowShow:0, nativeQuit:0, relaunch:0, dialogs:0, stat:0 };
  let readGate = null, projectGate = null, appMenu = null;
  const config = { home:'C:\\fixture\\crew-home', userData:'C:\\fixture\\crew-desktop', isolated:true, smoke:false, backendSource:'bundled',
    launchSettingsPath:path.join(FIXTURE_APP_DATA,'Crew','launch-settings.json'), ...(options.remote ? { remoteBackend:'saved-host' } : {}) };
  const webRequests = { headers:null, completed:null };
  class App extends EventEmitter {
    setName() {} setAppUserModelId() {} setPath() {}
    getPath() { return FIXTURE_APP_DATA; }
    getVersion() { return '0.0.0-fixture'; }
    requestSingleInstanceLock() { return true; }
    whenReady() { return ready.promise; }
    quit() {
      let prevented = false;
      this.emit('before-quit', { preventDefault() { prevented = true; } });
      if (!prevented) { calls.nativeQuit++; for (const window of windows) window.destroy(); }
    }
    exit() { calls.nativeQuit++; for (const window of windows) window.destroy(); }
    relaunch() { calls.relaunch++; }
  }
  const app = new App();
  app.isPackaged = !!options.packaged;
  class Contents extends EventEmitter {
    constructor(owner) { super(); this.owner = owner; this.url = ''; this.mainFrame = { url:'' }; this.session = {}; this.navigationHistory = { canGoBack:() => false }; }
    alive() { if (this.isDestroyed()) throw new TypeError('Object has been destroyed'); }
    isDestroyed() { return this.owner.destroyed; }
    getURL() { this.alive(); return this.url; }
    send() { this.alive(); }
    setWindowOpenHandler() { this.alive(); }
    async insertCSS() { this.alive(); calls.uiCalls++; }
    async executeJavaScript() { this.alive(); calls.uiCalls++; }
  }
  class Window extends EventEmitter {
    constructor() { super(); this.destroyed = false; this.webContents = new Contents(this); this.contentView = { removeChildView() {} }; windows.push(this); }
    alive() { if (this.destroyed) throw new TypeError('Object has been destroyed'); }
    isDestroyed() { return this.destroyed; }
    destroy() { if (this.destroyed) return; this.destroyed = true; this.webContents.emit('destroyed'); this.emit('closed'); }
    show() { this.alive(); calls.windowShow++; } focus() { this.alive(); } hide() { this.alive(); }
    close() { this.alive(); let prevented = false; this.emit('close', { preventDefault() { prevented = true; } }); if (!prevented) this.destroy(); }
    isMaximized() { this.alive(); return false; } isFocused() { this.alive(); return true; }
    getContentSize() { this.alive(); return [1220,850]; }
    setTitle() { this.alive(); } setAutoHideMenuBar() { this.alive(); } setMenuBarVisibility() { this.alive(); }
    loadURL(url) { this.alive(); this.webContents.url = url; this.webContents.mainFrame.url = url; return this.nextLoad?.promise || Promise.resolve(); }
  }
  class FakeTray extends EventEmitter {
    constructor() { super(); this.destroyed = false; trays.push(this); }
    setToolTip() {} setContextMenu(menu) { this.menu = menu; }
    isDestroyed() { return this.destroyed; }
    destroy() { this.destroyed = true; this.removeAllListeners(); }
  }
  const menu = template => ({ template, getMenuItemById(id) { return template.flatMap(item => item.submenu || [item]).find(item => item.id === id); }, popup({ callback }) { callback?.(); } });
  const electron = { app, BrowserWindow:Window, WebContentsView:class {}, Tray:FakeTray,
    nativeTheme:{ themeSource:'system', shouldUseDarkColors:false, on() {} }, webContents:{ getAllWebContents:() => [] },
    ipcMain:{ handle:(name,fn) => handlers.set(name,fn), on() {} },
    Menu:{ buildFromTemplate:menu, setApplicationMenu:value => { appMenu = value; }, getApplicationMenu:() => appMenu },
    nativeImage:{ createFromPath:file => { calls.image = file; return {}; } }, shell:{ openExternal:async () => {}, openPath:async () => {} }, safeStorage:{},
    session:{ defaultSession:{ webRequest:{ onBeforeSendHeaders:(filter, listener) => { webRequests.headers = { filter, listener }; }, onCompleted:(filter, listener) => { webRequests.completed = { filter, listener }; } } } },
    dialog:{ showMessageBox:async (...args) => { calls.dialogs++; if (args[0] instanceof Window) args[0].alive(); return { response:options.dialogResponse ?? 1 }; }, showOpenDialog:async () => ({ canceled:true }),
      showSaveDialog:async () => ({ canceled:false, filePath:'C:\\Users\\me\\demo.crewpack' }) } };
  class Backend {
    constructor() { this.url = options.backendUrl || 'http://127.0.0.1:12345'; }
    async start() { calls.backendStart++; calls.backendPath = sandbox.process.env.PATH; await options.backendStart?.promise; if(options.backendError)throw options.backendError; }
    async stop() { calls.backendStop++; await options.backendStop?.promise; }
  }
  class RemoteBackend {
    constructor(id, dependencies) { this.id = id; this.dependencies = dependencies; this.remote = true; this.owned = false; this.url = 'http://127.0.0.1:0'; this.startupError = null; }
    async start() {
      calls.backendStart++; this.url = 'http://192.168.1.20:47812'; this.name = '书房主机';
      if (options.remoteStart) await options.remoteStart(this);
      this.health = { version:'0.3.99', home:'/Users/host/.crew', pid:7, instance_id:'host', warnings:[] };
    }
    async stop() { calls.backendStop++; }
  }
  class Tools {
    constructor() { calls.tools = (calls.tools || 0) + 1; }
    async initialize() { await options.toolsInitialize?.promise; }
    start() {} async close() { calls.toolsClose++; await options.toolsClose?.promise; }
  }
  class Updates {
    constructor() { this.checking=options.updateCheck?.promise || null; }
    async close() { calls.updatesClose++; options.updateCheck?.resolve(); if (options.updatesError) throw options.updatesError; await options.updatesClose?.promise; }
  }
  class Tabs {
    constructor(window) { this.window = window; this.origin = 'http://127.0.0.1:12345'; this.entries = new Map(); this.disposed = false; }
    state() { return { tabs:[{ id:'home', project:null }], activeId:'home' }; }
    setBounds() {} select() { this.window.alive(); }
    dispose() { calls.tabsDispose++; this.disposed = true; }
    async open() { if (this.disposed) throw new TypeError('Object has been destroyed'); calls.tabsOpen++; }
    updateProject() { this.window.alive(); } closeProject() { this.window.alive(); }
  }
  class Preview {
    hide() {} refresh() {} dispose() { calls.previewDispose++; }
  }
  class ReportPreviewClient {
    constructor() { calls.reportPreviews = (calls.reportPreviews || 0) + 1; }
    async start() { await options.reportStart?.promise; }
    async close() { calls.reportClose++; await options.reportClose?.promise; }
  }
  class LaunchSettings {
    constructor(_config,dependencies) { this.dependencies=dependencies; this.pending=null; }
    apply() {
      this.pending=(async () => {
        if(this.dependencies.updates.checking)await this.dependencies.updates.checking;
        await options.launchCommit?.promise;
        await this.dependencies.onApplied();
        return {ok:true};
      })().finally(() => { this.pending=null; });
      return this.pending;
    }
  }
  class ObservedPromise extends Promise {
    finally(callback) {
      const result = super.finally(callback);
      result.catch(error => shutdownErrors.push(error));
      return result;
    }
  }
  const modules = {
    electron,
    'node:fs':{ mkdirSync() {} },
    'node:fs/promises':{ readFile:async file => readGate ? readGate.promise : String(file).endsWith('plugin.json') ? '{"name":"crew","version":"0.3.17"}' : String(file).endsWith('web.json') ? JSON.stringify(options.recoveryRecord) : '/* fixture asset */', mkdir:async () => {}, writeFile:async () => {}, access:async () => options.accessGate?.promise,
      stat:async () => { calls.stat++; return options.statGate ? options.statGate.promise : {isFile:() => true}; } },
    './core.cjs':{ options:() => config, Backend, RemoteBackend, loopback:require('../src/core.cjs').loopback, remoteHealth:value => value,
      projects:async () => projectGate ? projectGate.promise : [{ name:'demo', root:'C:\\demo', directory_state:{ status:'ready' } }],
      newWorkspace:async (parent,name,base) => { calls.created = { parent, name, base }; return { name }; },
      hostFolders:async (base,directory) => ({ base, path:directory }),
      download:async (base,route,destination) => { calls.download = { base, route, destination }; return { path:destination }; },
      request:async (base,route,body,timeoutMs) => options.request ? options.request(base,route,body,timeoutMs) : options.storageWrite&&route.startsWith('/api/workspace-data/')?options.storageWrite.promise:{} },
    './storage.cjs':{ prepare:async (project,base,_choose,_progress,request) => {if(options.storageWrite)await request(base,'/api/workspace-data/open',{});return project;} },
    './chrome.cjs':{ windowOptions:() => ({}) }, './tabs.cjs':{ WorkspaceTabs:Tabs },
    './appearance.cjs':{ Appearance:class { async load() { return this.state(); } state() { return { mode:'system', dark:false, stored:false }; } async set(mode) { return { mode, dark:mode === 'dark', stored:true }; } } },
    './menu.cjs':require('../src/menu.cjs'), './login-shell.cjs':{ readLoginShellPath:() => options.loginPath.promise },
    './python.cjs':python,
    './activity-preview.cjs':{ ActivityPreview:Preview }, './permissions.cjs':{ installPermissions() {} },
    './report-preview.cjs':{ReportPreviewClient, ReportPreviewRenderer:class {}},
    './tool-updates.cjs':{ ToolUpdates:Tools }, './crew-updates.cjs':{ CrewUpdates:Updates },
    './crew-self-update.cjs':{ CrewSelfUpdate:class { async initialize() {} async confirmReady() {} start() {} async close() {} }, installKind:() => ({ kind:'development' }) },
    './admin.cjs':{ Administrator:class {} }, './operations.cjs':{ OperationsClient:class { close() {} } },
    './product.cjs':require('../src/product.cjs'),
    './launch-settings.cjs':{ LaunchSettings, relaunchArgs:() => [], rememberPort:async () => false },
    './remote-backends.cjs':{ RemoteBackends:class { constructor(file) { calls.remoteFile = file; } async credential(id) { return { id, token:'t'.repeat(43) }; } } }
  };
  const sandbox = { require:name => Object.hasOwn(modules,name) ? modules[name] : require(name), __dirname:path.join(__dirname,'../src'),
    process:{ argv:['fixture','main.cjs'], platform:options.platform || 'win32', env:{...options.env}, versions:{}, resourcesPath:path.resolve('fixture resources') }, URL, URLSearchParams, Buffer,
    Promise:ObservedPromise, setTimeout, clearTimeout, setInterval, clearInterval,
    console:{ error:(...args) => logs.push(args), log() {} } };
  // Electron loads main.cjs through the CommonJS function wrapper.
  vm.runInNewContext('(function(){\n'+source+'\n})()',sandbox,{ filename:'main.cjs' });
  const invoke = (channel,...args) => { const contents = windows[0].webContents; return handlers.get(channel)({ sender:contents, senderFrame:contents.mainFrame },...args); };
  return { app, config, ready, windows, trays, calls, logs, shutdownErrors, handlers, invoke, webRequests, menu:() => appMenu,
    menuItem:id => appMenu.getMenuItemById(id),
    holdRead:gate => { readGate = gate; }, holdProjects:gate => { projectGate = gate; },
    boot:async () => { ready.resolve(); await tick(); assert.equal(windows.length,1); assert.equal(trays.length,1); } };
}

test('remote access IPC reads and changes the connected backend only from the trusted home', async t => {
  const requests=[];
  const server=http.createServer(async(req,res)=>{
    let text='';for await(const chunk of req)text+=chunk;
    const body=text?JSON.parse(text):null;requests.push({url:req.url,method:req.method,body,crew:req.headers['x-crew']});
    res.setHeader('Content-Type','application/json');
    if(body?.action==='revoke'){res.writeHead(409);res.end(JSON.stringify({error:'设备已被移除，请刷新列表'}));return;}
    res.end(JSON.stringify(body?.action==='pair'?{code:'123456',expires_at:'2026-11-01T12:05:00Z'}:{enabled:false,port:null,urls:[],devices:[],pair_pending:false}));
  });
  await listen(server);t.after(()=>new Promise(resolve=>server.close(resolve)));
  const h=harness({backendUrl:'http://127.0.0.1:'+server.address().port,request:require('../src/core.cjs').request});await h.boot();
  assert.deepEqual(await h.invoke('desktop:remote-access-read'),{enabled:false,port:null,urls:[],devices:[],pair_pending:false});
  assert.deepEqual(await h.invoke('desktop:remote-access-action',{action:'pair'}),{code:'123456',expires_at:'2026-11-01T12:05:00Z'});
  await assert.rejects(h.invoke('desktop:remote-access-action',{action:'revoke',device_id:'missing'}),/设备已被移除，请刷新列表/);
  assert.deepEqual(requests,[
    {url:'/api/remote-access',method:'GET',body:null,crew:'1'},
    {url:'/api/remote-access',method:'POST',body:{action:'pair'},crew:'1'},
    {url:'/api/remote-access',method:'POST',body:{action:'revoke',device_id:'missing'},crew:'1'}
  ]);
  const contents=h.windows[0].webContents;
  for(const channel of ['desktop:remote-access-read','desktop:remote-access-action']){
    for(const event of [{sender:{},senderFrame:contents.mainFrame},{sender:contents,senderFrame:{url:contents.mainFrame.url}},{sender:contents,senderFrame:{url:'https://example.test/'}}])
      await assert.rejects(h.handlers.get(channel)(event,{action:'start'}),/只允许来自 Crew 工作区首页/);
  }
  assert.equal(requests.length,3,'untrusted frames must not reach the backend');
  h.app.quit();await tick();
});

test('remote access completion after window shutdown does not access the closed window or repeat an action', async t => {
  const received=deferred();let respond,requests=0;
  const server=http.createServer((req,res)=>{requests++;req.resume();respond=()=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ok:true}));};received.resolve();});
  await listen(server);t.after(()=>new Promise(resolve=>server.close(resolve)));
  const h=harness({backendUrl:'http://127.0.0.1:'+server.address().port,request:require('../src/core.cjs').request});await h.boot();
  const pending=h.invoke('desktop:remote-access-action',{action:'stop'});await received.promise;
  h.app.quit();await tick();assert.equal(h.windows[0].isDestroyed(),true);const uiCalls=h.calls.uiCalls;
  respond();assert.deepEqual(await pending,{ok:true});assert.equal(h.calls.uiCalls,uiCalls);assert.equal(requests,1);
  await assert.rejects(h.invoke('desktop:remote-access-read'),/只允许来自 Crew 工作区首页/);
});

test('normal close keeps the shell alive and explicit quit cleans it up exactly once', async () => {
  const h = harness(); await h.boot();
  h.windows[0].close(); assert.equal(h.windows[0].isDestroyed(),false);
  h.trays[0].emit('double-click'); assert.equal(h.calls.windowShow,1);
  await h.invoke('desktop:chrome-ready'); assert.equal(h.calls.uiCalls,1);
  h.app.quit(); h.app.quit(); await tick();
  assert.equal(h.calls.backendStop,1); assert.equal(h.calls.toolsClose,1); assert.equal(h.calls.updatesClose,1);
  assert.equal(h.calls.nativeQuit,1); assert.equal(h.trays[0].isDestroyed(),true);
  assert.deepEqual(h.logs,[]); assert.deepEqual(h.shutdownErrors,[]);
});

test('Mac launch waits for the shell PATH and Dock activation restores the hidden window', async () => {
  const loginPath = deferred(), h = harness({platform:'darwin',loginPath});
  h.ready.resolve(); await tick();
  assert.equal(h.calls.backendStart,0);
  loginPath.resolve('/opt/homebrew/bin:/usr/bin'); await h.boot();
  assert.equal(h.calls.backendPath,'/opt/homebrew/bin:/usr/bin');
  assert.equal(h.menu().template[0].role,'appMenu');
  assert.equal(h.menuItem('window-back').accelerator,'Cmd+[');
  assert.equal(path.basename(h.calls.image),'crew-trayTemplate.png');
  assert.equal((await h.invoke('desktop:bootstrap')).platform,'darwin');
  assert.equal(h.handlers.has('desktop:crew-update-apply'),false);
  assert.equal(h.handlers.has('desktop:tools-check'),true);
  h.windows[0].close(); assert.equal(h.windows[0].isDestroyed(),false);
  h.app.emit('activate'); assert.equal(h.calls.windowShow,1);
  h.app.quit(); await tick();
  assert.deepEqual(h.logs,[]);
});

test('Mac shell failure blocks the backend and displays the real startup error', async () => {
  const loginPath = deferred(), h = harness({platform:'darwin',loginPath});
  h.ready.resolve(); await tick(); loginPath.reject(new Error('fixture shell error')); await tick();
  assert.equal(h.calls.backendStart,0);
  assert.equal(h.windows.length,0);
  assert.equal(h.calls.dialogs,1);
  assert.equal(h.calls.nativeQuit,1);
  assert.match(h.logs[0][0].message,/fixture shell error/);
});

test('a stale local service record cannot replace the Python version failure on the home page', async () => {
  const loginPath = deferred(); loginPath.resolve('/opt/homebrew/bin:/usr/bin');
  const message = 'Crew 需要 Python 3.11 或更新版本，当前 python3 是 3.10。 可用 Homebrew 安装：brew install python';
  const h = harness({platform:'darwin',loginPath,backendError:new python.PythonVersionError(message),recoveryRecord:{url:'http://127.0.0.1:12345'}});
  await h.boot();
  await assert.rejects(h.invoke('desktop:bootstrap'),{message});
  h.app.quit(); await tick();
});

test('quit waits for a workspace data operation before stopping its backend and never reopens disposed tabs',async()=>{
  const storageWrite=deferred(),h=harness({storageWrite});await h.boot();
  const opening=h.invoke('desktop:open','demo');await tick();
  h.app.quit();await tick();assert.equal(h.calls.backendStop,0);assert.equal(h.calls.nativeQuit,0);
  storageWrite.resolve({});await opening;await tick();
  assert.equal(h.calls.backendStop,1);assert.equal(h.calls.nativeQuit,1);assert.equal(h.calls.tabsOpen,0);
  assert.deepEqual(h.logs,[]);assert.deepEqual(h.shutdownErrors,[]);
});

test('queued tray, native menu and second-instance callbacks ignore a closed window', async () => {
  const h = harness(); await h.boot();
  const queued = [h.trays[0].listeners('double-click')[0], h.trays[0].menu.template[0].click,
    h.app.listeners('second-instance')[0], h.menuItem('window-close').click, h.menuItem('workspace-home').click];
  h.app.quit(); await tick();
  assert.equal(h.windows[0].isDestroyed(),true);
  for (const callback of queued) assert.doesNotThrow(callback);
  await tick();
  assert.deepEqual(h.logs,[]);
});

test('tray and ready-to-show callbacks cannot reopen the shell while shutdown is pending', async () => {
  const gate = deferred(), h = harness({ updatesClose:gate }); await h.boot();
  const queued = h.trays[0].listeners('double-click')[0];
  h.app.quit(); await tick();
  assert.equal(h.windows[0].isDestroyed(),false);
  queued(); h.windows[0].emit('ready-to-show');
  assert.equal(h.calls.windowShow,0);
  gate.resolve(); await tick();
  assert.equal(h.calls.nativeQuit,1);
});

test('quit before Electron ready never starts the backend or constructs a window', async () => {
  const h = harness(); h.app.quit(); await tick(); h.ready.resolve(); await tick();
  assert.equal(h.calls.backendStart,0); assert.equal(h.windows.length,0);
});

for (const boundary of ['backendStart','toolsInitialize']) {
  test('quit during '+boundary+' never resumes desktop construction', async () => {
    const gate = deferred(), h = harness({ [boundary]:gate });
    h.ready.resolve(); await tick();
    h.app.quit(); await tick(); gate.resolve(); await tick();
    assert.equal(h.windows.length,0); assert.equal(h.trays.length,0);
    assert.equal(h.calls.backendStop,1);
    assert.equal(h.calls.nativeQuit,1);
  });
}

test('repeated quit shares one cleanup and still stops the backend when an updater close fails', async () => {
  const h = harness({ updatesError:new Error('fixture updater close failed') }); await h.boot();
  h.app.quit(); h.app.quit(); await tick();
  assert.equal(h.calls.tabsDispose,1); assert.equal(h.calls.previewDispose,1);
  assert.equal(h.calls.updatesClose,1); assert.equal(h.calls.toolsClose,1); assert.equal(h.calls.backendStop,1);
  assert.equal(h.calls.nativeQuit,1);
  assert.deepEqual(h.shutdownErrors,[]);
  assert(h.logs.flat().some(value => String(value).includes('fixture updater close failed')));
});

test('home navigation suspended at loadURL cannot show a window after quit', async () => {
  const h = harness(); await h.boot();
  h.windows[0].webContents.url = pathToFileURL(path.join(__dirname,'../src/error.html')).href;
  const gate = deferred(); h.windows[0].nextLoad = gate;
  const pending = h.invoke('desktop:chrome-action','home'); await tick();
  h.app.quit(); await tick(); gate.resolve();
  await pending.catch(error => assert.doesNotMatch(error.message,/Object has been destroyed/));
});

for (const channel of ['desktop:chrome-ready','desktop:settings-ui','desktop:workspace-ui']) {
  test(channel+' stops native UI injection when its file read finishes after quit', async () => {
    const h = harness(); await h.boot();
    const gate = deferred(); h.holdRead(gate);
    const pending = h.invoke(channel); await tick();
    h.app.quit(); await tick(); gate.resolve('/* delayed fixture asset */');
    await pending.catch(error => assert.doesNotMatch(error.message,/Object has been destroyed/));
    assert.equal(h.calls.uiCalls,0);
  });
}

test('opening a workspace cannot mutate disposed tabs when discovery finishes after quit', async () => {
  const h = harness(); await h.boot();
  const gate = deferred(); h.holdProjects(gate);
  const pending = h.invoke('desktop:open','demo'); await tick();
  h.app.quit(); await tick(); gate.resolve([{ name:'demo' }]);
  await pending.catch(error => assert.doesNotMatch(error.message,/Object has been destroyed/));
  assert.equal(h.calls.tabsOpen,0);
});

test('shutdown cancels metadata that a pending connection change awaits, then protects its commit before stopping the backend',async () => {
  const check=deferred(),commit=deferred(),h=harness({updateCheck:check,launchCommit:commit});await h.boot();
  const applying=h.invoke('desktop:launch-settings-apply',{});await tick();
  h.app.quit();await tick();
  try {
    assert.equal(h.calls.updatesClose,1,'metadata cancellation must not wait behind a connection change awaiting that metadata');
    assert.equal(h.calls.toolsClose,1);
    assert.equal(h.calls.backendStop,0,'connection commit must finish before the final backend stop');
  } finally { check.resolve();commit.resolve();await applying;await tick(); }
  assert.equal(h.calls.backendStop,1);assert.equal(h.calls.nativeQuit,1);assert.equal(h.calls.relaunch,0);
});

test('a portable executable check finishing after quit cannot schedule relaunch',async () => {
  const stat=deferred(),h=harness({statGate:stat,env:{PORTABLE_EXECUTABLE_FILE:'C:\\fixture\\Crew.exe'}});await h.boot();
  const applying=h.invoke('desktop:launch-settings-apply',{});await tick();assert.equal(h.calls.stat,1);
  h.app.quit();await tick();stat.resolve({isFile:() => true});await applying;await tick();
  assert.equal(h.calls.relaunch,0);assert.equal(h.calls.backendStop,1);assert.equal(h.calls.nativeQuit,1);
});

test('tray restart relaunches Crew after the normal cleanup when nothing is working',async () => {
  const routes=[],h=harness({request:async (_base,route) => { routes.push(route); return {sessions:[{name:'lead',busy:false}]}; }});await h.boot();
  h.trays[0].menu.template.find(item => item.id==='tray-restart').click();await tick();
  assert.deepEqual(routes,['/api/admin/connection/plan']);assert.equal(h.calls.dialogs,0);
  assert.equal(h.calls.relaunch,1);assert.equal(h.calls.backendStop,1);assert.equal(h.calls.nativeQuit,1);
  assert.deepEqual(h.logs,[]);assert.deepEqual(h.shutdownErrors,[]);
});

test('restart warns about working sessions and cancelling keeps Crew running',async () => {
  const h=harness({request:async () => ({sessions:[{name:'lead',busy:true}]})});await h.boot();
  h.menuItem('app-restart').click();await tick();
  assert.equal(h.calls.dialogs,1);assert.equal(h.calls.relaunch,0);assert.equal(h.calls.backendStop,0);assert.equal(h.calls.nativeQuit,0);
  h.app.quit();await tick();
});

test('confirming restart over working sessions relaunches, and an unreachable backend does not block restart',async () => {
  const h=harness({dialogResponse:0,request:async () => ({sessions:[{name:'lead',busy:true}]})});await h.boot();
  h.menuItem('app-restart').click();await tick();
  assert.equal(h.calls.dialogs,1);assert.equal(h.calls.relaunch,1);assert.equal(h.calls.nativeQuit,1);
  const down=harness({request:async () => { throw new Error('fixture backend down'); }});await down.boot();
  down.trays[0].menu.template.find(item => item.id==='tray-restart').click();await tick();
  assert.equal(down.calls.dialogs,0);assert.equal(down.calls.relaunch,1);assert.equal(down.calls.nativeQuit,1);
  assert.equal(down.logs.length,1);assert.match(String(down.logs[0][1]),/fixture backend down/);
});

test('a logs lookup finishing during shutdown cannot open a new dialog',async () => {
  const access=deferred(),stop=deferred(),h=harness({accessGate:access,backendStop:stop});await h.boot();
  h.menuItem('app-logs').click();await tick();h.app.quit();await tick();
  access.reject(Object.assign(new Error('fixture logs do not exist'),{code:'ENOENT'}));await tick();
  try { assert.equal(h.calls.dialogs,0);assert.deepEqual(h.logs,[]); }
  finally { stop.resolve();await tick(); }
});

test('quitting while report preview registration is pending closes it before the backend',async () => {
  const start=deferred(),close=deferred(),h=harness({reportStart:start,reportClose:close});
  h.ready.resolve();await tick();h.app.quit();await tick();
  assert.equal(h.calls.backendStop,0);
  start.resolve();await tick();
  assert.equal(h.windows.length,0,'late report registration must not create the desktop UI after quit');
  assert.equal(h.calls.reportClose,1);assert.equal(h.calls.backendStop,0);
  close.resolve();await tick();
  assert.equal(h.calls.backendStop,1);assert.equal(h.calls.nativeQuit,1);
  assert.deepEqual(h.logs,[]);
});

test('saved LAN hosts live beside the launch settings, outside every HOME', () => {
  assert.equal(harness().calls.remoteFile, path.join(FIXTURE_APP_DATA, 'Crew', 'remote-backends.json'));
});

test('a LAN connection starts no local tools or report worker, and its token reaches only the host origin', async () => {
  const h = harness({ remote:true }); await h.boot();
  assert.equal(h.calls.tools, undefined); assert.equal(h.calls.reportPreviews, undefined); assert.equal(h.calls.backendStart, 1);
  const { listener } = h.webRequests.headers, headers = {};
  listener({ url:'http://192.168.1.20:47812/api/state?project=demo', requestHeaders:{ Accept:'*/*' } }, value => { headers.host = value.requestHeaders; });
  listener({ url:'http://192.168.1.21:47812/api/state', requestHeaders:{ Accept:'*/*' } }, value => { headers.other = value.requestHeaders; });
  listener({ url:'http://192.168.1.20:47813/', requestHeaders:{} }, value => { headers.port = value.requestHeaders; });
  assert.equal(headers.host.Authorization, 'Bearer ' + 't'.repeat(43));
  assert.deepEqual(headers.other, { Accept:'*/*' }); assert.deepEqual(headers.port, {});
  assert.deepEqual(plain(await h.invoke('desktop:connection')), { mode:'remote', id:'saved-host', name:'书房主机', url:'http://192.168.1.20:47812',
    host_version:'0.3.99', local_version:'0.3.17', auth_expired:false, error:null });
  h.app.quit(); await tick();
  assert.equal(h.calls.nativeQuit, 1);
});

test('a LAN connection hides host-only menu items and refuses this computer\'s backend, tool and update channels', async () => {
  const h = harness({ remote:true }); await h.boot();
  const ids = h.menu().template[0].submenu.map(item => item.id);
  for (const id of ['workspace-import', 'workspace-handoff', 'tools-updates']) assert(!ids.includes(id), id);
  for (const id of ['workspace-new', 'workspace-open', 'workspace-export', 'system-administrator', 'app-settings']) assert(ids.includes(id), id);
  for (const channel of ['desktop:admin-check', 'desktop:admin-restart', 'desktop:admin-chat-snapshot', 'desktop:admin-config-get', 'desktop:storage-import', 'desktop:existing',
    'desktop:parent', 'desktop:workspace-directory', 'desktop:tools-snapshot', 'desktop:crew-update-snapshot', 'desktop:remote-access-read'])
    await assert.rejects(h.invoke(channel), /只能在主机上的 Crew 中进行/, channel);
  assert.deepEqual(await h.invoke('desktop:host-folders', 'D:\\'), { base:'http://192.168.1.20:47812', path:'D:\\' });
  await assert.rejects(h.invoke('desktop:create', '新项目'), /主机上的保存位置/);
  assert.deepEqual(await h.invoke('desktop:create', '新项目', 'D:\\work'), { name:'新项目' });
  assert.deepEqual(h.calls.created, { parent:'D:\\work', name:'新项目', base:'http://192.168.1.20:47812' });
  await assert.rejects(h.invoke('desktop:storage-activate', 'w1'), /主机上的代码目录/);
  h.app.quit(); await tick();
});

test('a local window keeps its native folder dialogs and refuses the LAN host picker', async () => {
  const h = harness(); await h.boot();
  for (const id of ['workspace-import', 'workspace-handoff', 'tools-updates']) assert(h.menu().template[0].submenu.some(item => item.id === id), id);
  await assert.rejects(h.invoke('desktop:host-folders', 'C:\\'), /只用于连接局域网主机/);
  await assert.rejects(h.invoke('desktop:create', 'x', 'C:\\anywhere'), /系统对话框/);
  await assert.rejects(h.invoke('desktop:storage-activate', 'w1', 'C:\\anywhere'), /系统对话框/);
  assert.deepEqual(plain(await h.invoke('desktop:connection')), { mode:'local' });
  h.app.quit(); await tick();
});

test('LAN settings read and save the host HTTP settings, and export downloads the host package to this computer', async () => {
  const routes = [], h = harness({ remote:true, request:async (base, route, body) => { routes.push([base, route, body]); return route.endsWith('/export') ? { download_id:'abc123', cached_files:[] } : { ai:{} }; } });
  await h.boot();
  await h.invoke('desktop:settings-get'); await h.invoke('desktop:settings-save', { ai:{} });
  await h.invoke('desktop:storage-export', 'demo');
  assert.deepEqual(plain(routes), [['http://192.168.1.20:47812', '/api/runtimes?refresh=1', null], ['http://192.168.1.20:47812', '/api/settings', null],
    ['http://192.168.1.20:47812', '/api/settings', { ai:{} }], ['http://192.168.1.20:47812', '/api/workspace-data/export', { project:'demo' }]]);
  assert.deepEqual(h.calls.download, { base:'http://192.168.1.20:47812', route:'/api/workspace-data/download?id=abc123', destination:'C:\\Users\\me\\demo.crewpack' });
  h.app.quit(); await tick();
});

test('restarting a LAN window never asks the host for its session plan', async () => {
  const routes = [], h = harness({ remote:true, request:async (_base, route) => { routes.push(route); return {}; } }); await h.boot();
  h.menuItem('app-restart').click(); await tick();
  assert.deepEqual(routes, []); assert.equal(h.calls.dialogs, 0); assert.equal(h.calls.relaunch, 1);
});

test('a revoked device found at startup still opens the window and reports the lost authorization once', async () => {
  const h = harness({ remote:true, remoteStart:async backend => { backend.dependencies.onUnauthorized(); throw Object.assign(new Error('设备授权已失效，请在主机上重新生成配对码'), { status:401 }); } });
  await h.boot();
  const state = await h.invoke('desktop:connection');
  assert.equal(state.auth_expired, true); assert.equal(state.error, '授权已失效，请在主机上重新生成配对码');
  await assert.rejects(h.invoke('desktop:bootstrap'), /授权已失效/);
  await assert.rejects(h.invoke('desktop:admin-status'), /授权已失效/);
  h.webRequests.completed.listener({ url:'http://192.168.1.20:47812/api/state', statusCode:401 });
  assert.equal((await h.invoke('desktop:connection')).auth_expired, true);
  h.app.quit(); await tick();
});
