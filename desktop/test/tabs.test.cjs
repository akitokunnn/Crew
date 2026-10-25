'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { WorkspaceTabs } = require('../src/tabs.cjs');

function fixture(options = {}) {
  const views = [], changed = [], external = [], projects = [], keyboard = [];
  let homes = 0, windowClosed = 0;
  function webContents() {
    const wc = new EventEmitter();
    wc.loads = []; wc.focused = 0; wc.closed = 0; wc.reloaded = 0; wc.document = {};
    wc.isDestroyed = () => wc.closed > 0;
    wc.getURL = () => wc.url || '';
    wc.focus = () => wc.focused++;
    wc.close = () => { wc.closed++; wc.emit('destroyed'); };
    wc.reload = () => wc.reloaded++;
    wc.navigationHistory = { canGoBack: () => false, goBack: () => { wc.wentBack = true; } };
    wc.loadURL = async url => { wc.loads.push(url); wc.url = url; wc.emit('did-navigate', {}, url); };
    wc.setWindowOpenHandler = handler => { wc.popup = handler; };
    return wc;
  }
  const children = [];
  const window = { webContents: webContents(), close: () => windowClosed++, contentView: {
    addChildView: view => children.push(view), removeChildView: view => children.splice(children.indexOf(view), 1)
  } };
  const manager = new WorkspaceTabs(window, { origin: 'http://127.0.0.1:1234', createView: project => {
    const view = { webContents: webContents(), project, visible: false,
      setVisible(value) { this.visible = value; }, setBounds(value) { this.bounds = { ...value }; },
      setBorderRadius(value) { this.radius = value; } };
    views.push(view); return view;
  }, onChange: state => changed.push(state), onHome: () => homes++, external: url => external.push(url),
  openProject: name => projects.push(name), onKeyboard: (event, input) => keyboard.push({ event, input }), ...options });
  return { manager, views, window, children, changed, external, projects, keyboard,
    get homes() { return homes; }, get windowClosed() { return windowClosed; } };
}
function event() { return { prevented: false, preventDefault() { this.prevented = true; } }; }

test('a workspace page title change refreshes metadata and preserves its live document',async()=>{
  const titles=[];const f=fixture({onTitleChange:name=>titles.push(name)});
  await f.manager.open({name:'stable-key',display_name:'Before',root:'C:/old'});
  const wc=f.views[0].webContents,loads=wc.loads.length,document=wc.document;
  wc.emit('page-title-updated',event(),'Renamed · Crew');assert.deepEqual(titles,['stable-key']);
  f.manager.updateProject({name:'stable-key',display_name:'After',root:'C:/new'});
  assert.equal(f.manager.state().tabs[1].name,'After');assert.equal(wc.loads.length,loads);assert.equal(wc.document,document);
  wc.url='https://example.com/';wc.emit('page-title-updated',event(),'Foreign');assert.equal(titles.length,1);
  f.manager.dispose();
});

test('an offline empty shell can reconnect after delayed backend readiness while existing workspace origins cannot be replaced',async()=>{
  const f=fixture();
  f.manager.reconnect('http://127.0.0.1:4321');
  await f.manager.open({name:'recovered',root:'/recovered'});
  assert.equal(f.views[0].webContents.loads[0],'http://127.0.0.1:4321/?project=recovered');
  assert.throws(()=>f.manager.reconnect('http://127.0.0.1:5432'),/已有工作区/);
  const g=fixture();assert.throws(()=>g.manager.reconnect('https://example.com'),/loopback/);
});

test('projects deduplicate by stable name and switching retains each document without reload', async () => {
  const f = fixture(), first = await f.manager.open({ name: '项目 A', root: '/a' });
  const a = f.views[0], document = a.webContents.document;
  Object.assign(document, { draft: 'unsent A', scroll: 147, leader: 'A-lead' });
  const second = await f.manager.open({ name: '项目 B', root: '/b' });
  Object.assign(f.views[1].webContents.document, { draft: 'unsent B', scroll: 48, leader: 'B-lead' });
  assert.equal(a.visible, false); assert.equal(f.views[1].visible, true);
  const duplicate = await f.manager.open({ name: '项目 A', root: '/new-metadata' });
  assert.equal(duplicate.id, first.id); assert.equal(f.views.length, 2);
  assert.strictEqual(a.webContents.document, document);
  assert.deepEqual(document, { draft: 'unsent A', scroll: 147, leader: 'A-lead' });
  assert.equal(a.webContents.loads.length, 1); assert.equal(f.views[1].webContents.loads.length, 1);
  assert.equal(a.visible, true); assert.equal(f.views[1].visible, false);
  assert.equal(f.manager.activeView, a);
  assert.deepEqual(f.manager.state().tabs.map(tab => tab.project), [null, '项目 A', '项目 B']);
  assert.deepEqual(f.manager.state().tabs.map(tab => tab.id), ['home', first.id, second.id]);
});

