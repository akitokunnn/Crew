"use strict";

(function (root) {
  const THRESHOLD = 5;
  const point = event => ({x:event.clientX, y:event.clientY});
  const midpoint = ([a, b]) => ({x:(a.x + b.x) / 2, y:(a.y + b.y) / 2});
  const distance = ([a, b]) => Math.hypot(a.x - b.x, a.y - b.y);
  const toWorld = (camera, rect, p) => ({x:(p.x - rect.left - camera.x) / camera.z, y:(p.y - rect.top - camera.y) / camera.z});
  const toScreen = (camera, rect, p) => ({x:rect.left + camera.x + p.x * camera.z, y:rect.top + camera.y + p.y * camera.z});
  const anchor = (rect, world, screen, z) => ({x:screen.x - rect.left - world.x * z, y:screen.y - rect.top - world.y * z, z});
  const transform = ({x, y, z}) => `translate(${x}px,${y}px) scale(${z})`;

  function fit(rect, box, padding, clamp, maxZoom = 1) {
    const {left, top, right, bottom} = padding;
    const width = rect.width - left - right, height = rect.height - top - bottom;
    const w = box.x1 - box.x0, h = box.y1 - box.y0;
    const z = clamp(Math.min(maxZoom, width / Math.max(1, w), height / Math.max(1, h)));
    return {z, x:left + (width - w * z) / 2 - box.x0 * z, y:top + (height - h * z) / 2 - box.y0 * z};
  }

  function attach({surface, input = surface, camera, setCamera, clamp, wheelZoom, hit, editable, forcePan, start,
                   preview, commit, cancel:rollback, menu, settled}) {
    const pointers = new Map();
    let active = null, pinch = null, suppressClick = false;
    const rect = () => surface.getBoundingClientRect();

    function zoomAt(z, x, y) {
      const screen = {x, y}, bounds = rect();
      setCamera(anchor(bounds, toWorld(camera(), bounds, screen), screen, clamp(z)), true);
    }
    function zoomCenter(factor) {
      const {left, top, width, height} = rect();
      zoomAt(camera().z * factor, left + width / 2, top + height / 2);
    }
    function release(id) {
      if (input.hasPointerCapture(id)) input.releasePointerCapture(id);
    }
    function cancelActive() {
      const done = active;
      active = null;
      if (done) {
        if (done.pan && done.moved) setCamera(done.camera, false);
        rollback(done);
      }
    }
    function cancel() {
      if (!active && !pinch && !pointers.size) return false;
      suppressClick = Boolean(active?.moved || pinch);
      const ids = new Set(pointers.keys());
      if (active) ids.add(active.id);
      cancelActive();
      if (pinch) setCamera(pinch.camera, false);
      pinch = null;
      pointers.clear();
      for (const id of ids) release(id);
      settled();
      return true;
    }
    function down(event) {
      if (!camera() || ![0, 1, 2].includes(event.button)) return;
      suppressClick = false;
      const touch = event.pointerType === "touch";
      if (touch && pointers.size) {
        pointers.set(event.pointerId, point(event));
        input.setPointerCapture(event.pointerId);
        if (pointers.size === 2) {
          const ids = [...pointers.keys()];
          input.setPointerCapture(ids[0]);
          cancelActive();
          const pair = [...pointers.values()];
          pinch = {ids, camera:camera(), world:toWorld(camera(), rect(), midpoint(pair)), distance:Math.max(1, distance(pair))};
        }
        return;
      }
      if (active || pinch) return;
      const target = hit(event);
      if (!target) return;
      const pan = event.button !== 0 || forcePan() || !editable()
        || (!target.type && event.pointerType !== "mouse");
      active = {...target, ...point(event), id:event.pointerId, button:event.button, pan, moved:false, camera:camera()};
      if (touch) pointers.set(event.pointerId, point(event));
      if (event.button !== 0) event.preventDefault();
      // Preserve the original target for native clicks and capture as soon as a drag starts.
      start(active, event);
    }
    function move(event) {
      if (pointers.has(event.pointerId)) pointers.set(event.pointerId, point(event));
      if (pinch) {
        if (!pinch.ids.includes(event.pointerId)) return;
        const pair = pinch.ids.map(id => pointers.get(id));
        setCamera(anchor(rect(), pinch.world, midpoint(pair), clamp(pinch.camera.z * distance(pair) / pinch.distance)), false);
        return;
      }
      if (active?.id !== event.pointerId) return;
      const dx = event.clientX - active.x, dy = event.clientY - active.y;
      if (!active.moved && Math.hypot(dx, dy) < THRESHOLD) return;
      const first = !active.moved;
      if (first) { input.setPointerCapture(event.pointerId); active.moved = true; }
      event.preventDefault();
      if (active.pan) setCamera({...active.camera, x:active.camera.x + dx, y:active.camera.y + dy}, false);
      else preview(active, event, dx, dy, first);
    }
    function up(event) {
      pointers.delete(event.pointerId);
      if (pinch) {
        if (!pinch.ids.includes(event.pointerId)) return;
        pinch = null;
        suppressClick = true;
        setCamera(camera(), true);
        settled();
        return;
      }
      if (active?.id !== event.pointerId) return;
      const done = active;
      active = null;
      if (done.moved) suppressClick = true;
      if (done.pan && done.moved) setCamera(camera(), true);
      else if (done.button === 2) menu(done, event);
      else if (done.button === 0) commit(done, event);
      settled();
    }
    function interrupted(event) {
      if (active?.id === event.pointerId || pointers.has(event.pointerId)) cancel();
    }
    function lostCapture(event) {
      if (event.target === input) interrupted(event);
    }
    function wheel(event) {
      if (!camera() || event.target.closest("[data-canvas-wheel-native]")) return;
      event.preventDefault();
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect().height : 1;
      const dx = event.deltaX * unit, dy = event.deltaY * unit;
      const modifier = event.ctrlKey || event.metaKey;
      if (modifier || (wheelZoom() && dx === 0)) {
        zoomAt(camera().z * Math.exp(-dy * (modifier ? .01 : .0015)), event.clientX, event.clientY);
      } else setCamera({...camera(), x:camera().x - dx, y:camera().y - dy}, true);
    }
    function contextmenu(event) {
      if (!event.target.closest("input,textarea,[contenteditable]")) event.preventDefault();
    }
    function click(event) {
      if (!suppressClick || event.detail === 0) return;
      suppressClick = false;
      event.stopImmediatePropagation();
      event.preventDefault();
    }
    const listeners = [[input, "pointerdown", down], [window, "pointermove", move], [window, "pointerup", up],
      [window, "pointercancel", interrupted], [input, "lostpointercapture", lostCapture],
      [surface, "wheel", wheel, {passive:false}], [surface, "contextmenu", contextmenu], [input, "click", click, true],
      [window, "blur", cancel]];
    for (const [element, type, listener, options] of listeners) element.addEventListener(type, listener, options);
    return {rect, zoomAt, zoomCenter, cancel, busy:() => Boolean(active || pinch),
      toWorld:(x, y) => toWorld(camera(), rect(), {x, y}),
      toScreen:(x, y) => toScreen(camera(), rect(), {x, y}),
      destroy() {
        cancel();
        for (const [element, type, listener, options] of listeners) element.removeEventListener(type, listener, options);
      }};
  }

  root.CrewCanvas = {attach, toWorld, toScreen, anchor, fit, transform};
})(globalThis);
