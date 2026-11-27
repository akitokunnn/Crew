"use strict";

const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../frontend/canvas-core.js"), "utf8");

function target() {
  const listeners = new Map(), captured = new Set();
  return {
    addEventListener(type, fn) { listeners.set(type, fn); },
    removeEventListener(type) { listeners.delete(type); },
    fire(type, props = {}) {
      const event = {type, target:this, pointerId:1, pointerType:"mouse", button:0, clientX:100, clientY:70,
        deltaX:0, deltaY:0, deltaMode:0, closest:() => null, preventDefault() {}, ...props};
      listeners.get(type)?.(event);
    },
    closest:() => null,
    getBoundingClientRect:() => ({left:10, top:20, width:500, height:400}),
    hasPointerCapture:id => captured.has(id),
    setPointerCapture:id => captured.add(id),
    releasePointerCapture:id => captured.delete(id)
  };
}

function fixture() {
  const win = target(), surface = target();
  globalThis.window = win;
  vm.runInThisContext(source);
  const core = CrewCanvas, calls = [];
  const current = {camera:{x:30, y:-10, z:2}, wheel:false, edit:true};
  const clamp = z => Math.max(.2, Math.min(4, z));
  const controls = core.attach({surface, clamp, camera:() => current.camera,
    setCamera:(camera, save) => { current.camera = camera; calls.push(["camera", save]); },
    wheelZoom:() => current.wheel, hit:event => ({type:event.hit}), editable:() => current.edit, forcePan:() => false,
    start() {}, preview:active => calls.push(["preview", active.type]), commit:active => calls.push(["commit", active.type]),
    cancel:active => calls.push(["cancel", active.type]), menu:() => calls.push(["menu"]), settled() {}});
  return {current, core, controls, calls, surface, win};
}

test("world and screen coordinates round-trip and anchored zoom keeps the point under the cursor", () => {
  const {controls, current} = fixture();
  const p = {x:-17, y:36};
  const screen = controls.toScreen(p.x, p.y);
  assert.deepEqual(controls.toWorld(screen.x, screen.y), p);
  controls.zoomAt(100, screen.x, screen.y);
  assert.equal(current.camera.z, 4);
  assert.deepEqual(controls.toScreen(p.x, p.y), screen);
  controls.destroy();
});

test("wheel pans by default, modifiers and enabled vertical wheels zoom", () => {
  const {current, controls, surface} = fixture();
  surface.fire("wheel", {deltaY:2, deltaMode:1});
  assert.deepEqual(current.camera, {x:30, y:-42, z:2});
  const anchor = controls.toWorld(100, 70);
  surface.fire("wheel", {deltaY:-35, metaKey:true});
  assert(current.camera.z > 2);
  assert.deepEqual(controls.toScreen(anchor.x, anchor.y), {x:100, y:70});
  current.wheel = true;
  const z = current.camera.z;
  surface.fire("wheel", {deltaX:2, deltaY:10});
  assert.equal(current.camera.z, z);
  surface.fire("wheel", {deltaY:80});
  assert(current.camera.z < z);
  controls.destroy();
});

test("right click below the threshold opens its menu; dragging pans and left mouse previews a frame", () => {
  const {calls, controls, surface, win, current} = fixture();
  surface.fire("pointerdown", {button:2});
  win.fire("pointermove", {clientX:104});
  win.fire("pointerup", {button:2});
  assert.deepEqual(calls.splice(0), [["menu"]]);
  surface.fire("pointerdown", {button:2});
  win.fire("pointermove", {clientX:140});
  assert(surface.hasPointerCapture(1));
  win.fire("pointerup", {button:2});
  assert.deepEqual(current.camera, {x:70, y:-10, z:2});
  assert.deepEqual(calls.splice(0), [["camera", false], ["camera", true]]);
  surface.fire("pointerdown", {hit:"marquee"});
  win.fire("pointermove", {clientX:150});
  win.fire("pointerup");
  assert.deepEqual(calls, [["preview", "marquee"], ["commit", "marquee"]]);
  controls.destroy();
});

test("second touch rolls back an active node drag then anchors the pinch at its moving midpoint", () => {
  const {calls, controls, surface, win, current} = fixture();
  surface.fire("pointerdown", {pointerType:"touch", hit:"node", clientX:80});
  win.fire("pointermove", {pointerType:"touch", clientX:90});
  const anchor = controls.toWorld(110, 70);
  surface.fire("pointerdown", {pointerType:"touch", pointerId:2, clientX:130});
  assert.deepEqual(calls.splice(0), [["preview", "node"], ["cancel", "node"]]);
  win.fire("pointermove", {pointerType:"touch", pointerId:2, clientX:150});
  assert.equal(current.camera.z, 3);
  assert.deepEqual(controls.toScreen(anchor.x, anchor.y), {x:120, y:70});
  win.fire("pointerup", {pointerType:"touch", pointerId:2});
  win.fire("pointerup", {pointerType:"touch"});
  assert.deepEqual(calls, [["camera", false], ["camera", true]]);
  assert.equal(controls.busy(), false);
  controls.destroy();
});

test("pointercancel restores a touch pan and discards a node drag without committing on later pointerup", () => {
  const {calls, controls, surface, win, current} = fixture();
  const initial = current.camera;
  surface.fire("pointerdown", {pointerType:"touch"});
  win.fire("pointermove", {pointerType:"touch", clientX:150});
  assert.notDeepEqual(current.camera, initial);
  win.fire("pointercancel", {pointerType:"touch"});
  assert.deepEqual(current.camera, initial);
  assert.equal(surface.hasPointerCapture(1), false);
  calls.length = 0;
  surface.fire("pointerdown", {pointerType:"touch", hit:"node"});
  win.fire("pointermove", {pointerType:"touch", clientX:150});
  win.fire("pointercancel", {pointerType:"touch"});
  win.fire("pointerup", {pointerType:"touch"});
  assert.deepEqual(calls, [["preview", "node"], ["cancel", "node"]]);
  assert.equal(controls.cancel(), false);
  controls.destroy();
});
