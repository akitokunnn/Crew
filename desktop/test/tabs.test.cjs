'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const vm = require('node:vm');
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
    wc.loadURL = async url => { wc.loads.push(url); wc.url = url; wc.emit('did-navigate', {}, url); if (options.documentReady !== false) wc.emit('dom-ready'); };
    wc.setWindowOpenHandler = handler => { wc.popup = handler; };
    wc.pageEvents = [];
    wc.page = { dispatchEvent: event => wc.pageEvents.push(event), CustomEvent: class {
      constructor(type, options) { this.type = type; this.detail = options.detail; }
    } };
    wc.page.window = wc.page;
    wc.executeJavaScript = async source => vm.runInNewContext(source, wc.page);
    return wc;
  }
  const windowChildren = [], stage = { children: [], visible: true,
    addChildView(view) { this.children.push(view); }, removeChildView(view) { this.children.splice(this.children.indexOf(view), 1); },
    setVisible(value) { this.visible = value; }, setBounds(value) { this.bounds = { ...value }; } };
  const children = stage.children;
  const window = { webContents: webContents(), close: () => windowClosed++, contentView: {
    addChildView: view => windowChildren.push(view), removeChildView: view => windowChildren.splice(windowChildren.indexOf(view), 1)
  } };
  // A manual clock: animation frames run only when a test advances time.
  let clock = 0, frameId = 0, reduced = false;
  const frames = new Map();
  const manager = new WorkspaceTabs(window, { origin: 'http://127.0.0.1:1234',
    createStage: () => stage, prefersReducedMotion: () => reduced, now: () => clock,
    scheduleFrame: callback => { frames.set(++frameId, callback); return frameId; }, cancelFrame: id => frames.delete(id),
    createView: project => {
    const view = { webContents: webContents(), project, visible: false,
      setVisible(value) { this.visible = value; }, setBounds(value) { this.bounds = { ...value }; },
      setBorderRadius(value) { this.radius = value; } };
    views.push(view); return view;
  }, onChange: state => changed.push(state), onHome: () => homes++, external: url => external.push(url),
  openProject: name => projects.push(name), onKeyboard: (event, input) => keyboard.push({ event, input }), ...options });
  return { manager, views, window, stage, children, windowChildren, changed, external, projects, keyboard, frames,
    advance(ms) {
      const end = clock + ms;
      while (clock < end) { clock = Math.min(end, clock + 16); const due = [...frames.values()]; frames.clear(); for (const run of due) run(); }
    },
    stall(ms) { clock += ms; },
    set reduced(value) { reduced = value; },
    get homes() { return homes; }, get windowClosed() { return windowClosed; } };
}
function event() { return { prevented: false, preventDefault() { this.prevented = true; } }; }

test('workspace and assistant pages receive active-tab visibility while their documents stay mounted', async () => {
  const f = fixture();
  const first = await f.manager.open({name:'A'});
  const a = f.views[0].webContents;
  assert.equal(a.page.crewTabVisible, true);
  await f.manager.openAssistant();
  const assistant = f.views[1].webContents;
  assert.equal(a.page.crewTabVisible, false);
  assert.equal(assistant.page.crewTabVisible, true);
  f.manager.select(first.id);
  assert.equal(a.page.crewTabVisible, true);
  assert.equal(assistant.page.crewTabVisible, false);
  f.manager.select('home');
  assert.equal(a.page.crewTabVisible, false);
  assert.equal(assistant.page.crewTabVisible, false);
  assert.equal(a.loads.length, 1);
  assert.equal(assistant.loads.length, 1);
  assert.equal(a.pageEvents.at(-1).type, 'crew-tab-visibility');
  assert.equal(a.pageEvents.at(-1).detail.visible, false);
  // Reloading a background page replaces its JS global. It must remain inactive.
  delete a.page.crewTabVisible;
  a.emit('did-finish-load');
  assert.equal(a.page.crewTabVisible, false);
  f.manager.dispose();
});

