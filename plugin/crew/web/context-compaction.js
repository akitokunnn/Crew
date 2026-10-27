"use strict";
// One lifecycle for sidebar, cards and headers, independent of DOM rendering.
(() => {
  function create(options) {
    const pending = new Map(); let destroyed = false;
    function notify(item, message) { if (!destroyed) options.notify(`「${item.name}」：${message}`); }
    function changed() { if (!destroyed) options.changed(); }
    const phase = id => pending.get(id)?.phase || null;
    async function compact(id, {afterTurn = true} = {}) {
      if (destroyed) throw Error("Context compaction module is disposed");
      const item = options.findSession(id);
      if (!item || pending.has(id)) return;
      if (item.where === "vscode") { options.notify(`「${item.name}」正在原工具中运行，请在那边输入 /compact。`); return; }
      const operation = {phase:"submitting", unsubscribe:null, finished:false, closed:false};
      pending.set(id, operation); changed();
      const close = () => { if (operation.unsubscribe && !operation.closed) { operation.closed = true; operation.unsubscribe(); } };
      const finish = event => {
        if (pending.get(id) !== operation) return;
        operation.finished = true; close(); pending.delete(id); changed();
        if (event.type !== "compact") notify(item, event.text || event.reason || "会话已离线，压缩未完成");
      };
      try {
        operation.unsubscribe = await options.subscribe(id, {onFinish:finish, notify:message => notify(item,message)});
        if (destroyed || operation.finished) close();
        if (destroyed) return;
        // A completion/cancellation observed while establishing the stream is
        // already terminal for this click. Do not launch another native action.
        if (operation.finished) { await options.refresh(); return; }
        const receipt = await options.request(id, {afterTurn});
        if (destroyed) return;
        if (pending.get(id) === operation) {
          operation.phase = receipt.queued ? "queued" : "compacting"; changed();
          notify(item, receipt.message || "正在压缩上下文…");
        }
      } catch (error) {
        close();
        if (pending.get(id) === operation) { pending.delete(id); changed(); }
        if (!destroyed) throw error;
        return;
      }
      await options.refresh();
    }
    const unregister = options.actions?.register("context-compaction", {"context-compact":({element}) => compact(element.dataset.id)});
    return {phase, compact, pendingIds:() => [...pending.keys()],
      destroy() { destroyed = true; unregister?.(); for (const operation of pending.values()) { if (operation.unsubscribe && !operation.closed) { operation.closed = true; operation.unsubscribe(); } } pending.clear(); }};
  }
  const exported = {create};
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  else window.CrewContextCompaction = exported;
})();
