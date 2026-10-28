'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');

const source = fs.readFileSync(process.env.CREW_MAIN_LIFECYCLE_SOURCE || path.join(__dirname, '../src/main.cjs'), 'utf8');
const tick = async () => { for (let index = 0; index < 80; index++) await Promise.resolve(); };
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

// Execute the application entry point, but replace all native/process/file IO.
// The destroyed-object checks mirror Electron's native lifetime boundary.
function harness(options = {}) {
  const ready = deferred(), windows = [], trays = [], handlers = new Map(), logs = [], shutdownErrors = [],tabManagers=[];
  const calls = { backendStart:0, backendStop:0, backendDisconnect:0, toolsClose:0, updatesClose:0, tabsDispose:0, previewDispose:0, tabsOpen:0,projectIntents:[],storagePrepared:0,workbenchOpen:0,viewOptions:[],uiCalls:0, windowShow:0, nativeQuit:0, relaunch:0, dialogs:0, stat:0 };
  let readGate = null, projectGate = null, appMenu = null;
  const config = { home:'C:\\fixture\\crew-home', userData:'C:\\fixture\\crew-desktop', isolated:true, smoke:options.smoke === true, backendSource:'bundled' };
  class App extends EventEmitter {
    setName() {} setAppUserModelId() {} setPath() {}
    getPath() { return 'C:\\fixture\\app-data'; }
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
  const electron = { app, BrowserWindow:Window, WebContentsView:class {constructor(options){calls.viewOptions.push(options);}}, Tray:FakeTray,
    ipcMain:{ handle:(name,fn) => handlers.set(name,fn), on() {} },
    Menu:{ buildFromTemplate:menu, setApplicationMenu:value => { appMenu = value; }, getApplicationMenu:() => appMenu },
    nativeImage:{ createFromPath:() => ({}) }, shell:{ openExternal:async () => {}, openPath:async () => {} },
    dialog:{ showMessageBox:async (...args) => { calls.dialogs++; if (args[0] instanceof Window) args[0].alive(); return { response:1 }; }, showOpenDialog:async () => ({ canceled:true }) } };
  class Backend {
    constructor() { this.url = 'http://127.0.0.1:12345'; }
    async start() { calls.backendStart++; await options.backendStart?.promise;if(options.backendError)throw options.backendError; }
    async stop() { calls.backendStop++; await options.backendStop?.promise; }
    async disconnect() { calls.backendDisconnect++; await options.backendDisconnect?.promise; }
  }
  class Tools {
    async initialize() { await options.toolsInitialize?.promise; }
    start() {} async close() { calls.toolsClose++; await options.toolsClose?.promise; }
  }
  class Updates {
    constructor() { this.checking=options.updateCheck?.promise || null; }
    async close() { calls.updatesClose++; options.updateCheck?.resolve(); if (options.updatesError) throw options.updatesError; await options.updatesClose?.promise; }
  }
  class Tabs {
    constructor(window,settings) { this.window = window;this.settings=settings; this.origin = 'http://127.0.0.1:12345'; this.entries = new Map(); this.disposed = false;this.active='home';tabManagers.push(this); }
    state() { return { tabs:[...(this.workbench?[{id:'workbench',project:null,name:'工作台'}]:[]),{ id:'home', project:null,name:'工作区管理' }], activeId:this.active }; }
    setBounds() {} select(id) { this.window.alive();this.active=id; }
    async openWorkbench(url) {calls.workbenchOpen++;await options.workbenchLoad?.promise;if(this.disposed)throw new TypeError('Object has been destroyed');if(!this.workbench)this.settings.createView(null);this.workbench=true;this.workbenchURL=url||this.origin+'/';this.active='workbench';}
    dispose() { calls.tabsDispose++; this.disposed = true; }
    async open(project,intent) { if (this.disposed) throw new TypeError('Object has been destroyed'); calls.tabsOpen++;calls.projectIntents.push({project,intent}); }
    updateProject() { this.window.alive(); } closeProject() { this.window.alive(); }
  }
  class Preview {
    hide() {} refresh() {} dispose() { calls.previewDispose++; }
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
    'node:fs/promises':{ readFile:async () => readGate ? readGate.promise : '/* fixture asset */', mkdir:async () => {}, writeFile:async () => {}, access:async () => options.accessGate?.promise,
      stat:async () => { calls.stat++; return options.statGate ? options.statGate.promise : {isFile:() => true}; } },
    './core.cjs':{ options:() => config, Backend, loopback:value => {new URL(value);return value;},
      projects:async () => projectGate ? projectGate.promise : [{ name:'demo' }],
      projectLocations:async items => items.map(item => ({ ...item, directory_state:{ status:options.directoryMissing?'missing':'ready' } })),
      request:async (_base,route) => options.storageWrite&&route.startsWith('/api/workspace-data/')?options.storageWrite.promise:{} },
    './storage.cjs':{ prepare:async (project,base,_choose,_progress,request) => {calls.storagePrepared++;if(options.storageWrite)await request(base,'/api/workspace-data/open',{});return 'preparedProject' in options?options.preparedProject:project;} },
    './chrome.cjs':{ windowOptions:() => ({}) }, './tabs.cjs':{ WorkspaceTabs:Tabs },
    './activity-preview.cjs':{ ActivityPreview:Preview }, './permissions.cjs':{ installPermissions() {} },
    './tool-updates.cjs':{ ToolUpdates:Tools }, './crew-updates.cjs':{ CrewUpdates:Updates },
    './admin.cjs':{ Administrator:class {} }, './operations.cjs':{ OperationsClient:class { close() {} } },
    './launch-settings.cjs':{ LaunchSettings, relaunchArgs:() => [] }
  };
  const sandbox = { require:name => Object.hasOwn(modules,name) ? modules[name] : require(name), __dirname:path.join(__dirname,'../src'),
    process:{ argv:['fixture','main.cjs'], platform:'win32', env:{...options.env}, versions:{} }, URL, URLSearchParams, Buffer,
    Promise:ObservedPromise, setTimeout, clearTimeout,
    console:{ error:(...args) => logs.push(args), log() {} } };
  vm.runInNewContext(source,sandbox,{ filename:'main.cjs' });
  const invoke = (channel,...args) => { const contents = windows[0].webContents; return handlers.get(channel)({ sender:contents, senderFrame:contents.mainFrame },...args); };
  return { app, ready, windows, trays, calls, logs, shutdownErrors, handlers, invoke,tabManagers,
    menuItem:id => appMenu.getMenuItemById(id),
    holdRead:gate => { readGate = gate; }, holdProjects:gate => { projectGate = gate; },
    boot:async () => { ready.resolve(); await tick(); assert.equal(windows.length,1); assert.equal(trays.length,options.smoke ? 0 : 1); } };
}

test('normal close keeps the shell alive and explicit quit cleans it up exactly once', async () => {
  const h = harness(); await h.boot();
  h.windows[0].close(); assert.equal(h.windows[0].isDestroyed(),false);
  h.trays[0].emit('double-click'); assert.equal(h.calls.windowShow,1);
  await h.invoke('desktop:chrome-ready'); assert.equal(h.calls.uiCalls,1);
  h.app.quit(); h.app.quit(); await tick();
  assert.equal(h.calls.backendDisconnect,1);assert.equal(h.calls.backendStop,0); assert.equal(h.calls.toolsClose,1); assert.equal(h.calls.updatesClose,1);
  assert.equal(h.calls.nativeQuit,1); assert.equal(h.trays[0].isDestroyed(),true);
  assert.deepEqual(h.logs,[]); assert.deepEqual(h.shutdownErrors,[]);
});

test('successful startup selects an unprivileged service workbench and keeps local management trusted',async()=>{
  const h=harness();await h.boot();assert.equal(h.calls.workbenchOpen,1);
  assert.match(h.windows[0].webContents.url,/home\.html$/);
  const state=await h.invoke('desktop:chrome-ready');assert.equal(state.tabs.activeId,'workbench');
  assert.equal(h.calls.viewOptions[0].webPreferences.preload,undefined);
  assert.equal(h.calls.viewOptions[0].webPreferences.nodeIntegration,false);
  assert.equal(h.calls.viewOptions[0].webPreferences.sandbox,true);
  h.menuItem('workspace-home').click();await tick();assert.equal((await h.invoke('desktop:chrome-ready')).tabs.activeId,'home');
  await h.invoke('desktop:chrome-action','home');assert.equal((await h.invoke('desktop:chrome-ready')).tabs.activeId,'workbench');
  const contents=h.windows[0].webContents;
  await assert.rejects(h.handlers.get('desktop:bootstrap')({sender:contents,senderFrame:{...contents.mainFrame,url:'http://127.0.0.1:12345/'}}),/只允许/);
});

test('a backend startup error retains the local administrator instead of opening a false workbench',async()=>{
  const h=harness({backendError:new Error('fixture backend unavailable')});await h.boot();
  assert.equal(h.calls.workbenchOpen,0);assert.match(h.windows[0].webContents.url,/home\.html$/);
  await h.invoke('desktop:chrome-action','home');assert.equal(h.calls.workbenchOpen,0);
});

test('legacy workbench navigation keeps directory and history preparation before preserving its destination',async()=>{
  const h=harness({preparedProject:{name:'storage:verified',read_only:true}});await h.boot();
  await h.tabManagers[0].settings.openProject('demo',{url:'http://127.0.0.1:12345/?legacy=1&project=demo&settings=git#git'});
  assert.equal(h.calls.storagePrepared,1);assert.equal(h.calls.tabsOpen,1);
  const target=new URL(h.calls.projectIntents[0].intent.url);
  assert.equal(target.searchParams.get('project'),'storage:verified');assert.equal(target.searchParams.get('legacy'),'1');
  assert.equal(target.searchParams.get('settings'),'git');assert.equal(target.hash,'#git');
  const cancelled=harness({preparedProject:null});await cancelled.boot();
  await cancelled.tabManagers[0].settings.openProject('demo',{url:'http://127.0.0.1:12345/?legacy=1&project=demo#all'});
  assert.equal(cancelled.calls.tabsOpen,0);assert.equal(cancelled.tabManagers[0].active,'workbench');
  const missing=harness({directoryMissing:true});await missing.boot();
  await missing.tabManagers[0].settings.openProject('demo',{url:'http://127.0.0.1:12345/?legacy=1&project=demo#all'});
  assert.equal(missing.calls.storagePrepared,0);assert.equal(missing.calls.tabsOpen,0);assert.equal(missing.tabManagers[0].active,'home');
});

test('isolated smoke quit stops its self-owned backend instead of detaching it', async () => {
  const h = harness({ smoke:true }); await h.boot();
  h.app.quit(); h.app.quit(); await tick();
  assert.equal(h.calls.backendStop,1);
  assert.equal(h.calls.backendDisconnect,0);
  assert.equal(h.calls.nativeQuit,1);
  assert.deepEqual(h.logs,[]); assert.deepEqual(h.shutdownErrors,[]);
});

test('quit waits for a workspace data operation before disconnecting its backend and never reopens disposed tabs',async()=>{
  const storageWrite=deferred(),h=harness({storageWrite});await h.boot();
  const opening=h.invoke('desktop:open','demo');await tick();
  h.app.quit();await tick();assert.equal(h.calls.backendDisconnect,0);assert.equal(h.calls.nativeQuit,0);
  storageWrite.resolve({});await opening;await tick();
  assert.equal(h.calls.backendDisconnect,1);assert.equal(h.calls.backendStop,0);assert.equal(h.calls.nativeQuit,1);assert.equal(h.calls.tabsOpen,0);
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
    assert.equal(h.calls.backendDisconnect,1);assert.equal(h.calls.backendStop,0);
    assert.equal(h.calls.nativeQuit,1);
  });
}