test('cross-workspace task links retain their hash and select the existing document without reloading its draft',async()=>{
  const destinations=[];const f=fixture({openProject:(name,hash)=>destinations.push({name,hash})});
  await f.manager.openAssistant();const assistant=f.views[0].webContents;
  assistant.emit('will-navigate',event(),'http://127.0.0.1:1234/?project=fitness#leader=coach&goal=checkins');
  assert.deepEqual(destinations,[{name:'fitness',hash:'#leader=coach&goal=checkins'}]);
  await f.manager.open({name:'fitness'},destinations[0].hash);const workspace=f.views[1].webContents;
  assert.equal(new URL(workspace.loads[0]).hash,'#leader=coach&goal=checkins');
  workspace.document.draft='不要丢失的输入';const calls=[];workspace.executeJavaScript=async text=>calls.push(text);
  await f.manager.open({name:'fitness'},'#leader=coach&goal=review');
  assert.equal(workspace.loads.length,1);assert.equal(workspace.document.draft,'不要丢失的输入');
  assert.deepEqual(calls,['location.hash="#leader=coach&goal=review"']);
});

test('the assistant is one global conversation across workspace tabs with no project activity polling',async()=>{
  const calls=[];const f=fixture({setTimer:()=>1,clearTimer:()=>{},readActivity:async names=>{calls.push(names);return {projects:names.map(project=>({project,counts:{},items:[]}))};}});
  const first=await f.manager.openAssistant();
  assert.equal(first.project,null);assert.equal(first.kind,'assistant');
  assert.equal(f.views[0].webContents.loads[0],'http://127.0.0.1:1234/assistant.html');
  await f.manager.refreshActivity();assert.equal(calls.length,0);
  f.views[0].webContents.document.draft='跨工作区保留的草稿';
  await f.manager.open({name:'fitness'});await f.manager.open({name:'mib'});
  const second=await f.manager.openAssistant();assert.equal(first.id,second.id);
  assert.equal(f.views.length,3);assert.equal(f.views[0].webContents.loads.length,1);
  assert.equal(f.views[0].webContents.document.draft,'跨工作区保留的草稿');
  assert(calls.every(names=>names.every(name=>name==='fitness'||name==='mib')));
  assert.equal(f.manager.closeProject('fitness'),true);
  assert.equal(f.manager.state().activeId,'assistant');
  const navigate=event();f.views[0].webContents.emit('will-navigate',navigate,'http://127.0.0.1:1234/?project=mib');
  assert.equal(navigate.prevented,true);assert.deepEqual(f.projects,['mib']);
  assert.equal(f.manager.close('assistant'),true);assert.equal(f.views[0].webContents.closed,1);
});

test('saved history tabs remain readable without polling the execution activity endpoint',async()=>{
  const calls=[];const f=fixture({setTimer:()=>1,clearTimer:()=>{},readActivity:async names=>{calls.push(names);return {projects:names.map(project=>({project,counts:{},items:[]}))};}});
  await f.manager.open({name:'storage:archive',read_only:true});await f.manager.refreshActivity();
  assert.equal(calls.length,0);assert.equal(f.manager.state().tabs[1].readOnly,true);
  assert.equal(f.manager.state().tabs[1].activity.label,'已保存的只读历史');
  await f.manager.open({name:'live'});await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(calls.at(-1),['live']);f.manager.dispose();
});

test('one batched activity poll keeps hidden projects current without reloading or acknowledging them',async()=>{
  let tick,cleared=0;const calls=[];
  const f=fixture({setTimer:fn=>(tick=fn,1),clearTimer:()=>cleared++,readActivity:async names=>{
    calls.push(names);return {projects:names.map(project=>({project,counts:{working:2,decisions:1,unread:3},items:[],updated_at:'now'}))};
  }});
  const a=await f.manager.open({name:'A'});await f.manager.open({name:'B'});await new Promise(resolve=>setImmediate(resolve));
  tick();await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(calls.at(-1),['A','B']);
  for(const tab of f.manager.state().tabs.filter(tab=>tab.project)){assert.equal(tab.activity.badge,1);assert.equal(tab.activity.unread,3);}
  f.manager.select(a.id);assert.equal(f.manager.state().tabs[1].activity.unread,3);
  assert(f.views.every(view=>view.webContents.loads.length===1));f.manager.dispose();assert.equal(cleared,1);
});
test('activity fetch failure keeps the last facts and explicitly marks them stale',async()=>{
  let failing=false;
  const f=fixture({setTimer:()=>1,clearTimer:()=>{},readActivity:async()=>{if(failing)throw Error('connection lost');return {projects:[{project:'A',counts:{working:2},items:[]}]};}});
  await f.manager.open({name:'A'});await new Promise(resolve=>setImmediate(resolve));failing=true;await f.manager.refreshActivity();
  const activity=f.manager.state().tabs[1].activity;assert.equal(activity.tone,'stale');assert.equal(activity.counts.working,2);assert.equal(activity.error,'connection lost');f.manager.dispose();
});
test('an in-flight activity response cannot revive a closed or disposed project view',async()=>{
  let resolve;const f=fixture({setTimer:()=>1,clearTimer:()=>{},readActivity:()=>new Promise(done=>resolve=done)});
  const a=await f.manager.open({name:'A'});f.manager.close(a.id);const b=await f.manager.open({name:'A'});
  resolve({projects:[{project:'A',counts:{decisions:9}}]});await new Promise(done=>setImmediate(done));
  assert.equal(f.manager.state().tabs.find(tab=>tab.id===b.id).activity,null);f.manager.dispose();
});

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
  assert.deepEqual(f.stage.bounds, { x: 8, y: 76, width: 900, height: 600 });
  for (const view of f.views) { assert.deepEqual(view.bounds, { x: 0, y: 0, width: 900, height: 600 }); assert.equal(view.radius, 8); }
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
  assert.equal(f.children.length, 0); assert.equal(f.windowChildren.length, 0); assert.equal(f.windowClosed, 0);
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

