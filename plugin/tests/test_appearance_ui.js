"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict");
const {create} = require("../crew/web/appearance-ui.js"), {fixture} = require("./shell_ui_fixture.cjs"), {create:actions} = require("../crew/web/ui-actions.js");
const themes = ["sage", "blue"].map(id => ({id,name:id,light:{"--accent":"#abc"},dark:{"--accent":"#def"}}));
function setup(extra = {}) {
  const dom = fixture(), host = dom.element("section"), stored = new Map(), messages = [], themeCalls = [], previews = [], saved = [], changed = [];
  let muted = false, volume = .25, modeChanges = 0, renders = 0;
  const commands = actions({onError:error=>messages.push(error.message)});
  const options = {document:dom.doc, window:dom.win, host, actions:commands, icon:() => "icon", notify:message=>messages.push(message),
    storage:{getItem:key=>stored.get(key) ?? null,setItem:(key,value)=>stored.set(key,value)},
    renderer:{replace(node,html){renders++;node.innerHTML=html;}}, theme:{setMode:mode=>themeCalls.push(["mode",mode]),apply:(...args)=>themeCalls.push(["apply",...args])},
    sound:{getSettings:()=>({muted,volume}),setMuted:value=>{muted=value;},setVolume:value=>{volume=value;},preview:kind=>previews.push(kind)},
    saveProjectTheme:async(name,value)=>{saved.push([name,value]);return{theme:value};},onProjectTheme:(...args)=>changed.push(args),onModeChange:()=>modeChanges++, ...extra};
  const appearance = create(options);
  const dispatch = (action,dataset={}) => commands.dispatch(action,{element:{dataset}});
  return {...dom, host, stored, messages, themeCalls, previews, saved, changed, commands, appearance, dispatch, sound:()=>({muted,volume}), modeChanges:()=>modeChanges,renders:()=>renders};
}
test("appearance restores validated preferences and applies server project theme without persisting server state", () => {
  const values = {"crew.theme":"dark","crew.fontScale":"1.25"}, writes=[];
  const f = setup({storage:{getItem:key=>values[key] ?? null,setItem:(...args)=>writes.push(args)}});
  assert.equal(f.appearance.mode(),"dark"); assert.equal(f.doc.documentElement.style["--fs"],"1.25");
  f.appearance.sync({themes,project:{name:"project",theme:"sage"}});
  assert.deepEqual(f.themeCalls.at(-1),["apply",themes,"sage","dark"]); assert.match(f.host.innerHTML,/为这个项目保存/); assert.deepEqual(writes,[]);
  f.media.emit("change"); assert.deepEqual(f.themeCalls.at(-1),["apply",themes,"sage","dark"]);
  f.appearance.sync({themes,project:null}); assert.deepEqual(f.themeCalls.at(-1),["apply",themes,null,"dark"]); assert.doesNotMatch(f.host.innerHTML,/为这个项目保存/);
});
test("invalid stored modes/scales use defaults and read/write failures preserve chosen UI state", () => {
  for (const scale of [null,"", "NaN", "Infinity", ".89", "1.41"]) {
    const f = setup({storage:{getItem:key=>key==="crew.theme"?"unknown":scale,setItem(){}}});
    assert.equal(f.appearance.mode(),"system"); assert.equal(f.doc.documentElement.style["--fs"],"1"); f.appearance.destroy();
  }
  const f = setup({storage:{getItem(){throw Error("read denied");},setItem(){throw Error("write denied");}}});
  assert.deepEqual(f.messages,["read denied","read denied"]); f.dispatch("theme-select",{themeMode:"light"});
  assert.equal(f.appearance.mode(),"light"); assert.equal(f.messages.at(-1),"write denied"); assert.equal(f.modeChanges(),1);
  f.dispatch("theme-select",{themeMode:"invalid"}); assert.equal(f.appearance.mode(),"light");
});
test("range changes validate bounds, persist preferences and preserve the active slider during dragging", () => {
  const f = setup(), initialRenders = f.renders(), font = f.host.querySelector('[data-setting="font"]'), sound = f.host.querySelector('[data-setting="volume"]');
  for (const value of [.9,1.4]) { font.value=String(value);f.host.emit("input",{target:font});assert.equal(f.doc.documentElement.style["--fs"],String(value)); }
  assert.equal(f.host.querySelector("#font-scale-value").textContent,"140%"); assert.equal(f.stored.get("crew.fontScale"),"1.4");
  for (const value of [.89,1.41,NaN,Infinity]) { font.value=String(value);f.host.emit("input",{target:font});assert.equal(f.doc.documentElement.style["--fs"],"1.4"); }
  sound.value="0";f.host.emit("input",{target:sound});assert.equal(f.sound().volume,0);assert.equal(f.host.querySelector("#sound-volume-value").textContent,"0%");
  sound.value="1";f.host.emit("input",{target:sound});assert.equal(f.sound().volume,1);
  for (const value of [-1,1.01,NaN]) { sound.value=String(value);f.host.emit("input",{target:sound});assert.equal(f.sound().volume,1); }
  assert.equal(f.renders(),initialRenders); assert.equal(f.host.querySelector('[data-setting="font"]'),font);
  f.dispatch("font-reset");assert.equal(f.doc.documentElement.style["--fs"],"1");assert.equal(f.host.querySelector('[data-act="font-reset"]').disabled,true);
});
test("mode cycling and sound settings remain independent of playback notification policy", () => {
  const f = setup(); for (const mode of ["light","dark","system"]) {f.dispatch("theme");assert.equal(f.appearance.mode(),mode);assert.equal(f.stored.get("crew.theme"),mode);}
  f.dispatch("sound-mute");assert.equal(f.sound().muted,true);assert.equal(f.host.querySelector('[data-act="sound-test"]').disabled,true);
  f.dispatch("sound-mute");f.dispatch("sound-test");assert.deepEqual(f.previews,["decision"]);
});
test("project preference saves reject unknown themes, prevent duplicate writes, and ignore replies after scope change or destruction", async () => {
  const pending = [], f = setup({saveProjectTheme:(name,theme)=>new Promise(resolve=>pending.push({name,theme,resolve}))});
  f.appearance.sync({themes,project:{name:"a",theme:"sage"}}); f.dispatch("project-theme",{themeId:"unknown"});assert.equal(pending.length,0);
  f.dispatch("project-theme",{themeId:"blue"});f.dispatch("project-theme",{themeId:"blue"});assert.equal(pending.length,1);
  f.appearance.sync({themes,project:{name:"b",theme:"sage"}});pending[0].resolve({theme:"blue"});await new Promise(setImmediate);assert.deepEqual(f.changed,[]);
  f.dispatch("project-theme",{themeId:"blue"});pending[1].resolve({theme:"blue"});await new Promise(setImmediate);assert.deepEqual(f.changed,[["b","blue"]]);
  f.dispatch("project-theme",{themeId:"sage"});f.appearance.destroy();pending[2].resolve({theme:"sage"});await new Promise(setImmediate);
  assert.deepEqual(f.changed,[["b","blue"]]);assert.equal(f.host.listenerCount(),0);assert.equal(f.media.listenerCount(),0);assert.deepEqual(f.commands.owners(),[]);
});