test('repeated quit shares one cleanup and still disconnects the backend when an updater close fails', async () => {
  const h = harness({ updatesError:new Error('fixture updater close failed') }); await h.boot();
  h.app.quit(); h.app.quit(); await tick();
  assert.equal(h.calls.tabsDispose,1); assert.equal(h.calls.previewDispose,1);
  assert.equal(h.calls.updatesClose,1); assert.equal(h.calls.toolsClose,1); assert.equal(h.calls.backendDisconnect,1);assert.equal(h.calls.backendStop,0);
  assert.equal(h.calls.nativeQuit,1);
  assert.deepEqual(h.shutdownErrors,[]);
  assert(h.logs.flat().some(value => String(value).includes('fixture updater close failed')));
});

test('home navigation suspended at loadURL cannot show a window after quit', async () => {
  const h = harness(); await h.boot();
  h.windows[0].webContents.url = pathToFileURL(path.join(__dirname,'../src/error.html')).href;
  const gate = deferred(); h.windows[0].nextLoad = gate;
  const pending = h.invoke('desktop:chrome-action','settings'); await tick();
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

test('shutdown cancels metadata that a pending connection change awaits, then protects its commit before disconnecting the backend',async () => {
  const check=deferred(),commit=deferred(),h=harness({updateCheck:check,launchCommit:commit});await h.boot();
  const applying=h.invoke('desktop:launch-settings-apply',{});await tick();
  h.app.quit();await tick();
  try {
    assert.equal(h.calls.updatesClose,1,'metadata cancellation must not wait behind a connection change awaiting that metadata');
    assert.equal(h.calls.toolsClose,1);
    assert.equal(h.calls.backendDisconnect,0,'connection commit must finish before the final backend disconnect');
  } finally { check.resolve();commit.resolve();await applying;await tick(); }
  assert.equal(h.calls.backendDisconnect,1);assert.equal(h.calls.backendStop,0);assert.equal(h.calls.nativeQuit,1);assert.equal(h.calls.relaunch,0);
});

test('a portable executable check finishing after quit cannot schedule relaunch',async () => {
  const stat=deferred(),h=harness({statGate:stat,env:{PORTABLE_EXECUTABLE_FILE:'C:\\fixture\\Crew.exe'}});await h.boot();
  const applying=h.invoke('desktop:launch-settings-apply',{});await tick();assert.equal(h.calls.stat,1);
  h.app.quit();await tick();stat.resolve({isFile:() => true});await applying;await tick();
  assert.equal(h.calls.relaunch,0);assert.equal(h.calls.backendDisconnect,1);assert.equal(h.calls.backendStop,0);assert.equal(h.calls.nativeQuit,1);
});

test('a logs lookup finishing during shutdown cannot open a new dialog',async () => {
  const access=deferred(),stop=deferred(),h=harness({accessGate:access,backendDisconnect:stop});await h.boot();
  h.menuItem('app-logs').click();await tick();h.app.quit();await tick();
  access.reject(Object.assign(new Error('fixture logs do not exist'),{code:'ENOENT'}));await tick();
  try { assert.equal(h.calls.dialogs,0);assert.deepEqual(h.logs,[]); }
  finally { stop.resolve();await tick(); }
});
