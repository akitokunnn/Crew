"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../frontend/architecture.js"), "utf8");
const comparison = fs.readFileSync(path.join(__dirname, "../../frontend/restructure-ui.js"), "utf8");
const settle = () => new Promise(resolve => setImmediate(resolve));

for (const end of ["pointerup", "pointercancel", "save"]) {
  test(`after ${end} the open drawer catches up to the last announced version once`, async () => {
    const hostEvents = {}, canvasEvents = {}, calls = [];
    const canvas = {focus() {}, dataset:{}, getBoundingClientRect:() => ({width:800,height:600}),
      addEventListener:(type, fn) => { canvasEvents[type] = fn; }};
    const drawer = {dataset:{}, querySelector:() => ({scrollTop:0})};
    const elements = {".arch-canvas":canvas, ".arch-history":drawer, ".arch-drop-label":{}, ".arch-ghost":{}};
    const host = {dataset:{}, contains:() => true, querySelectorAll:() => [],
      querySelector:selector => elements[selector], addEventListener:(type, fn) => { hostEvents[type] = fn; }};
    const media = {matches:false, addEventListener() {}};
    const ctx = vm.createContext({window:{matchMedia:() => media}, document:{activeElement:null, addEventListener() {}},
      requestAnimationFrame:fn => fn(), ResizeObserver:class { observe() {} }, localStorage:{getItem:() => null, setItem() {}}});
    vm.runInContext(comparison, ctx);
    const arch = vm.runInContext(source + "\nCrewArchitecture", ctx);
    let version = 1, finishSave;
    const board = () => ({parts:[], leaders:[], version, direction:"TB"});
    const settings = {project:"demo", state:{domains:{version}}, api:async (url, body) => {
      const op = url.split("/").pop().split("?")[0];
      calls.push([op, body?.number]);
      const meta = {number:version, note:`revision ${version}`, created_at:"2026-11-27"};
      if (op === "domains.get") return board();
      if (op === "domains.apply") return new Promise(resolve => { finishSave = () => resolve({board:board(), version, effects:[{}]}); });
      if (op === "domains.versions.list") return {versions:[meta], latest_matches:true, more:false};
      assert.equal(op, "domains.version.get");
      const removed = version === 1 ? [] : [{id:"vision", name:"视觉", parent_id:null}];
      return {version:{...meta, number:body.number, note:"初版"}, state:{parts:[], leaders:[]},
        current:{parts:removed, leaders:[]}, rollback:{nothing:!removed.length, gone:[], archived:[], unplaced:[]},
        diff:{parts:{added:[], removed, renamed:[], moved:[]}, leaders:{placed:[], moved:[], unplaced:[], lead_on:[], lead_off:[]}}};
    }};
    arch.mount(host, settings);
    await settle();
    const click = dataset => hostEvents.click({target:{closest:() => ({dataset})}});
    click({archAct:"history"}); await settle();
    click({archAct:"history-pick", number:"1"}); await settle();
    assert.match(drawer.innerHTML, /现在的架构和这个版本相同/);
    const pointer = {target:{closest:() => null}, button:1, pointerId:7, clientX:30, clientY:40, preventDefault() {}};
    if (end === "save") {
      click({archAct:"direction", direction:"LR"});
      await settle();
      assert.equal(calls.at(-1)[0], "domains.apply");
    } else canvasEvents.pointerdown(pointer);
    calls.length = 0;
    for (version of [2, 3]) arch.update({...settings, state:{domains:{version}}});
    assert.deepEqual(calls, []);
    if (end === "save") finishSave();
    else canvasEvents[end]({...pointer, type:end});
    await settle();
    assert.deepEqual(calls, [["domains.get", undefined], ["domains.versions.list", undefined], ["domains.version.get", 1]]);
    assert.match(drawer.innerHTML, /revision 3/);
    assert.match(drawer.innerHTML, /版本 1<\/b>/);
    assert.match(drawer.innerHTML, /回滚会：删除 1/);
    assert.match(drawer.innerHTML, /视觉/);
  });
}
