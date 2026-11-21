'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = name => fs.readFileSync(path.join(__dirname,'../src',name),'utf8');
class Element {
  constructor() {
    this.children = [];
    this.events = new Map();
    this.dataset = {};
    this.classList = { toggle() {} };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren() { this.children = []; }
  addEventListener(event,handler) { this.events.set(event,[...(this.events.get(event) || []),handler]); }
  setAttribute() {}
  removeAttribute() {}
  focus() {}
  click() { for (const handler of this.events.get('click') || []) handler(); }
  find(predicate) { return this.children.find(predicate) || this.children.map(child=>child.find(predicate)).find(Boolean); }
}

async function mount(platform) {
  const elements = new Map([...source('home.html').matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)].map(([tag,id]) => [id,Object.assign(new Element(),{hidden:/\bhidden\b/.test(tag)})]));
  const $ = id => elements.get(id), calls = [];
  const tools = {tools:[{id:'codex',name:'Codex',phase:'idle',installed_version:'1.0.0',latest_version:'1.1.0',update_available:true,can_update:true}]};
  const crew = {phase:'idle',desktop_version:'0.3.17',can_update:true};
  const self = {phase:'available',current:'0.3.17',kind:platform==='darwin'?'mac':'installer',restart:true,can_download:true,
    latest:{version:'0.4.0',url:'https://github.com/akitokunnn/Crew/releases/tag/v0.4.0',notes:'修复若干问题'},message:'有新版本 0.4.0。',blocked:[]};
  const reply = (name,result) => async () => { calls.push(name); return result; };
  const sandbox = {document:{getElementById:$,createElement:()=>new Element()},window:{crewDesktop:{
    platform,remote:false,toolsSnapshot:reply('tools snapshot',tools),toolsCheck:reply('tools check',tools),
    onCrewUpdateState(){},crewUpdateSnapshot:reply('Crew snapshot',crew),crewUpdateCheck:reply('Crew check',crew),
    onSelfUpdateState(){},selfUpdateSnapshot:reply('self snapshot',self),selfUpdateCheck:reply('self check',self),selfUpdateDownload:reply('self download',{...self,phase:'ready'})
  }}};
  vm.createContext(sandbox);
  for (const file of ['home-tools.js','home-crew-updates.js','home-self-update.js']) vm.runInContext(source(file),sandbox,{filename:file});
  const settled = () => new Promise(resolve=>setImmediate(resolve));
  await settled(); $('nav-tools').click(); await settled();
  return {$,calls,settled};
}

test('Mac tools page checks versions and offers the Crew self-update without backend unification', async () => {
  const {$,calls,settled} = await mount('darwin');
  assert.equal($('tools-page').hidden,false);
  assert.equal($('crew-update-card').hidden,true);
  assert.equal($('crew-self-update-card').hidden,false);
  assert.equal($('crew-self-latest').textContent,'0.4.0');
  assert.equal($('crew-self-action').textContent,'下载更新');
  $('crew-self-action').click(); await settled();
  assert.equal($('crew-self-action').textContent,'重启并更新');
  calls.splice(calls.indexOf('self snapshot'),1); calls.splice(calls.indexOf('self download'),1);
  assert.equal($('tools-preferences').hidden,true);
  assert.equal($('tools-note').textContent,'Mac 版暂不支持一键更新，请用原安装方式更新。');
  $('tools-check').click(); await settled();
  assert.deepEqual(calls.filter(call=>call.startsWith('tools')),['tools snapshot','tools snapshot','tools check']);
  assert.ok(calls.includes('self check'));
  const list = $('tools-list');
  assert.ok(list.find(el=>el.textContent==='有新版本'));
  assert.ok(list.find(el=>el.textContent==='1.1.0'));
  assert.equal(list.find(el=>el.dataset.toolUpdate),undefined);
});

test('Windows retains the Crew card, saved auto-update controls and tool update action', async () => {
  const {$,calls,settled} = await mount('win32');
  assert.equal($('crew-update-card').hidden,false);
  assert.equal($('crew-self-update-card').hidden,false);
  assert.equal($('tools-preferences').hidden,false);
  assert.ok($('tools-list').find(el=>el.dataset.toolUpdate==='codex'));
  $('crew-update-check').click(); await settled();
  assert.ok(calls.includes('Crew check'));
});
