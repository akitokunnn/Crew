"use strict";
(() => {
  // Workspace data layer: one snapshot, then incremental changes by sequence (docs/EVENT_STREAM_DESIGN.md).
  // The shell (app.js) owns `state`; this module reads it through ctx getters and hands back each new value.
  const text = value => value === null || value === undefined ? "" : String(value);
  const byText = (a, b) => text(a) < text(b) ? -1 : text(a) > text(b) ? 1 : 0;
  // Mirrors change_feed.KINDS (all but `snapshot`, which the server turns into snapshot-required): where each
  // kind lives in the snapshot. Every change carries the entity's current value. Three shapes:
  //   list  - entities in a snapshot list, replaced or removed by key, then reordered like the server orders them;
  //   field - one snapshot field replaced as a whole;
  //   part  - the named fields of one snapshot object (null = the snapshot itself); other fields there belong to
  //           sources that are not in the feed yet and keep their value;
  //   page  - entities the snapshot does not hold; the view that shows them takes them from the change list.
  const KINDS = {
    todo: {list:"todos", key:"id", order:(a, b) => Number(!a.blocking) - Number(!b.blocking) || byText(a.created_at, b.created_at) || a.id - b.id,
      derive(next) { next.todo_count = next.todos.length; }},
    project: {list:"projects", key:"name", order:(a, b) => byText(a.name, b.name)},
    decision: {list:"decisions", key:"id", order:(a, b) => a.id - b.id},
    goal: {list:"goals", key:"id", order:(a, b) => b.priority - a.priority || byText(a.created_at, b.created_at) || byText(a.id, b.id)},
    workspace: {field:"project"},
    ai: {field:"ai"},
    domains: {field:"domains"},
    channel: {part:"channel", keys:["unread", "latest_id", "digest_id"]},
    channel_message: {page:"channel"},
    war_room: {page:"war-room"},
    sound_settings: {part:"sounds", keys:["preferences", "revision", "catalog", "missing"]},
    limits: {part:null, keys:["limits", "usage_scanned_at", "usage_error"]},
    conversations: {part:null, keys:["leaders", "members", "attention", "native_approvals", "git_assistant", "git_branches", "candidates"]},
    channel_digest: {part:"channel", keys:["digesting", "digest_error"]},
    sound_events: {part:"sounds", keys:["events"]},
    runtimes: {field:"runtimes"},
    notification: {field:"notification"},
    scanner: {part:null, keys:["agents_error", "vscode_error"]},
  };
  const RECONNECT_DELAYS = [1000, 2000, 5000, 10000];
  // A change this page cannot merge means the page is older than the server; only a reload helps.
  class UnknownChange extends Error {}

  function merge(base, changes) {
    const next = {...base}, lists = new Set();
    for (const change of changes) {
      const [name, op] = change.type.split(".");
      const kind = Object.hasOwn(KINDS, name) ? KINDS[name] : undefined;
      if (kind === undefined || (op !== "updated" && op !== "removed")) throw new UnknownChange(`页面不认识的增量事件：${change.type}，请刷新页面`);
      if (kind.page !== undefined) continue;
      if (kind.list !== undefined) {
        if (!lists.has(kind)) { next[kind.list] = [...base[kind.list]]; lists.add(kind); }
        const list = next[kind.list], index = list.findIndex(item => String(item[kind.key]) === change.entity);
        if (op === "removed") { if (index >= 0) list.splice(index, 1); }
        else if (index >= 0) list[index] = change.data;
        else list.push(change.data);
        continue;
      }
      // A field or a part is never removed; the server only sends its new value.
      if (op === "removed") throw new UnknownChange(`页面不认识的增量事件：${change.type}，请刷新页面`);
      if (kind.field !== undefined) { next[kind.field] = change.data; continue; }
      const keys = Object.keys(change.data);
      if (keys.length !== kind.keys.length || !kind.keys.every(key => keys.includes(key))) throw new UnknownChange(`增量事件 ${change.type} 的字段与页面不一致，请刷新页面`);
      if (kind.part === null) Object.assign(next, change.data);
      else next[kind.part] = {...next[kind.part], ...change.data};
    }
    for (const kind of lists) { next[kind.list].sort(kind.order); if (kind.derive) kind.derive(next); }
    return next;
  }

  function create(ctx) {
    let seq = null, stream = null, retryTimer = null, attempts = 0, pending = [], catchingUp = false;
    let busy = false, settled = Promise.resolve(), status = "idle";

    function queryPath(path) { const url = new URL(path, ctx.origin); if (ctx.project !== null && url.pathname !== "/api/settings") url.searchParams.set("project", ctx.project); return url.pathname + url.search; }
    // One request per call. Nothing here repeats a request, so a write is never sent twice by a reconnect.
    async function request(path, data) {
      const response = await ctx.fetch(queryPath(path), data === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json", "X-Crew":"1"}, body:JSON.stringify(data)});
      const result = await response.json();
      if (!response.ok) { const error = new Error(result.error || "请求失败，请重试。"); error.status = response.status; error.details = result; throw error; }
      return result;
    }
    function setStatus(value, detail = null) { status = value; ctx.onStatus(value, detail); }
    function closeStream() { if (stream !== null) { stream.close(); stream = null; } pending = []; catchingUp = false; }
    function cancelRetry() { if (retryTimer !== null) { ctx.clearTimeout(retryTimer); retryTimer = null; } }
    function scheduleRetry(action) {
      cancelRetry();
      const delay = RECONNECT_DELAYS[Math.min(attempts, RECONNECT_DELAYS.length - 1)];
      attempts += 1;
      retryTimer = ctx.setTimeout(() => { retryTimer = null; action(); }, delay);
    }
    function fail(error) { closeStream(); cancelRetry(); setStatus("failed", error.message); ctx.onError(error); }
    function applyChanges(changes, catchUp) {
      const fresh = changes.filter(change => change.seq > seq);
      if (fresh.length === 0) return;
      const previous = ctx.state, next = merge(previous, fresh);
      seq = fresh[fresh.length - 1].seq;
      ctx.onState(next, previous, {source:"change", catchUp, changes:fresh});
    }
    function subscribe() {
      closeStream();
      setStatus("connecting");
      const source = new ctx.EventSource(queryPath(`/api/events?after=${seq}`));
      stream = source; catchingUp = true;
      source.onmessage = event => {
        if (stream !== source) return;
        const change = JSON.parse(event.data);
        // Changes committed before the subscription are delivered in one batch once the server says ready.
        if (catchingUp) { pending.push(change); return; }
        try { applyChanges([change], false); } catch (error) { if (!(error instanceof UnknownChange)) throw error; fail(error); }
      };
      source.addEventListener("ready", () => {
        if (stream !== source) return;
        const backlog = pending; pending = []; catchingUp = false; attempts = 0;
        try { applyChanges(backlog, true); } catch (error) { if (!(error instanceof UnknownChange)) throw error; fail(error); return; }
        setStatus("live");
      });
      source.addEventListener("snapshot-required", event => {
        if (stream !== source) return;
        resyncLater(JSON.parse(event.data).reason);
      });
      source.onerror = () => {
        if (stream !== source) return;
        // Resume from the last applied sequence; the snapshot is only taken again if the server asks for it.
        closeStream();
        setStatus("disconnected");
        scheduleRetry(subscribe);
      };
    }
    // Snapshot first, then exactly one subscription from the snapshot's sequence.
    async function resync(reason) {
      closeStream(); cancelRetry();
      setStatus(seq === null ? "connecting" : "resyncing", reason);
      let snapshot;
      try { snapshot = await ctx.api("/api/snapshot"); }
      catch (error) { setStatus("disconnected", error.message); throw error; }
      const previous = ctx.state;
      seq = snapshot.seq; attempts = 0;
      ctx.onState(snapshot.data, previous, {source:"snapshot", reason, catchUp:false, changes:[]});
      subscribe();
    }
    // Without a caller to tell, a failed snapshot is reported and taken again after a back-off.
    function resyncLater(reason) {
      resync(reason).catch(error => { ctx.onError(error); scheduleRetry(() => resyncLater(reason)); });
    }
    // Same contract as the former polling loadState: concurrent calls share one load, `fresh` asks for one more.
    // A failure goes to the caller, and the data layer keeps trying so the page recovers on its own.
    async function loadState(fresh = false) {
      if (busy) { await settled; if (fresh) return loadState(true); return; }
      busy = true;
      let complete;
      settled = new Promise(resolve => { complete = resolve; });
      try { await resync("load"); }
      catch (error) { scheduleRetry(() => resyncLater("load")); throw error; }
      finally { busy = false; complete(); }
    }
    function stop() { closeStream(); cancelRetry(); setStatus("idle"); }
    return {queryPath, request, loadState, stop, get seq() { return seq; }, get status() { return status; }};
  }

  window.CrewAppData = {create, merge, KINDS};
})();