// Page slide between workspaces and the global assistant.
const WIDTH = 1000;
async function slideFixture(options) {
  const f = fixture(options);
  f.manager.setBounds({ x: 7, y: 75, width: WIDTH, height: 700 });
  return f;
}
const shown = f => f.children.filter(view => view.visible);
const settled = f => {
  assert.equal(f.frames.size, 0, 'no animation frame may remain scheduled');
  assert.equal(f.manager.state().transition, null);
  const active = f.manager.activeView;
  for (const view of f.children) {
    assert.equal(view.visible, view === active, 'only the active page is visible once settled');
    assert.deepEqual(view.bounds, { x: 0, y: 0, width: WIDTH, height: 700 });
  }
  assert.equal(f.stage.visible, Boolean(active));
};

test('project to assistant pushes both pages left, then focuses the assistant at rest', async () => {
  const f = await slideFixture(), a = await f.manager.open({ name: 'A' });
  const project = f.views[0], focusedBefore = project.webContents.focused;
  const opening = f.manager.openAssistant();
  const assistant = f.views[1];
  assert.equal(f.manager.state().tabs.find(tab => tab.id === 'assistant').loading, true, 'the new page slides in while it is still loading');
  assert.deepEqual(f.manager.state().transition, { from: a.id, to: 'assistant' });
  assert(project.bounds.x === 0); assert.equal(assistant.bounds.x, WIDTH);
  assert.deepEqual(shown(f), [project, assistant]); assert.equal(f.stage.visible, true);
  await opening;
  assert.equal(assistant.webContents.page.crewTabVisible, true, 'page visibility does not wait for the slide');
  assert.equal(project.webContents.page.crewTabVisible, false);
  f.advance(160);
  assert(project.bounds.x < -WIDTH / 2 && project.bounds.x > -WIDTH, 'ease-out covers most of the distance early');
  assert.equal(assistant.bounds.x, project.bounds.x + WIDTH, 'the two pages stay edge to edge');
  assert.equal(assistant.webContents.focused, 0, 'focus waits for the slide to finish');
  f.advance(200);
  settled(f); assert.equal(f.manager.activeView, assistant);
  assert.equal(assistant.webContents.focused, 1); assert.equal(project.webContents.focused, focusedBefore);
  assert.equal(f.changed.at(-1).transition, null, 'the shell hears the finished state');

  f.manager.select(a.id);
  assert.deepEqual(f.manager.state().transition, { from: 'assistant', to: a.id });
  assert.equal(assistant.bounds.x, 0); assert.equal(project.bounds.x, -WIDTH);
  f.advance(100);
  assert(assistant.bounds.x > 0, 'returning slides the assistant back out to the right');
  assert.equal(project.bounds.x, assistant.bounds.x - WIDTH);
  f.advance(300); settled(f); assert.equal(f.manager.activeView, project);
});

