"use strict";
// Ordering is a transaction over stable leader IDs. DOM drag feedback and the
// menu/keyboard operations use the same transaction, including undo and cleanup.
(() => {
  function create(options) {
    const doc = options.document || document;
    const sidebar = options.sidebar;
    let drag = null, saving = false, destroyed = false;
    const leaders = options.leaders;
    const priority = () => leaders().filter(item => item.priority !== null).sort((a,b) => a.priority-b.priority);
    const ids = () => priority().map(item => item.session_id);
    const sortable = item => item.priority === null ? leaders().filter(person => person.priority === null) : priority();
    const find = id => leaders().find(item => item.session_id === id);

    async function transaction(run) {
      if (destroyed || saving) return;
      saving = true;
      try { await run(); }
      finally {
        saving = false;
        if (!destroyed) await options.refresh();
      }
    }
    async function savePriority(ranks, before = ids()) {
      const project = options.project();
      const evicted = before.find(id => !ranks.includes(id));
      const inserted = ranks.some(id => !before.includes(id));
      const name = find(evicted)?.name;
      return transaction(async () => {
        await options.savePriority({project, ranks:[...ranks]});
        if (!destroyed && inserted && evicted !== undefined) {
          options.notify(`${name} 已移出优先`, async () => {
            await options.savePriority({project, ranks:[...before]});
            if (!destroyed) { await options.refresh(); options.notify("已撤销"); }
          });
        }
      });
    }
    function saveNormalOrder(next) {
      const normal = [...next];
      const order = leaders().map(item => item.priority === null ? normal.shift() : item.session_id);
      const project = options.project();
      return transaction(() => options.saveOrder({project,order}));
    }
    function setPriority(id, rank) {
      const before = ids(), ranks = before.filter(sid => sid !== id);
      if (rank !== 0) ranks.splice(rank-1,0,id);
      return savePriority(ranks.slice(0,3), before);
    }
    function move(id, offset) {
      const item = find(id);
      if (!item) return;
      const order = sortable(item).map(person => person.session_id);
      const before = [...order], index = order.indexOf(id), other = index+offset;
      if (other < 0 || other >= order.length) return;
      [order[index],order[other]] = [order[other],order[index]];
      return item.priority === null ? saveNormalOrder(order) : savePriority(order,before);
    }
    function clearDrop() {
      sidebar.querySelectorAll(".drop-before,.drop-after,.drop-zone").forEach(item => item.classList.remove("drop-before","drop-after","drop-zone"));
    }
    function finishDrag() {
      drag?.classList.remove("dragging");
      drag = null;
      sidebar.classList.remove("leader-drag-active");
      clearDrop();
    }
    function dragstart(event) {
      const item = event.target.closest('#sidebar [data-act="leader"]');
      if (!item) return;
      if (saving || destroyed) { event.preventDefault(); return; }
      drag = item;
      item.classList.add("dragging");
      (options.requestAnimationFrame || requestAnimationFrame)(() => {
        if (drag && !destroyed) sidebar.classList.add("leader-drag-active");
      });
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain",item.dataset.id);
    }
    function dragover(event) {
      if (!drag) return;
      clearDrop();
      const zone = event.target.closest('#sidebar [data-leader-zone]');
      if (!zone) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      zone.classList.add("drop-zone");
      const item = event.target.closest('[data-act="leader"]');
      if (item && item !== drag) item.classList.add(event.clientY < item.getBoundingClientRect().top + item.offsetHeight/2 ? "drop-before" : "drop-after");
    }
    function drop(event) {
      if (!drag) return;
      const zone = event.target.closest('#sidebar [data-leader-zone]');
      if (!zone) return;
      event.preventDefault();
      const id = drag.dataset.id, source = drag.closest('[data-leader-zone]').dataset.leaderZone;
      const target = zone.dataset.leaderZone, item = event.target.closest('[data-act="leader"]');
      if (item === drag) { finishDrag(); options.changed(); return; }
      const before = ids(), after = item !== null && item.classList.contains("drop-after");
      const next = [...zone.querySelectorAll('[data-act="leader"]')].map(item => item.dataset.id).filter(sid => sid !== id);
      const index = item === null ? next.length : next.indexOf(item.dataset.id) + (after ? 1 : 0);
      next.splice(index,0,id);
      finishDrag();
      const request = target === "priority" ? savePriority(next.slice(0,3),before)
        : source === "priority" ? savePriority(before.filter(sid => sid !== id),before) : saveNormalOrder(next);
      Promise.resolve(request).catch(error => { if (!destroyed) options.notify(error.message); });
    }
    function dragend() { if (drag) { finishDrag(); options.changed(); } }
    const handlers = {dragstart,dragover,drop,dragend};
    for (const [type,handler] of Object.entries(handlers)) doc.addEventListener(type,handler);
    const unregister = options.actions?.register("leader-order",{
      "set-priority":({element}) => setPriority(element.dataset.id,Number(element.dataset.rank)),
      "leader-up":({element}) => move(element.dataset.id,-1),
      "leader-down":({element}) => move(element.dataset.id,1)
    });
    return {priority,ids,sortable,setPriority,move,saveNormalOrder,savePriority,
      isSaving:() => saving,isInteracting:() => saving||drag!==null,
      destroy() {
        destroyed = true; finishDrag(); unregister?.();
        for (const [type,handler] of Object.entries(handlers)) doc.removeEventListener(type,handler);
      }};
  }
  const exported = {create};
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  else window.CrewLeaderOrder = exported;
})();
