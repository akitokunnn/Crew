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
  addEventListener(event,handler) { this.events.set(event,handler); }
  setAttribute() {}
  removeAttribute() {}
  focus() {}
  click() { this.events.get('click')?.(); }
  find(predicate) { return this.children.find(predicate) || this.children.map(child=>child.find(predicate)).find(Boolean); }
}

async function mount(platform) {
  const elements = new Map([...source('home.html').matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)].map(([tag,id]) => [id,Object.assign(new Element(),{hidden:/\bhidden\b/.test(tag)})]));
  const $ = id => elements.get(id), calls = [];
  const tools = {tools:[{id:'codex',name:'Codex',phase:'idle',installed_version:'1.0.0',latest_version:'1.1.0',update_available:true,can_update:true}]};
  const crew = {phase:'idle',desktop_version:'0.3.17',can_update:true};
  const reply = (name,result) => async () => { calls.push(name); return result; };
  const sandbox = {document:{getElementById:$,createElement:()=>new Element()},window:{crewDesktop:{
    platform,remote:false,toolsSnapshot:reply('tools snapshot',tools),toolsCheck:reply('tools check',tools),
    onCrewUpdateState(){},crewUpdateSnapshot:reply('Crew snapshot',crew),crewUpdateCheck:reply('Crew check',crew)
  }}};
  vm.createContext(sandbox);
  for (const file of ['home-tools.js','home-crew-updates.js']) vm.runInContext(source(file),sandbox,{filename:file});
  const settled = () => new Promise(resolve=>setImmediate(resolve));
  await settled(); $('nav-tools').click(); await settled();
  return {$,calls,settled};
}

test('Mac tools page offers the same tool updates and auto-update preference as Windows', async () => {
  const {$,calls,settled} = await mount('darwin');
  assert.equal($('tools-page').hidden,false);
  // Crew itself still updates by downloading a new dmg on Mac.
  assert.equal($('crew-update-card').hidden,true);
  assert.equal($('crew-mac-update-card').hidden,false);
  assert.equal($('tools-preferences').hidden,false);
  $('tools-check').click(); await settled();
  assert.deepEqual(calls,['tools snapshot','tools snapshot','tools check']);
  const list = $('tools-list');
  assert.ok(list.find(el=>el.textContent==='有更新'));
  assert.ok(list.find(el=>el.textContent==='1.1.0'));
  assert.ok(list.find(el=>el.dataset.toolUpdate==='codex'));
});

test('Windows retains the Crew card, saved auto-update controls and tool update action', async () => {
  const {$,calls,settled} = await mount('win32');
  assert.equal($('crew-update-card').hidden,false);
  assert.equal($('crew-mac-update-card').hidden,true);
  assert.equal($('tools-preferences').hidden,false);
  assert.ok($('tools-list').find(el=>el.dataset.toolUpdate==='codex'));
  $('crew-update-check').click(); await settled();
  assert.ok(calls.includes('Crew check'));
});