test('switching back mid-slide reverses from the current offsets and lands on the original page', async () => {
  const f = await slideFixture(), a = await f.manager.open({ name: 'A' });
  await f.manager.openAssistant(); const [project, assistant] = f.views;
  f.advance(64);
  const offset = project.bounds.x;
  f.manager.select(a.id);
  assert(Math.abs(project.bounds.x - offset) <= 1, 'reversal continues from where the pages are');
  assert.equal(assistant.bounds.x, project.bounds.x + WIDTH);
  f.manager.select('assistant'); f.manager.select(a.id); f.manager.select(a.id);
  assert(Math.abs(project.bounds.x - offset) <= 1, 'rapid toggling never jumps');
  f.advance(400); settled(f);
  assert.equal(f.manager.activeId, a.id); assert.equal(f.manager.activeView, project);
  assert.equal(assistant.webContents.focused, 0, 'an interrupted target never steals focus');
  assert(project.webContents.focused > 0);
});

test('selecting a third tab mid-slide settles the old slide and ends on the new selection', async () => {
  const f = await slideFixture(), a = await f.manager.open({ name: 'A' }), b = await f.manager.open({ name: 'B' });
  f.manager.select(a.id);
  await f.manager.openAssistant(); f.advance(100);
  f.manager.select(b.id);
  assert.deepEqual(f.manager.state().transition, { from: 'assistant', to: b.id });
  assert.equal(f.views[0].visible, false, 'the abandoned page no longer covers the view area');
  f.advance(400); settled(f); assert.equal(f.manager.activeView, f.views[1]);
});

test('resizing during a slide keeps the pages aligned and every view ends at the new size', async () => {
  const f = await slideFixture(); await f.manager.open({ name: 'A' });
  await f.manager.openAssistant(); f.advance(100);
  f.manager.setBounds({ x: 7, y: 75, width: 600, height: 500 });
  assert.deepEqual(f.stage.bounds, { x: 7, y: 75, width: 600, height: 500 });
  const [project, assistant] = f.views;
  assert.equal(assistant.bounds.x, project.bounds.x + 600); assert.equal(assistant.bounds.height, 500);
  f.advance(400);
  for (const view of f.views) assert.deepEqual(view.bounds, { x: 0, y: 0, width: 600, height: 500 });
  assert.deepEqual(shown(f), [assistant]); assert.equal(f.frames.size, 0);
});

test('closing either page mid-slide leaves no hidden view or frame behind', async () => {
  const f = await slideFixture(), a = await f.manager.open({ name: 'A' });
  await f.manager.openAssistant(); f.advance(100);
  f.manager.close(a.id);
  settled(f); assert.equal(f.manager.activeId, 'assistant'); assert.equal(f.views[1].webContents.focused, 1);
  assert.deepEqual(f.children, [f.views[1]]);

  const g = await slideFixture(), b = await g.manager.open({ name: 'B' });
  await g.manager.openAssistant(); g.advance(100);
  g.manager.close('assistant');
  settled(g); assert.equal(g.manager.activeId, b.id, 'the neighbor appears directly');
  assert.deepEqual(g.children, [g.views[0]]);

  const h = await slideFixture(); await h.manager.open({ name: 'C' });
  await h.manager.openAssistant(); h.advance(100); h.manager.dispose();
  assert.equal(h.frames.size, 0); assert.equal(h.windowChildren.length, 0);
});

test('the system reduced-motion setting switches pages directly', async () => {
  const f = await slideFixture(), a = await f.manager.open({ name: 'A' });
  f.reduced = true;
  await f.manager.openAssistant();
  settled(f); assert.equal(f.manager.activeView, f.views[1]); assert.equal(f.views[1].webContents.focused, 1);
  f.manager.select(a.id); settled(f); assert.equal(f.manager.activeView, f.views[0]);
  f.reduced = false;
  f.manager.select('assistant'); assert.notEqual(f.manager.state().transition, null, 'the setting is read on every switch');
});

test('only assistant switches slide; home slides as a covered page and the assistant tab stays last', async () => {
  const f = await slideFixture(), a = await f.manager.open({ name: 'A' }), b = await f.manager.open({ name: 'B' });
  f.manager.select(a.id); f.manager.select('home'); f.manager.select(b.id);
  assert.equal(f.frames.size, 0); assert.equal(f.manager.state().transition, null);
  f.manager.select('home'); assert.equal(f.stage.visible, false, 'nothing covers home clicks');

  f.manager.openAssistant(); const assistant = f.views[2];
  assert.deepEqual(f.manager.state().transition, { from: 'home', to: 'assistant' }, 'the shell keeps home painted beneath the slide');
  assert.equal(assistant.bounds.x, WIDTH); assert.deepEqual(shown(f), [assistant]);
  f.advance(400); settled(f);
  const focused = f.window.webContents.focused;
  f.manager.select('home');
  f.advance(100); assert(assistant.bounds.x > 0 && assistant.visible, 'the assistant leaves to the right over home');
  assert.equal(f.window.webContents.focused, focused);
  f.advance(300); settled(f); assert.equal(f.window.webContents.focused, focused + 1);

  await f.manager.open({ name: 'C' });
  assert.deepEqual(f.manager.state().tabs.map(tab => tab.project ?? tab.id), ['home', 'A', 'B', 'C', 'assistant']);
});

