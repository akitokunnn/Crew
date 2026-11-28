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
  const core = CrewCanvas, calls = [], savedCameras = [];
  const current = {camera:{x:30, y:-10, z:2}, wheel:false, edit:true};
  const clamp = z => Math.max(.2, Math.min(4, z));
  const controls = core.attach({surface, clamp, camera:() => current.camera,
    setCamera:(camera, save) => {
      current.camera = camera;
      calls.push(["camera", save]);
      if (save) savedCameras.push(camera);
    },
    wheelZoom:() => current.wheel, hit:event => ({type:event.hit}), editable:() => current.edit, forcePan:() => false,
    start() {}, preview:active => calls.push(["preview", active.type]), commit:active => calls.push(["commit", active.type]),
    cancel:active => calls.push(["cancel", active.type]), menu:() => calls.push(["menu"]), settled() {}});
  return {current, core, controls, calls, savedCameras, surface, win};
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

for (const [label, wheel, expected] of [
  ["vertical pixels", {deltaY:40}, {x:30, y:-50, z:2}],
  ["horizontal pixels", {deltaX:-24}, {x:54, y:-10, z:2}],
  ["diagonal pixels", {deltaX:12, deltaY:-28}, {x:18, y:18, z:2}],
  ["lines", {deltaX:-1, deltaY:2, deltaMode:1}, {x:46, y:-42, z:2}],
  ["pages", {deltaX:1, deltaY:-2, deltaMode:2}, {x:-370, y:790, z:2}],
]) {
  test(`unmodified wheel pans with zoom disabled: ${label}`, () => {
    const {controls, current, surface} = fixture();
    surface.fire("wheel", wheel);
    assert.deepEqual(current.camera, expected);
    controls.destroy();
  });
}

test("Ctrl+wheel keeps the same world point under the pointer", () => {
  const {controls, current, surface} = fixture();
  const pointer = {x:162, y:126};
  const world = controls.toWorld(pointer.x, pointer.y);
  surface.fire("wheel", {ctrlKey:true, deltaY:-30, clientX:pointer.x, clientY:pointer.y});
  assert(current.camera.z > 2);
  const after = controls.toWorld(pointer.x, pointer.y);
  assert(Math.abs(after.x - world.x) < 1e-9);
  assert(Math.abs(after.y - world.y) < 1e-9);
  controls.destroy();
});

test("wheel inside a native scroll area leaves default scrolling and the camera alone", () => {
  const {controls, current, calls, surface} = fixture();
  const nativeScrollArea = {};
  surface.fire("wheel", {
    deltaY:90, ctrlKey:true,
    target:{closest:selector => selector === "[data-canvas-wheel-native]" ? nativeScrollArea : null},
    preventDefault:() => assert.fail("native wheel default was prevented"),
  });
  assert.deepEqual(current.camera, {x:30, y:-10, z:2});
  assert.deepEqual(calls, []);
  controls.destroy();
});

test("dragging one touch across blank space saves the final camera on pointerup", () => {
  const {controls, current, savedCameras, surface, win} = fixture();
  surface.fire("pointerdown", {pointerType:"touch"});
  win.fire("pointermove", {pointerType:"touch", clientX:122, clientY:36});
  const finalCamera = {x:52, y:-44, z:2};
  assert.deepEqual(current.camera, finalCamera);
  assert.deepEqual(savedCameras, []);
  win.fire("pointerup", {pointerType:"touch", clientX:122, clientY:36});
  assert.deepEqual(savedCameras, [finalCamera]);
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
  let defaultPrevented = false, propagationStopped = false;
  surface.fire("click", {detail:1,
    preventDefault:() => { defaultPrevented = true; },
    stopImmediatePropagation:() => { propagationStopped = true; },
  });
  assert(defaultPrevented);
  assert(propagationStopped);
  assert.deepEqual(calls, [["camera", false], ["camera", true]]);
  assert.equal(controls.busy(), false);
  controls.destroy();
});

for (const interruption of ["pointercancel", "lostpointercapture"]) {
  test(`${interruption} restores the camera at pinch start without saving or committing`, () => {
    const {controls, current, calls, savedCameras, surface, win} = fixture();
    const first = {pointerType:"touch", pointerId:1, clientX:80};
    const second = {pointerType:"touch", pointerId:2, clientX:130};
    surface.fire("pointerdown", first);
    surface.fire("pointerdown", second);
    calls.length = 0;
    win.fire("pointermove", {...second, clientX:155});
    assert.equal(current.camera.z, 3);
    const eventTarget = interruption === "pointercancel" ? win : surface;
    eventTarget.fire(interruption, second);
    win.fire("pointerup", first);
    win.fire("pointerup", second);
    assert.deepEqual(current.camera, {x:30, y:-10, z:2});
    assert.deepEqual(savedCameras, []);
    assert.deepEqual(calls, [["camera", false], ["camera", false]]);
    assert.equal(controls.busy(), false);
    controls.destroy();
  });
}

test("adding, moving and releasing a third touch does not change the original pinch", () => {
  const {controls, current, savedCameras, surface, win} = fixture();
  const first = {pointerType:"touch", pointerId:1, clientX:80};
  const second = {pointerType:"touch", pointerId:2, clientX:130};
  const third = {pointerType:"touch", pointerId:3, clientX:210, clientY:125};
  surface.fire("pointerdown", first);
  surface.fire("pointerdown", second);
  const midpoint = controls.toWorld(105, 70);
  win.fire("pointermove", {...second, clientX:140});
  const beforeThird = current.camera;
  surface.fire("pointerdown", third);
  win.fire("pointermove", {...third, clientX:260, clientY:150});
  assert.deepEqual(current.camera, beforeThird);
  win.fire("pointerup", third);
  surface.fire("lostpointercapture", third);
  assert.equal(controls.busy(), true);
  assert.deepEqual(savedCameras, []);
  win.fire("pointermove", {...second, clientX:155});
  assert.equal(current.camera.z, 3);
  assert.deepEqual(controls.toScreen(midpoint.x, midpoint.y), {x:117.5, y:70});
  win.fire("pointerup", second);
  win.fire("pointerup", first);
  assert.deepEqual(savedCameras, [current.camera]);
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