test('closing selects a neighbor, keeps home, and owns no service shutdown', async () => {
  const f = fixture(), a = await f.manager.open({ name: 'A' }), b = await f.manager.open({ name: 'B' });
  f.manager.select('home');
  assert.equal(f.manager.close('home'), false);
  assert.equal(f.windowClosed, 0, 'fixed home tab cannot hide other open workspaces');
  f.manager.select(a.id); f.manager.close(a.id);
  assert.equal(f.manager.activeId, b.id); assert.equal(f.views[0].webContents.closed, 1);
  assert.equal(f.views[1].webContents.closed, 0); assert.equal(f.children.length, 1);
  f.manager.close(b.id);
  assert.equal(f.manager.activeId, 'home'); assert.equal(f.children.length, 0); assert.equal(f.homes, 2);
  assert.equal(f.windowClosed, 0);
  assert.equal(f.manager.close('unknown'), false);
  f.manager.close('home'); assert.equal(f.windowClosed, 1);
  assert.equal(f.manager.state().tabs.length, 1, 'home close only asks the parent window to hide');
});

test('workspace display and directory updates retain stable tab identity and live draft', async () => {
  const f=fixture(),a=await f.manager.open({name:'stable-A',display_name:'展示 A',root:'/old'}),b=await f.manager.open({name:'stable-B',display_name:'展示 B',root:'/b'});
  const document=f.views[0].webContents.document;document.draft='still unsent';
  assert.equal(a.name,'展示 A');assert.equal(a.project,'stable-A');
  assert.equal(f.manager.updateProject({name:'stable-A',display_name:'新的展示名称',root:'/new'}),true);
  const updated=f.manager.state().tabs.find(tab=>tab.id===a.id);assert.equal(updated.name,'新的展示名称');assert.equal(updated.project,'stable-A');
  assert.equal(f.views[0].webContents.loads.length,1);assert.equal(f.views[0].webContents.document.draft,'still unsent');assert.equal(f.manager.activeId,b.id);
  assert.equal(f.manager.closeProject('stable-A'),true);assert.equal(f.views[1].webContents.closed,0);assert.equal(f.manager.activeId,b.id);assert.equal(f.manager.closeProject('missing'),false);
});

test('only the current main-frame workspace can route its native settings intent', async () => {
  const opened=[],f=fixture({workspaceSettings:name=>opened.push(name)});await f.manager.open({name:'stable-A'});
  const wc=f.views[0].webContents,valid=event();wc.emit('will-navigate',valid,'http://127.0.0.1:1234/?workspace-settings=stable-A');assert(valid.prevented);assert.deepEqual(opened,['stable-A']);assert.equal(wc.loads.length,1);
  wc.emit('will-navigate',event(),'http://127.0.0.1:1234/?workspace-settings=other');
  wc.emit('will-redirect',event(),'http://127.0.0.1:1234/?workspace-settings=stable-A',false,false);assert.deepEqual(opened,['stable-A']);
});

test('cycling, bounds, back, reload and keyboard callbacks target the active page', async () => {
  const f = fixture(), a = await f.manager.open({ name: 'A' }), b = await f.manager.open({ name: 'B' });
  f.manager.setBounds({ x: 8, y: 76, width: 900, height: 600 });
  for (const view of f.views) { assert.deepEqual(view.bounds, { x: 8, y: 76, width: 900, height: 600 }); assert.equal(view.radius, 8); }
  assert.throws(() => f.manager.setBounds({ x: 0, y: 0, width: NaN, height: 1 }));
  f.manager.cycle(1); assert.equal(f.manager.activeId, 'home');
  f.manager.cycle(-1); assert.equal(f.manager.activeId, b.id);
  f.manager.cycle(-1); assert.equal(f.manager.activeId, a.id);
  f.manager.reload(); assert.equal(f.views[0].webContents.reloaded, 1);
  f.views[0].webContents.navigationHistory.canGoBack = () => true;
  assert.equal(f.manager.back(), true); assert.equal(f.views[0].webContents.wentBack, true);
  const input = { key: 'Tab', control: true }, pressed = event();
  f.views[0].webContents.emit('before-input-event', pressed, input);
  assert.deepEqual(f.keyboard, [{ event: pressed, input }]);
  f.manager.select('home'); f.manager.reload(); assert.equal(f.window.webContents.reloaded, 1);
});

test('navigation remains scoped to the original project and cross-project opens a verified tab', async () => {
  const f = fixture(); await f.manager.open({ name: 'A' }); const wc = f.views[0].webContents;
  const same = event(); wc.emit('will-navigate', same, 'http://127.0.0.1:1234/?project=A#leader=one');
  assert.equal(same.prevented, false);
  const other = event(); wc.emit('will-navigate', other, 'http://127.0.0.1:1234/?project=B');
  assert.equal(other.prevented, true); assert.deepEqual(f.projects, ['B']);
  const home = event(); wc.emit('will-navigate', home, 'http://127.0.0.1:1234/');
  assert.equal(home.prevented, true); assert.equal(f.manager.activeId, 'home');
  for (const url of ['file:///C:/Crew/home.html', 'javascript:alert(1)', 'http://user@127.0.0.1:1234/?project=A',
    'http://127.0.0.1:1234/?project=A&project=B']) {
    const attempt = event(); wc.emit('will-navigate', attempt, url); assert.equal(attempt.prevented, true);
  }
  assert.deepEqual(f.projects, ['B']); assert.deepEqual(f.external, []);
});