test('a failed page never slides; the recovery controls appear directly', async () => {
  const f = await slideFixture(), a = await f.manager.open({ name: 'A' });
  await f.manager.openAssistant(); f.advance(400);
  f.views[0].webContents.emit('render-process-gone');
  f.manager.select(a.id);
  assert.equal(f.frames.size, 0); assert.deepEqual(shown(f), []); assert.equal(f.stage.visible, false);
  assert(f.window.webContents.focused > 0);
});

test('a busy main thread before the first frame does not skip the opening of the slide', async () => {
  const f = await slideFixture(); await f.manager.open({ name: 'A' });
  await f.manager.openAssistant(); const [project, assistant] = f.views;
  f.stall(250);
  f.advance(16);
  assert(project.bounds.x === 0); assert.equal(assistant.bounds.x, WIDTH, 'the first drawn frame is the starting position');
  f.advance(32); assert(assistant.bounds.x < WIDTH && assistant.bounds.x > WIDTH / 2);
  f.advance(400); settled(f);
});

test('a brand-new page holds the slide at its start until its document is ready, but never waits for long', async () => {
  const f = await slideFixture({ documentReady: false }); await f.manager.open({ name: 'A' });
  f.views[0].webContents.emit('dom-ready');
  await f.manager.openAssistant(); const [project, assistant] = f.views;
  f.advance(96);
  assert(project.bounds.x === 0); assert.equal(assistant.bounds.x, WIDTH); assert.equal(assistant.visible, true);
  assistant.webContents.emit('dom-ready');
  f.advance(16); assert(project.bounds.x === 0, 'the first frame after the document is ready is the start');
  f.advance(32); assert(project.bounds.x < 0);
  f.advance(400); settled(f);

  const g = await slideFixture({ documentReady: false }); await g.manager.open({ name: 'B' });
  g.views[0].webContents.emit('dom-ready');
  await g.manager.openAssistant();
  g.advance(144); assert(g.views[0].bounds.x === 0);
  g.advance(48); assert(g.views[0].bounds.x < 0, 'a slow page slides its loading surface in');
  g.advance(400); settled(g);
});

test('a page that fails mid-slide ends the slide with the recovery controls focused', async () => {
  const f = await slideFixture(); await f.manager.open({ name: 'A' });
  await f.manager.openAssistant(); f.advance(100);
  const focused = f.window.webContents.focused;
  f.views[1].webContents.emit('did-fail-load', {}, -105, 'offline', '', true);
  assert.equal(f.frames.size, 0); assert.equal(f.manager.state().transition, null); assert.equal(f.stage.visible, false);
  assert.equal(f.manager.activeId, 'assistant'); assert.deepEqual(shown(f), []);
  for (const view of f.views) assert.deepEqual(view.bounds, { x: 0, y: 0, width: WIDTH, height: 700 });
  assert.equal(f.window.webContents.focused, focused + 1);
});

