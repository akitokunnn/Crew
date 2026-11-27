'use strict';
const {test} = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const window = {};
for (const file of ['../../frontend/workspace-start.js', '../src/home-onboarding.js']) {
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, file), 'utf8'), {window});
}
const guide = window.CrewOnboarding;
const ready = (id, extra) => ({id, name:id === 'claude' ? 'Claude Code' : 'Codex', installed:true, logged_in:true, detail:null, error:null, ...extra});
const state = extra => ({step:0, checking:false, readiness:[ready('claude'), ready('codex')], readinessError:'', settingsError:'',
  snapshot:{ai:{default:{runtime:'codex'}}}, engine:'codex', mode:'system', fontScale:1, pending:false, appearance:false,
  folder:null, folderName:'', error:'', ...extra});

test('the guide opens only on local first start with an empty workspace list', () => {
  const first = {remote:false, projects:0, pendingAction:null, status:'pending'};
  assert.equal(guide.shouldOpen(first), true);
  for (const change of [{remote:true},{projects:1},{pendingAction:'new'},{status:'completed'},{status:'skipped'}])
    assert.equal(guide.shouldOpen({...first, ...change}), false);
});

test('only an installed and confirmed engine is selectable', () => {
  assert.equal(guide.status(ready('claude')).label, '已登录');
  assert.equal(guide.status(ready('claude', {installed:false})).label, '未安装');
  assert.equal(guide.status(ready('codex', {logged_in:false})).command, 'codex login');
  assert.equal(guide.status(ready('codex', {logged_in:null})).label, '登录未确认');
  assert.equal(guide.usable(ready('codex', {logged_in:null,error:'超时'})), false);
  assert.equal(guide.defaultEngine('codex', [ready('claude'),ready('codex')]), 'codex');
  assert.equal(guide.defaultEngine('codex', [ready('claude'),ready('codex',{logged_in:false})]), 'claude');
  assert.equal(guide.defaultEngine(null, [ready('claude'),ready('codex')]), null);
  assert.equal(guide.defaultEngine(null, []), null);
});

test('understanding Crew comes before the engine check and the three opening choices', () => {
  const intro = guide.markup(state());
  for (const feature of ['Leader', '思维导图 / 架构图', '作战室']) assert(intro.includes('<h3>' + feature + '</h3>'));
  assert.match(intro, /开始配置/);
  const engine = guide.markup(state({step:1, readiness:[ready('claude',{error:'<b>超时</b>'}),ready('codex')]}));
  assert.match(engine, /&lt;b&gt;超时&lt;\/b&gt;/);
  assert.match(engine, /value="codex" checked/);
  assert.doesNotMatch(engine, /模型|速度|思考强度/);
  const third = guide.markup(state({step:2,folder:{kind:'new',directory:'<temp>'},folderName:'项目'}));
  for (const choice of window.CrewWorkspaceStart.paths) assert(third.includes(choice.title));
  assert.match(third, /开作战室<\/strong><span class="start-recommended">推荐/);
  assert.match(third, /&lt;temp&gt;/);
  assert.match(third, /仅进入工作区/);
});

test('the next action waits for readiness and setup failures keep their reasons', () => {
  const html = guide.markup(state({step:1,readiness:[],readinessError:'后台返回 500',settingsError:'配置损坏'}));
  assert.match(html, /引擎检测失败：后台返回 500/);
  assert.match(html, /读取 AI 设置失败：配置损坏/);
  assert.match(html, /data-ob-act="next" disabled/);
  assert.match(guide.markup(state({step:2})), /data-ob-act="start" disabled>仅进入工作区/);
  assert.match(guide.markup(state({step:2,error:'无法保存引导'})), /role="alert">无法保存引导/);
});

test('appearance opens from the footer without adding a step', () => {
  const html = guide.markup(state({appearance:true,mode:'dark',fontScale:1.2}));
  assert.match(html, /<footer class="ob-foot">.*外观 · 深色 · 120%/);
  assert.match(html, /data-mode="dark" aria-pressed="true"/);
  assert.match(html, /value="1.2" data-ob-field="font"/);
  assert.equal([...html.matchAll(/class="ob-step"/g)].length, 3);
});

test('cancelling the folder picker leaves the guide open and a repeated start opens only one workspace', async () => {
  const handlers = {}, room = {kind:'existing', directory:'project', project:{name:'project'}};
  const dialog = {
    open:false, contains:() => false, setAttribute() {}, querySelector:() => ({focus() {}, scrollTop:0}),
    showModal() { this.open = true; }, close() { this.open = false; }, remove() {},
    addEventListener(type, work) { handlers[type] = work; }
  };
  const browser = {window:{CrewWorkspaceStart:window.CrewWorkspaceStart},
    document:{body:{append() {}},createElement:() => dialog,activeElement:null}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/home-onboarding.js'), 'utf8'), browser);
  const choices = [null, room], opened = [], outcomes = [];
  let release;
  const guide = browser.window.CrewOnboarding.open({
    bridge:{
      theme:async () => ({mode:'system',fontScale:1}), onTheme:() => () => {},
      runtimeReadiness:async () => ({runtimes:[ready('codex')]}),
      settingsGet:async () => ({ai:{default:{runtime:'codex'}}}),
      onboardingSave:async outcome => outcomes.push(outcome)
    },
    onChoose:async () => choices.shift(),
    onWorkspace:(folder, _name, choice) => {
      opened.push([folder.project.name, choice]);
      return new Promise(resolve => { release = resolve; });
    }
  });
  const click = (obAct, start) => handlers.click({target:{closest:() => ({dataset:{obAct,start}})}});
  const settle = () => new Promise(resolve => setImmediate(resolve));
  click('next'); await settle();
  click('next'); await settle();
  assert.equal(guide.state.step, 2);
  click('open-existing'); await settle();
  assert.equal(dialog.open, true);
  assert.equal(guide.state.folder, null);
  assert.deepEqual(outcomes, []);
  click('open-existing'); await settle();
  click('start', 'leader');
  click('start', 'leader');
  assert.deepEqual(opened, [['project','leader']]);
  assert.equal(dialog.open, true);
  release(true); await settle();
  assert.deepEqual(outcomes, ['completed']);
  assert.equal(dialog.open, false);
});
