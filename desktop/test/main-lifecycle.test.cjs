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
  const ready = deferred(), windows = [], trays = [], handlers = new Map(), logs = [], shutdownErrors = [];
  const calls = { backendStart:0, backendStop:0, toolsClose:0, updatesClose:0, tabsDispose:0, previewDispose:0, tabsOpen:0, uiCalls:0, windowShow:0, nativeQuit:0, relaunch:0, dialogs:0, stat:0 };
  let readGate = null, projectGate = null, appMenu = null;
  const config = { home:'C:\\fixture\\crew-home', userData:'C:\\fixture\\crew-desktop', isolated:true, smoke:false, backendSource:'bundled' };
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
  const electron = { app, BrowserWindow:Window, WebContentsView:class {}, Tray:FakeTray,
    ipcMain:{ handle:(name,fn) => handlers.set(name,fn), on() {} },
    Menu:{ buildFromTemplate:menu, setApplicationMenu:value => { appMenu = value; }, getApplicationMenu:() => appMenu },
    nativeImage:{ createFromPath:() => ({}) }, shell:{ openExternal:async () => {}, openPath:async () => {} },
    dialog:{ showMessageBox:async (...args) => { calls.dialogs++; if (args[0] instanceof Window) args[0].alive(); return { response:1 }; }, showOpenDialog:async () => ({ canceled:true }) } };
  class Backend {
    constructor() { this.url = 'http://127.0.0.1:12345'; }
    async start() { calls.backendStart++; await options.backendStart?.promise; }
    async stop() { calls.backendStop++; await options.backendStop?.promise; }
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
    './core.cjs':{ options:() => config, Backend, loopback:value => value,
      projects:async () => projectGate ? projectGate.promise : [{ name:'demo' }],
      projectLocations:async items => items.map(item => ({ ...item, directory_state:{ status:'ready' } })),
      request:async () => ({}) },
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
  return { app, ready, windows, trays, calls, logs, shutdownErrors, handlers, invoke,
    menuItem:id => appMenu.getMenuItemById(id),
    holdRead:gate => { readGate = gate; }, holdProjects:gate => { projectGate = gate; },
    boot:async () => { ready.resolve(); await tick(); assert.equal(windows.length,1); assert.equal(trays.length,1); } };
}

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

test('a logs lookup finishing during shutdown cannot open a new dialog',async () => {
  const access=deferred(),stop=deferred(),h=harness({accessGate:access,backendStop:stop});await h.boot();
  h.menuItem('app-logs').click();await tick();h.app.quit();await tick();
  access.reject(Object.assign(new Error('fixture logs do not exist'),{code:'ENOENT'}));await tick();
  try { assert.equal(h.calls.dialogs,0);assert.deepEqual(h.logs,[]); }
  finally { stop.resolve();await tick(); }
});