// The idea wall is a second global page, between the workspaces and the assistant.
test('the idea wall opens as one global page from a workspace link, without project polling or title updates', async () => {
  const calls = [], titles = [];
  const f = fixture({ setTimer: () => 1, clearTimer: () => {}, onTitleChange: name => titles.push(name),
    readActivity: async names => { calls.push(names); return { projects: names.map(project => ({ project, counts: {}, items: [] })) }; } });
  await f.manager.open({ name: 'A' });
  const link = event(); f.views[0].webContents.emit('will-navigate', link, 'http://127.0.0.1:1234/notes.html');
  assert.equal(link.prevented, true, 'the workspace document stays mounted');
  await new Promise(resolve => setImmediate(resolve));
  const notes = f.views[1].webContents;
  assert.equal(f.views[1].project.kind, 'notes');
  assert.deepEqual(notes.loads, ['http://127.0.0.1:1234/notes.html']);
  assert.deepEqual(f.manager.state().tabs.at(-1), { id: 'notes', kind: 'notes', name: '想法', project: null, loading: false, error: false });
  assert.equal(f.manager.state().activeId, 'notes'); assert.equal(notes.page.crewTabVisible, true);
  await f.manager.openAssistant();
  assert.deepEqual(f.manager.state().tabs.map(tab => tab.id), ['home', 'workspace-1', 'notes', 'assistant'], 'the assistant stays last');
  // Filter changes on the wall are in-page hash navigation of the same document.
  notes.emit('did-navigate-in-page', {}, 'http://127.0.0.1:1234/notes.html#tags=%E4%BA%A7%E5%93%81&mode=any', true);
  notes.emit('page-title-updated', event());
  assert.equal(f.manager.state().tabs.find(tab => tab.id === 'notes').error, false);
  assert.deepEqual(titles, []);
  await f.manager.refreshActivity(); assert(calls.every(names => names.every(name => name === 'A')));
  const again = await f.manager.openNotes(); assert.equal(again.id, 'notes'); assert.equal(notes.loads.length, 1);
  assert.equal(f.manager.close('notes'), true); assert.equal(notes.closed, 1);
  f.manager.dispose();
});

test('“去聊聊” hands its note hash to the assistant, reusing an open assistant without reloading its draft', async () => {
  const f = fixture(); await f.manager.openNotes();
  const notes = f.views[0].webContents;
  const chat = event(); notes.emit('will-navigate', chat, 'http://127.0.0.1:1234/assistant.html#note=n-1');
  assert.equal(chat.prevented, true);
  await new Promise(resolve => setImmediate(resolve));
  const assistant = f.views[1].webContents;
  assert.deepEqual(assistant.loads, ['http://127.0.0.1:1234/assistant.html#note=n-1']);
  assert.equal(f.manager.state().activeId, 'assistant');
  assistant.document.draft = '还没发出的草稿'; const calls = []; assistant.executeJavaScript = async text => calls.push(text);
  f.manager.select('notes');
  notes.emit('will-navigate', event(), 'http://127.0.0.1:1234/assistant.html#note=n-2');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(assistant.loads.length, 1); assert.equal(assistant.document.draft, '还没发出的草稿');
  assert(calls.includes('location.hash="#note=n-2"'));
  assert.equal(f.manager.state().activeId, 'assistant');
  // A workspace task link from the wall still opens that workspace.
  notes.emit('will-navigate', event(), 'http://127.0.0.1:1234/?project=fitness#leader=coach');
  assert.deepEqual(f.projects, ['fitness']);
  // A link the wall opens in a new window goes to the same global page.
  assert.deepEqual(notes.popup({ url: 'http://127.0.0.1:1234/notes.html#view=archived' }), { action: 'deny' });
  assert.equal(f.manager.state().activeId, 'notes');
  f.manager.dispose();
});

test('project, idea wall and assistant slide in their tab order', async () => {
  const f = await slideFixture(), a = await f.manager.open({ name: 'A' });
  await f.manager.openNotes(); const [project, notes] = f.views;
  assert.deepEqual(f.manager.state().transition, { from: a.id, to: 'notes' });
  assert(project.bounds.x === 0); assert.equal(notes.bounds.x, WIDTH, 'the wall enters from the right of the workspace');
  f.advance(400); settled(f); assert.equal(f.manager.activeView, notes);

  await f.manager.openAssistant(); const assistant = f.views[2];
  assert.deepEqual(f.manager.state().transition, { from: 'notes', to: 'assistant' });
  assert.equal(assistant.bounds.x, WIDTH, 'the assistant sits to the right of the wall');
  f.advance(400); settled(f);

  f.manager.select('notes');
  assert.deepEqual(f.manager.state().transition, { from: 'assistant', to: 'notes' });
  assert.equal(notes.bounds.x, -WIDTH, 'going back to the wall slides it in from the left');
  f.advance(400); settled(f); assert.equal(f.manager.activeView, notes);

  f.manager.select(a.id);
  f.advance(100); assert(notes.bounds.x > 0, 'leaving the wall for a workspace slides it out to the right');
  f.advance(300); settled(f);

  f.reduced = true; f.manager.select('notes');
  assert.equal(f.manager.state().transition, null, 'reduced motion switches without a slide'); settled(f);
});