test('external clicks open a browser, but external redirects and subframes never do', async () => {
  const f = fixture(); await f.manager.open({ name: 'A' }); const wc = f.views[0].webContents;
  const click = event(); wc.emit('will-navigate', click, 'https://example.com/reference');
  assert.equal(click.prevented, true); assert.deepEqual(f.external, ['https://example.com/reference']);
  const redirect = event(); wc.emit('will-redirect', redirect, 'https://example.com/redirect', false, true);
  assert.equal(redirect.prevented, true); assert.equal(f.external.length, 1); assert.equal(f.manager.state().tabs[1].error, true);
  const iframe = event(); wc.emit('will-redirect', iframe, 'http://127.0.0.1:1234/?project=B', false, false);
  assert.equal(iframe.prevented, true); assert.deepEqual(f.projects, []);
  assert.deepEqual(wc.popup({ url: 'https://example.com/popup' }), { action: 'deny' });
  assert.equal(f.external.length, 2);
  assert.deepEqual(wc.popup({ url: 'file:///C:/Crew/home.html' }), { action: 'deny' });
  assert.equal(f.external.length, 2);
  assert.deepEqual(wc.popup({ url: 'http://127.0.0.1:1234/?project=B' }), { action: 'deny' });
  assert.deepEqual(f.projects, ['B']); assert.equal(f.external.length, 2);
  wc.popup({ url: 'http://127.0.0.1:1234/' }); assert.equal(f.manager.activeId, 'home');
  wc.popup({ url: 'http://127.0.0.1:1234/?project=A#leader=one' });
  assert.equal(f.manager.activeId, 'workspace-1'); assert.equal(f.external.length, 2);
});

test('rejected cross-project navigation reports the error while preserving the healthy source and draft', async () => {
  const failures=[];
  const f = fixture({ openProject: async () => { throw new Error('unknown project'); }, onNavigationError:error=>failures.push(error.message) });
  await f.manager.open({ name: 'A' });
  const original=f.views[0].webContents.document;
  original.draft='preserve this unsent draft';
  const navigation = event();
  f.views[0].webContents.emit('will-navigate', navigation, 'http://127.0.0.1:1234/?project=unknown');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(navigation.prevented, true); assert.equal(f.manager.state().tabs[1].error, false);
  assert.equal(f.views[0].visible,true);assert.equal(f.views[0].webContents.loads.length,1);
  assert.strictEqual(f.views[0].webContents.document,original);assert.equal(original.draft,'preserve this unsent draft');
  assert.deepEqual(failures,['unknown project']);
});

test('loading, failed requests, crashes and disposal update state without reviving closed views', async () => {
  const f = fixture(), a = await f.manager.open({ name: 'A' }), wc = f.views[0].webContents;
  wc.emit('did-start-loading'); assert.equal(f.manager.state().tabs[1].loading, true);
  wc.emit('did-fail-load', {}, -105, 'offline', wc.url, true);
  assert.equal(f.manager.state().tabs[1].loading, false); assert.equal(f.manager.state().tabs[1].error, true);
  assert.equal(f.views[0].visible, false, 'failed native view must not cover the parent recovery controls');
  f.manager.select(a.id); assert.equal(f.views[0].visible, false);
  wc.emit('did-start-loading'); wc.emit('did-stop-loading'); assert.equal(f.manager.state().tabs[1].error, false);
  assert.equal(f.views[0].visible, true);
  wc.emit('render-process-gone'); assert.equal(f.manager.state().tabs[1].error, true); assert.equal(f.views[0].visible, false);
  wc.reload = () => { throw new Error('crashed reload failed'); };
  assert.equal(f.manager.reload(), false); assert.equal(f.manager.state().tabs[1].error, true); assert.equal(f.views[0].visible, false);
  f.manager.close(a.id); const changes = f.changed.length;
  wc.emit('did-start-loading'); assert.equal(f.changed.length, changes);
  await f.manager.open({ name: 'B' }); f.manager.dispose(); f.manager.dispose();
  assert.equal(f.children.length, 0); assert.equal(f.windowClosed, 0);
  assert.equal(f.views[1].webContents.closed, 1); assert.equal(f.manager.state().activeId, 'home');
  await assert.rejects(f.manager.open({ name: 'C' }), /closed/);
});

test('background load and recovery never reveal a view above the active workspace', async () => {
  const f = fixture(), a = await f.manager.open({ name: 'A' });
  await f.manager.open({ name: 'B' });
  const wc = f.views[0].webContents;
  wc.emit('render-process-gone'); assert.equal(f.views[0].visible, false);
  wc.emit('did-start-loading'); assert.equal(f.views[0].visible, false);
  wc.emit('did-navigate', {}, 'http://127.0.0.1:1234/?project=A');
  assert.equal(f.views[0].visible, false); assert.equal(f.views[1].visible, true);
  f.manager.select(a.id); assert.equal(f.views[0].visible, true); assert.equal(f.views[1].visible, false);
});
