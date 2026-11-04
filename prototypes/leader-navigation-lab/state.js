"use strict";
// Navigation state for this lab only. A scope key is "<leaderId>:general" or "<leaderId>:<taskId>".
(() => {
  const D = window.LeaderLabData;
  const leaderById = new Map(D.leaders.map(item => [item.id, item]));
  const taskById = new Map(D.tasks.map(item => [item.id, item]));
  const ENDED = new Set(["completed", "failed", "cancelled"]);

  const keyOf = (leaderId, taskId) => `${leaderId}:${taskId || "general"}`;
  function parse(key) {
    const [leaderId, rest] = key.split(":");
    return {leaderId, taskId: rest === "general" ? null : rest};
  }
  const leader = id => leaderById.get(id);
  const task = id => taskById.get(id);
  const tasksOf = leaderId => D.tasks.filter(item => item.leaderId === leaderId);
  const needsYou = item => Boolean(item.decision) || item.status === "waiting_user" || item.status === "awaiting_acceptance";
  const isEnded = item => ENDED.has(item.status);

  // Leaders in sidebar order: pinned by priority first, then the rest in data order.
  const orderedLeaders = () => [...D.leaders].sort((a, b) => (a.priority ?? 99) - (b.priority ?? 99));

  function pendingOf(leaderId) {
    const own = tasksOf(leaderId).filter(needsYou).length;
    return own + (leader(leaderId).generalDecision ? 1 : 0);
  }
  const pendingTotal = () => D.leaders.reduce((sum, item) => sum + pendingOf(item.id), 0);

  // Filters used by the full task list; counts come from the same data.
  const FILTERS = [
    ["all", "全部", () => true],
    ["needs", "待你", needsYou],
    ["running", "推进中", item => !needsYou(item) && (item.status === "running" || item.status === "pausing")],
    ["queued", "待规划", item => item.status === "queued"],
    ["paused", "已暂停", item => !needsYou(item) && item.status === "paused"],
    ["ended", "已结束", isEnded]
  ];
  const filterFn = id => FILTERS.find(item => item[0] === id)[2];
  const statusRank = item => needsYou(item) ? 0 : item.status === "running" || item.status === "pausing" ? 1 : item.status === "queued" ? 2 : item.status === "paused" ? 3 : 4;
  const sortForList = list => [...list].sort((a, b) => statusRank(a) - statusRank(b) || (statusRank(a) === 0 ? b.waitMin - a.waitMin : a.updatedMin - b.updatedMin));

  const s = {
    leaderId: "mips",
    scopeKey: keyOf("mips", "mips-01"),
    view: "chat",              // chat | list | workbench | assistant
    lastScope: Object.fromEntries(D.leaders.map(item => [item.id, keyOf(item.id)])),
    recent: Object.fromEntries(D.leaders.map(item => [item.id, []])),
    peek: new Set(),           // leaders expanded with the chevron only
    currentFolded: false,      // the current leader folded by its own chevron
    drafts: {}, scroll: {}, messages: {}, modes: {},
    list: Object.fromEntries(D.leaders.map(item => [item.id, {query: "", filter: "all", scrollTop: 0}])),
    listLeader: null,
    returnTo: null,            // scope key to restore when leaving list / workbench
    openedFromList: null,      // leader id whose list opened the current chat
    sidebarCollapsed: false,
    mobileNav: null,           // null | "leaders" | leader id
    assistantDraft: "", assistantMessages: [...D.assistantSeed]
  };
  // A believable starting point: two recently opened MiPS tasks, audio last used in a task.
  s.lastScope.mips = s.scopeKey;
  s.recent.mips = ["mips-01", "mips-02", "mips-11"];
  s.lastScope.audio = keyOf("audio", "audio-02");
  s.recent.audio = ["audio-02"];

  function touchRecent(leaderId, taskId) {
    s.recent[leaderId] = [taskId, ...s.recent[leaderId].filter(id => id !== taskId)].slice(0, 4);
  }

  // Work set rows for one leader. Limits shrink on short windows.
  function workSet(leaderId, limits) {
    const current = s.leaderId === leaderId && s.view === "chat" ? parse(s.scopeKey).taskId : null;
    const waiting = tasksOf(leaderId).filter(needsYou).sort((a, b) => b.waitMin - a.waitMin);
    const shown = waiting.slice(0, limits.needs);
    const rows = shown.map(item => ({kind: "task", task: item}));
    const used = new Set(shown.map(item => item.id));
    if (current && !used.has(current)) { rows.push({kind: "task", task: task(current)}); used.add(current); }
    let recentAdded = 0;
    for (const id of s.recent[leaderId]) {
      if (recentAdded >= limits.recent) break;
      if (used.has(id)) continue;
      rows.push({kind: "task", task: task(id)}); used.add(id); recentAdded += 1;
    }
    const hiddenWaiting = waiting.filter(item => !used.has(item.id)).length;
    return {rows, hiddenWaiting, total: tasksOf(leaderId).length};
  }

  function openScope(key, {fromList = null} = {}) {
    const {leaderId, taskId} = parse(key);
    if (s.leaderId !== leaderId) { s.peek.delete(leaderId); s.currentFolded = false; }
    s.leaderId = leaderId;
    s.scopeKey = key;
    s.view = "chat";
    s.lastScope[leaderId] = key;
    s.openedFromList = fromList;
    if (!fromList) s.returnTo = null;
    if (taskId) touchRecent(leaderId, taskId);
  }

  // Selecting a leader returns to whatever conversation was last open in that group.
  function selectLeader(leaderId) { openScope(s.lastScope[leaderId]); }

  function togglePeek(leaderId) {
    if (s.leaderId === leaderId) s.currentFolded = !s.currentFolded;
    else if (s.peek.has(leaderId)) s.peek.delete(leaderId);
    else s.peek.add(leaderId);
  }
  const isExpanded = leaderId => s.leaderId === leaderId ? !s.currentFolded : s.peek.has(leaderId);

  function openList(leaderId, filter, {resume = false} = {}) {
    if (s.view === "chat" && !resume) s.returnTo = s.scopeKey;
    if (s.leaderId !== leaderId) { s.peek.delete(leaderId); s.currentFolded = false; }
    s.leaderId = leaderId;
    s.listLeader = leaderId;
    if (filter) { s.list[leaderId].filter = filter; s.list[leaderId].scrollTop = 0; }
    s.view = "list";
  }
  function openWorkbench() {
    if (s.view === "chat") s.returnTo = s.scopeKey;
    s.view = "workbench";
  }
  function goBack() {
    const key = s.returnTo || s.lastScope[s.leaderId];
    openScope(key);
  }

  function messagesOf(key) {
    if (!s.messages[key]) s.messages[key] = window.LeaderLabSeed(key);
    return s.messages[key];
  }

  // Explicit user actions. Navigation never reaches these.
  function send(key, text) {
    const {taskId} = parse(key);
    const item = taskId ? task(taskId) : null;
    const started = Boolean(item && item.status === "queued");
    messagesOf(key).push({role: "user", text});
    if (started) item.status = "running";
    messagesOf(key).push({role: "local", text: started ? "本地演示：任务已标记为推进中。消息没有发给 AI，也不会有真实回复。" : "本地演示：消息只保存在这个页面里，没有发给 AI。"});
    s.drafts[key] = "";
    s.scroll[key] = null;
    return started;
  }
  function start(key) {
    const item = task(parse(key).taskId);
    if (item.status !== "queued") throw new Error(`任务 ${item.id} 状态为 ${item.status}，不是待规划`);
    item.status = "running";
    messagesOf(key).push({role: "local", text: "本地演示：已开始推进。没有唤醒真实 AI。"});
    s.scroll[key] = null;
  }
  function decide(key, option) {
    const {leaderId, taskId} = parse(key);
    if (taskId) {
      const item = task(taskId);
      item.decision = null;
      if (item.status === "waiting_user") item.status = "running";
    } else {
      leader(leaderId).generalDecision = null;
    }
    messagesOf(key).push({role: "user", text: `拍板：${option}`});
    messagesOf(key).push({role: "local", text: "本地演示：选择已记录，待你计数已更新。没有发给 AI。"});
    s.scroll[key] = null;
  }
  function accept(key, passed) {
    const item = task(parse(key).taskId);
    item.status = passed ? "completed" : "running";
    item.acceptance = null;
    messagesOf(key).push({role: "user", text: passed ? "验收通过。" : "退回修改。"});
    messagesOf(key).push({role: "local", text: "本地演示：验收结果已记录。没有发给 AI。"});
    s.scroll[key] = null;
  }
  const modeOf = key => s.modes[key] || (parse(key).taskId ? task(parse(key).taskId).mode : "human");
  function setMode(key, mode) { s.modes[key] = mode; }

  window.LeaderLabState = {
    s, D, keyOf, parse, leader, task, tasksOf, needsYou, isEnded, orderedLeaders, pendingOf, pendingTotal,
    FILTERS, filterFn, sortForList, workSet, openScope, selectLeader, togglePeek, isExpanded,
    openList, openWorkbench, goBack, messagesOf, send, start, decide, accept, modeOf, setMode
  };
})();
