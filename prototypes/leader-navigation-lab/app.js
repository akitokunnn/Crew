"use strict";
// Rendering and interaction for the multi-leader navigation lab. Offline, no AI calls.
(() => {
  const L = window.LeaderLabState;
  const {s, D} = L;
  const root = document.getElementById("app");
  const LOGO = "../../plugin/crew/web/logo.svg";

  // Icon paths copied from plugin/crew/web/app.js so the lab speaks the product's icon language.
  const paths = {
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    x: '<path d="m6 6 12 12M6 18 18 6"/>', right: '<path d="m9 5 7 7-7 7"/>', down: '<path d="m5 9 7 7 7-7"/>',
    left: '<path d="m15 5-7 7 7 7"/>', check: '<path d="m5 12 4 4L19 6"/>',
    file: '<path d="M13 2H5v20h14V8l-6-6Zm0 0v6h6"/>',
    msg: '<path d="M21 15a3 3 0 0 1-3 3H9l-6 4V6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3v9Z"/>',
    send: '<path d="m5 12-2-9 18 9-18 9 2-9Zm0 0h16"/>',
    leave: '<path d="M9 3H3v18h6m6-14 5 5-5 5M8 12h12"/>',
    panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    list: '<path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/>',
    keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>'
  };
  const ic = (name, cls = "") => `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${paths[name]}</svg>`;
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[ch]);
  const ago = min => min < 60 ? `${min} 分钟前` : min < 1440 ? `${Math.floor(min / 60)} 小时前` : `${Math.floor(min / 1440)} 天前`;
  const shortWait = min => min < 60 ? `${min} 分` : `${Math.floor(min / 60)} 时`;
  const isMobile = () => matchMedia("(max-width: 640px)").matches;
  const limits = () => innerHeight <= 700 ? {needs: 2, recent: 1} : {needs: 4, recent: 2};
  const MODES = {human: ["由我拍板", "遇到关键取舍时，在对话里等我决定。"], auto: ["自主推进", "leader 自行取舍，事后在对话里汇报。"]};
  const ui = {overlay: null, palQuery: "", palIndex: 0, toastTimer: null, sbScroll: 0, mnavRet: null};

  // What a task needs from the user, in one short phrase.
  function needWord(item) {
    if (item.decision) return "待你拍板";
    if (item.status === "awaiting_acceptance") return "待验收";
    return D.labels[item.status];
  }
  const scopeTitle = key => { const {taskId} = L.parse(key); return taskId ? L.task(taskId).title : "总会话"; };
  const otherPending = () => L.pendingTotal() - scopePending(s.scopeKey);
  function scopePending(key) {
    const {leaderId, taskId} = L.parse(key);
    return taskId ? (L.needsYou(L.task(taskId)) ? 1 : 0) : (L.leader(leaderId).generalDecision ? 1 : 0);
  }
  const highlight = (text, q) => {
    if (!q) return esc(text);
    const at = text.toLowerCase().indexOf(q.toLowerCase());
    return at < 0 ? esc(text) : `${esc(text.slice(0, at))}<mark>${esc(text.slice(at, at + q.length))}</mark>${esc(text.slice(at + q.length))}`;
  };

  // ---------- sidebar ----------
  function mark(item) {
    if (L.needsYou(item)) return '<i class="mark need" aria-hidden="true"></i>';
    if (item.status === "running" || item.status === "pausing") return '<i class="mark run" aria-hidden="true"></i>';
    return '<i class="mark" aria-hidden="true"></i>';
  }
  function scopeRow(key, cls = "ws-row") {
    const {leaderId, taskId} = L.parse(key);
    const current = s.view === "chat" && s.scopeKey === key;
    const item = taskId ? L.task(taskId) : null;
    const need = item ? L.needsYou(item) : Boolean(L.leader(leaderId).generalDecision);
    const label = item ? `${item.title}，${needWord(item)}` : `总会话${need ? "，有待你拍板的事" : ""}`;
    const glyph = item ? mark(item) : `<i class="mark ${need ? "need" : ""}" aria-hidden="true"></i>`;
    return `<li><button class="${cls}" data-act="scope" data-key="${esc(key)}" title="${esc(item ? `${item.title} · ${needWord(item)}${item.waitMin && L.needsYou(item) ? ` · 等了 ${ago(item.waitMin).replace("前", "")}` : ""}` : "总会话")}" aria-label="${esc(label)}" ${current ? 'aria-current="page"' : ""}>${glyph}<span class="t">${esc(item ? item.title : "总会话")}</span></button></li>`;
  }
  function workRows(leaderId, cls) {
    const set = L.workSet(leaderId, limits());
    return [
      scopeRow(L.keyOf(leaderId), cls),
      ...set.rows.map(row => scopeRow(L.keyOf(leaderId, row.task.id), cls)),
      set.hiddenWaiting ? `<li><button class="${cls} more" data-act="list" data-leader="${leaderId}" data-filter="needs"><i class="mark" aria-hidden="true"></i><span class="t">另 ${set.hiddenWaiting} 项待你</span>${ic("right", "sm")}</button></li>` : "",
      `<li><button class="${cls} all ${s.view === "list" && s.listLeader === leaderId ? "is-on" : ""}" data-act="list" data-leader="${leaderId}" aria-label="全部任务，共 ${set.total} 项" ${s.view === "list" && s.listLeader === leaderId ? 'aria-current="page"' : ""}><i class="mark" aria-hidden="true"></i><span class="t">全部任务</span><span class="n">${set.total}</span>${ic("right", "sm")}</button></li>`
    ].join("");
  }
  function leaderGroup(leader, pinEnd) {
    const open = L.isExpanded(leader.id), current = s.leaderId === leader.id, pending = L.pendingOf(leader.id);
    const label = `进入「${leader.name}」${leader.priority ? `，置顶第 ${leader.priority}` : ""}${pending ? `，${pending} 项待你` : ""}，回到上次的对话`;
    return `<section class="lgroup ${current ? "is-current" : ""} ${open ? "is-open" : ""} ${pinEnd ? "pin-end" : ""}">
      <div class="lrow">
        <button class="ltoggle" data-act="peek" data-leader="${leader.id}" aria-expanded="${open}" aria-controls="ws-${leader.id}" aria-label="${open ? "收起" : "展开"}「${esc(leader.name)}」的对话列表" title="${open ? "收起" : "展开看看，不切换对话"}">${ic("right", "sm")}</button>
        <button class="lname" data-act="leader" data-leader="${leader.id}" title="${esc(leader.name)}" aria-label="${esc(label)}"><span class="lav" aria-hidden="true">${esc(leader.initial)}</span><span class="ltext">${esc(leader.name)}</span>${pending ? `<span class="attn" aria-hidden="true">${pending}</span>` : ""}</button>
      </div>
      <ul class="ws" id="ws-${leader.id}" ${open ? "" : "hidden"}>${open ? workRows(leader.id, "ws-row") : ""}</ul>
    </section>`;
  }
  function sidebar() {
    const leaders = L.orderedLeaders();
    const pinned = leaders.filter(item => item.priority !== null).length;
    const total = L.pendingTotal();
    return `<aside class="sidebar" aria-label="项目导航">
      <div class="sb-head">
        <button class="project" data-act="menu" data-menu="project" aria-haspopup="menu" aria-label="项目菜单：${esc(D.project.name)}"><img class="logo" src="${LOGO}" alt=""><span class="project-name">${esc(D.project.name)}</span>${ic("down", "sm")}</button>
      </div>
      <div class="sb-top">
        <button class="nav-row" data-act="palette" aria-label="找 leader 或任务，Ctrl K">${ic("search")}<span class="t">找 leader 或任务</span><kbd>Ctrl K</kbd></button>
        <button class="nav-row" data-act="workbench" ${s.view === "workbench" ? 'aria-current="page"' : ""} aria-label="工作台${total ? `，${total} 项待你` : ""}">${ic("grid")}<span class="t">工作台</span>${total ? `<span class="attn" aria-hidden="true">${total}</span>` : ""}</button>
      </div>
      <nav class="sb-leaders" aria-label="Leader">${leaders.map((item, i) => leaderGroup(item, i === pinned - 1)).join("")}</nav>
      <div class="sb-foot">
        <button class="nav-row quiet" data-act="assistant" aria-label="全局助手，打开独立页面">${ic("msg")}<span class="t">全局助手</span>${ic("leave", "sm")}</button>
        <button class="icon-btn" data-act="collapse" aria-label="收起侧栏" title="收起侧栏 · Ctrl B">${ic("panel")}</button>
      </div>
    </aside>`;
  }

  // ---------- top bar ----------
  function topbar(kind) {
    const leader = L.leader(s.leaderId);
    const otherDot = otherPending() > 0 ? '<i class="dot" aria-hidden="true"></i>' : "";
    const expand = s.sidebarCollapsed ? `<button class="icon-btn d-only menu-btn" data-act="collapse" aria-label="展开侧栏${otherPending() ? "，其他对话有待你的事" : ""}" title="展开侧栏 · Ctrl B">${ic("panel")}${otherDot}</button>` : "";
    const mnav = `<button class="icon-btn m-only menu-btn" data-act="mnav" aria-label="打开导航${otherPending() ? "，其他对话有待你的事" : ""}">${ic("menu")}${otherDot}</button>`;
    if (kind === "chat") {
      const {taskId} = L.parse(s.scopeKey);
      const item = taskId ? L.task(taskId) : null;
      const pending = scopePending(s.scopeKey);
      const lead = s.openedFromList ? `<button class="back-link" data-act="back-list" aria-label="返回全部任务，保留搜索和位置">${ic("left", "sm")}<span class="d-only">全部任务</span></button>` : mnav;
      return `<header class="topbar">${expand}${lead}
        <nav class="crumb" aria-label="当前位置">
          <button class="crumb-leader d-only" data-act="leader-general" title="${esc(leader.name)} · 回到总会话">${esc(leader.name)}</button><span class="sep d-only" aria-hidden="true">/</span>
          <h1 class="crumb-h"><button class="crumb-scope d-only" data-act="details" title="${esc(item ? item.title : "总会话")} · 查看资料">${esc(item ? item.title : "总会话")}</button><button class="m-title m-only" data-act="details" title="查看资料"><strong>${esc(item ? item.title : "总会话")}</strong><span>${esc(leader.name)}${item ? ` · ${esc(needWord(item))}` : ""}</span></button></h1>
          ${item ? `<span class="status-text d-only ${L.needsYou(item) ? "is-need" : ""}">${esc(needWord(item))}</span>` : ""}
        </nav>
        <div class="top-actions">
          ${pending ? `<button class="pending-link" data-act="jump-pending">待处理 ${pending}</button>` : ""}
          <button class="ghost-btn d-only" data-act="details">${ic("file")}<span>资料</span></button>
          <button class="icon-btn" data-act="menu" data-menu="more" aria-haspopup="menu" aria-label="更多操作">${ic("more")}</button>
        </div>
      </header>`;
    }
    const back = s.returnTo ? `<button class="back-link" data-act="back" aria-label="返回 ${esc(scopeTitle(s.returnTo))}">${ic("left", "sm")}<span class="d-only">返回</span></button>` : mnav;
    if (kind === "list") {
      return `<header class="topbar">${expand}${back}
        <nav class="crumb" aria-label="当前位置"><button class="crumb-leader" data-act="leader-general" title="${esc(leader.name)} · 回到总会话">${esc(leader.name)}</button><span class="sep" aria-hidden="true">/</span><h1 class="crumb-here">全部任务</h1></nav>
        <div class="top-actions"><button class="icon-btn" data-act="menu" data-menu="more" aria-haspopup="menu" aria-label="更多操作">${ic("more")}</button></div>
      </header>`;
    }
    return `<header class="topbar">${expand}${back}<nav class="crumb" aria-label="当前位置"><span class="crumb-here">工作台</span><span class="crumb-note d-only">${esc(D.project.name)} · 全部 leader</span></nav>
      <div class="top-actions"><button class="icon-btn" data-act="menu" data-menu="more" aria-haspopup="menu" aria-label="更多操作">${ic("more")}</button></div></header>`;
  }

  // ---------- conversation ----------
  function message(msg, key) {
    const {leaderId, taskId} = L.parse(key);
    const leader = L.leader(leaderId);
    if (msg.role === "user") return `<div class="msg user"><p>${esc(msg.text)}</p></div>`;
    if (msg.role === "local") return `<p class="msg local"><i aria-hidden="true"></i>${esc(msg.text)}</p>`;
    if (msg.role === "decision") {
      const decision = taskId ? L.task(taskId).decision : leader.generalDecision;
      if (!decision) return "";
      return `<section class="ask" id="pending-anchor" aria-label="等你拍板"><p class="ask-kicker">等你拍板${taskId ? "" : " · 总会话"}</p><h2>${esc(decision.question)}</h2><p>${esc(decision.context)}</p><div class="ask-actions">${decision.options.map((opt, i) => `<button class="${i === 0 ? "btn primary" : "btn"}" data-act="decide" data-key="${esc(key)}" data-option="${esc(opt)}">${esc(opt)}</button>`).join("")}</div></section>`;
    }
    if (msg.role === "acceptance") {
      const item = L.task(taskId);
      if (item.status !== "awaiting_acceptance") return "";
      return `<section class="ask" id="pending-anchor" aria-label="等你验收"><p class="ask-kicker">等你验收</p><h2>${esc(item.title)}</h2><p>交付物和自查记录已附在上一条消息里。通过后任务结束；退回会继续推进。</p><div class="ask-actions"><button class="btn primary" data-act="accept" data-key="${esc(key)}" data-pass="1">验收通过</button><button class="btn" data-act="accept" data-key="${esc(key)}" data-pass="0">退回修改</button></div></section>`;
    }
    const who = key === "assistant" ? "全局助手" : leader.name;
    return `<div class="msg leader"><span class="who">${esc(who)}</span><p>${esc(msg.text)}</p>${msg.list && msg.list.length ? `<ul>${msg.list.map(line => `<li>${esc(line)}</li>`).join("")}</ul>` : ""}</div>`;
  }
  function queuedIntro(item) {
    const leader = L.leader(item.leaderId);
    return `<div class="queued-intro"><p class="eyebrow">待规划 · 还没开始</p><h2>${esc(item.title)}</h2><p>这项任务属于「${esc(leader.name)}」。可以先在这里补充需求、确认边界；点“开始推进”或发送第一条消息，才会交给 leader 执行。打开、切换或改决策方式都不会开始它。</p><div class="intro-actions"><button class="btn primary" data-act="start" data-key="${esc(L.keyOf(item.leaderId, item.id))}">开始推进</button><button class="btn" data-act="leader-general">先去总会话聊</button></div></div>`;
  }
  function composer(key, placeholder, opts = {}) {
    const draft = key === "assistant" ? s.assistantDraft : (s.drafts[key] || "");
    const mode = opts.mode ? MODES[L.modeOf(key)][0] : "";
    return `<div class="composer-wrap"><div class="composer-col">
      ${opts.queued ? '<p class="composer-note">发送会开始本任务。</p>' : ""}
      <form class="composer" data-scope="${esc(key)}">
        <textarea id="composer-input" data-scope="${esc(key)}" rows="2" aria-label="${esc(placeholder)}" placeholder="${esc(placeholder)}">${esc(draft)}</textarea>
        <div class="composer-bar">
          ${opts.mode ? `<button type="button" class="mode-btn" data-act="menu" data-menu="mode" aria-haspopup="menu" aria-label="决策方式：${mode}">${ic("check", "sm")}<span>${mode}</span>${ic("down", "sm")}</button>` : `<span class="composer-hint">${esc(opts.hint || "")}</span>`}
          <button type="submit" class="send-btn ${opts.queued ? "wide" : ""}" ${draft.trim() ? "" : "disabled"} aria-label="${opts.queued ? "发送并开始" : "发送"}">${opts.queued ? "发送并开始" : ic("send")}</button>
        </div>
      </form>
      <div class="composer-foot"><span>本地演示，发送不调用 AI</span><span class="d-only">${esc(opts.runtime || "")}Enter 发送 · Shift Enter 换行</span></div>
    </div></div>`;
  }
  function chatView() {
    const key = s.scopeKey;
    const {leaderId, taskId} = L.parse(key);
    const leader = L.leader(leaderId);
    const item = taskId ? L.task(taskId) : null;
    const queued = Boolean(item && item.status === "queued");
    const messages = L.messagesOf(key);
    const body = (queued ? queuedIntro(item) : "") + messages.map(msg => message(msg, key)).join("");
    const placeholder = item ? `发给「${item.title}」…` : `发给 ${leader.name} · 总会话…`;
    return `${topbar("chat")}<section class="chat" aria-label="${esc(item ? item.title : `${leader.name} 总会话`)}">
      <div class="chat-scroll" data-key="${esc(key)}"><div class="chat-col">${body}</div></div>
      ${composer(key, placeholder, {mode: Boolean(item), queued, runtime: `${leader.runtime} · `, hint: "总会话只商量整体安排，不会开始任务"})}
    </section>`;
  }

  // ---------- full task list ----------
  function listRows(leaderId) {
    const st = s.list[leaderId];
    const q = st.query.trim();
    const items = L.sortForList(L.tasksOf(leaderId).filter(L.filterFn(st.filter)).filter(item => !q || item.title.toLowerCase().includes(q.toLowerCase())));
    if (!items.length) return `<li class="list-empty">没有${q ? `匹配“${esc(q)}”的` : "这一类"}任务。${q ? '<button class="link" data-act="clear-search">清除搜索</button>' : ""}</li>`;
    return items.map(item => `<li><button class="task-row" data-act="open-task" data-task="${item.id}"><span class="tr-status ${L.needsYou(item) ? "is-need" : ""}">${esc(needWord(item))}</span><span class="tr-title">${highlight(item.title, q)}</span><span class="tr-meta">${L.needsYou(item) ? `等了 ${ago(item.waitMin).replace("前", "")}` : ago(item.updatedMin)} · ${MODES[L.modeOf(L.keyOf(leaderId, item.id))][0]}</span></button></li>`).join("");
  }
  function listView() {
    const leaderId = s.listLeader;
    const st = s.list[leaderId];
    const all = L.tasksOf(leaderId);
    return `${topbar("list")}<section class="list-page" aria-label="全部任务">
      <div class="list-scroll"><div class="list-col">
        <div class="list-tools">
          <label class="search-field">${ic("search")}<input id="list-search" type="search" value="${esc(st.query)}" placeholder="搜索 ${all.length} 项任务" aria-label="搜索「${esc(L.leader(leaderId).name)}」的任务" autocomplete="off"></label>
          <div class="segs" role="group" aria-label="状态范围">${L.FILTERS.map(([id, label, fn]) => `<button data-act="filter" data-filter="${id}" aria-pressed="${st.filter === id}">${label}<span class="n">${all.filter(fn).length}</span></button>`).join("")}</div>
        </div>
        <ul class="task-list" id="task-list">${listRows(leaderId)}</ul>
      </div></div>
    </section>`;
  }

  // ---------- workbench ----------
  function workbenchView() {
    const groups = L.orderedLeaders().map(leader => {
      const waiting = L.tasksOf(leader.id).filter(L.needsYou).sort((a, b) => b.waitMin - a.waitMin);
      return {leader, waiting};
    });
    const busy = groups.filter(g => g.waiting.length || g.leader.generalDecision);
    const calm = groups.filter(g => !g.waiting.length && !g.leader.generalDecision);
    const total = L.pendingTotal();
    const row = (key, title, reason, meta) => `<li><button class="wb-row" data-act="scope" data-key="${esc(key)}"><span class="wb-title">${esc(title)}</span><span class="wb-reason">${esc(reason)}</span><span class="wb-meta">${esc(meta)}</span></button></li>`;
    return `${topbar("workbench")}<section class="wb" aria-label="工作台"><div class="wb-scroll"><div class="wb-col">
      <h1>待你处理</h1>
      <p class="wb-sub">${total ? `${total} 项，分布在 ${busy.length} 个 leader。按置顶顺序排列，组内等得最久的在前；点开直接进入那个对话。` : "现在没有等你的事。"}</p>
      ${busy.map(({leader, waiting}) => `<section class="wb-group" aria-label="${esc(leader.name)}">
        <h2><button class="wb-leader" data-act="leader" data-leader="${leader.id}" title="${esc(leader.name)}">${esc(leader.name)}</button><span class="wb-count">${L.pendingOf(leader.id)}</span></h2>
        <ul>${leader.generalDecision ? row(L.keyOf(leader.id), leader.generalDecision.question, "总会话 · 待你拍板", "不属于某项任务") : ""}${waiting.map(item => row(L.keyOf(leader.id, item.id), item.title, item.decision ? `待你拍板：${item.decision.question}` : needWord(item), `等了 ${ago(item.waitMin).replace("前", "")}`)).join("")}</ul>
      </section>`).join("")}
      ${calm.length ? `<p class="wb-calm">${calm.map(g => `「${esc(g.leader.name)}」`).join("")}没有等你的事。</p>` : ""}
    </div></div></section>`;
  }

  // ---------- global assistant ----------
  function assistantPage() {
    return `<div class="assistant-page"><section class="main">
      <header class="topbar"><button class="back-link" data-act="assistant-back">${ic("left", "sm")}<span>返回 ${esc(D.project.name)}</span></button><nav class="crumb" aria-label="当前位置"><h1 class="crumb-here">全局助手</h1><span class="crumb-note d-only">跨工作区 · 不属于任何 leader</span></nav></header>
      <section class="chat" aria-label="全局助手">
        <div class="chat-scroll" data-key="assistant"><div class="chat-col">${s.assistantMessages.map(msg => message(msg, "assistant")).join("")}</div></div>
        ${composer("assistant", "问全局助手…", {hint: "跨工作区提问；项目内的事回到对应 leader"})}
      </section>
    </section></div>`;
  }

  // ---------- mobile layered navigation ----------
  function mobileNav() {
    if (!s.mobileNav) return "";
    const close = `<button class="icon-btn" data-act="mnav-close" aria-label="关闭导航，回到对话">${ic("x")}</button>`;
    if (s.mobileNav === "leaders") {
      const total = L.pendingTotal();
      return `<div class="mnav" data-layer role="dialog" aria-modal="true" aria-label="全部 leader">
        <header class="mnav-head"><button class="project" data-act="menu" data-menu="project" aria-haspopup="menu" aria-label="项目菜单"><img class="logo" src="${LOGO}" alt=""><span class="project-name">${esc(D.project.name)}</span>${ic("down", "sm")}</button>${close}</header>
        <div class="mnav-body">
          <ul class="mlist">
            <li><button class="mrow" data-act="palette">${ic("search")}<span class="t">找 leader 或任务</span></button></li>
            <li><button class="mrow" data-act="workbench">${ic("grid")}<span class="t">工作台</span>${total ? `<span class="attn">${total}</span>` : ""}</button></li>
          </ul>
          <p class="mcap">Leader</p>
          <ul class="mlist">${L.orderedLeaders().map((leader, i, all) => {
            const pending = L.pendingOf(leader.id), current = leader.id === s.leaderId;
            const pinEnd = leader.priority !== null && (all[i + 1] || {}).priority === null;
            return `<li class="mleader ${current ? "is-current" : ""} ${pinEnd ? "pin-end" : ""}"><button class="mrow" data-act="leader" data-leader="${leader.id}" aria-label="进入「${esc(leader.name)}」上次的对话${pending ? `，${pending} 项待你` : ""}" ${current ? 'aria-current="true"' : ""}><span class="lav" aria-hidden="true">${esc(leader.initial)}</span><span class="t">${esc(leader.name)}</span>${pending ? `<span class="attn" aria-hidden="true">${pending}</span>` : ""}</button><button class="mdrill" data-act="mnav-leader" data-leader="${leader.id}" aria-label="查看「${esc(leader.name)}」的全部对话">${ic("right")}</button></li>`;
          }).join("")}</ul>
          <ul class="mlist mfoot"><li><button class="mrow quiet" data-act="assistant">${ic("msg")}<span class="t">全局助手</span>${ic("leave", "sm")}</button></li></ul>
        </div>
      </div>`;
    }
    const leader = L.leader(s.mobileNav);
    const others = L.pendingTotal() - L.pendingOf(leader.id);
    return `<div class="mnav" data-layer role="dialog" aria-modal="true" aria-label="${esc(leader.name)} 的对话">
      <header class="mnav-head"><button class="back-link" data-act="mnav-leaders" aria-label="全部 leader${others ? `，其他组 ${others} 项待你` : ""}">${ic("left", "sm")}<span>全部 leader</span>${others ? `<span class="attn" aria-hidden="true">${others}</span>` : ""}</button>${close}</header>
      <div class="mnav-body">
        <h2 class="mnav-title">${esc(leader.name)}</h2>
        <ul class="mlist">${workRows(leader.id, "mrow")}</ul>
      </div>
    </div>`;
  }

  // ---------- overlays ----------
  function paletteResults() {
    const q = ui.palQuery.trim().toLowerCase();
    const out = [];
    const leaders = L.orderedLeaders().filter(item => !q || item.name.toLowerCase().includes(q));
    if (leaders.length) {
      out.push(`<li class="pal-cap" role="presentation">Leader</li>`);
      leaders.forEach(leader => out.push({key: s.lastScope[leader.id], html: `<span class="lav" aria-hidden="true">${esc(leader.initial)}</span><span class="pal-t">${highlight(leader.name, ui.palQuery.trim())}</span><span class="pal-m">${L.pendingOf(leader.id) ? `${L.pendingOf(leader.id)} 项待你` : `${L.tasksOf(leader.id).length} 项任务`}</span>`, leader: leader.id}));
    }
    const tasks = q ? D.tasks.filter(item => item.title.toLowerCase().includes(q)) : D.tasks.filter(L.needsYou).sort((a, b) => b.waitMin - a.waitMin);
    const shown = tasks.slice(0, 30);
    if (shown.length) {
      out.push(`<li class="pal-cap" role="presentation">${q ? `任务 · ${tasks.length} 项` : "待你 · 全部 leader"}</li>`);
      shown.forEach(item => out.push({key: L.keyOf(item.leaderId, item.id), html: `${mark(item)}<span class="pal-t">${highlight(item.title, ui.palQuery.trim())}</span><span class="pal-m">${esc(L.leader(item.leaderId).name)}</span>`}));
    }
    if (tasks.length > shown.length) out.push(`<li class="pal-cap" role="presentation">还有 ${tasks.length - shown.length} 项，继续输入缩小范围</li>`);
    return out;
  }
  function paletteList() {
    const results = paletteResults();
    const options = results.filter(item => typeof item === "object");
    if (ui.palIndex >= options.length) ui.palIndex = Math.max(0, options.length - 1);
    let n = -1;
    const html = results.map(item => {
      if (typeof item === "string") return item;
      n += 1;
      return `<li role="option" id="pal-${n}" class="pal-row" data-act="pal-pick" data-key="${esc(item.key)}" aria-selected="${n === ui.palIndex}">${item.html}</li>`;
    }).join("");
    return {html: html || '<li class="pal-empty">没有匹配的 leader 或任务</li>', count: options.length};
  }
  function overlay() {
    const o = ui.overlay;
    if (!o) return "";
    if (o.type === "palette") {
      const list = paletteList();
      return `<div class="backdrop" data-act="close-overlay"></div><div class="palette" data-layer role="dialog" aria-modal="true" aria-label="跨 leader 搜索">
        <label class="pal-search">${ic("search")}<input id="pal-input" role="combobox" aria-expanded="true" aria-controls="pal-list" aria-activedescendant="pal-${ui.palIndex}" value="${esc(ui.palQuery)}" placeholder="找 leader 或任务（全部 ${D.leaders.length} 个 leader）" autocomplete="off"><button class="pal-close m-only" data-act="close-overlay">取消</button></label>
        <ul id="pal-list" role="listbox" aria-label="结果">${list.html}</ul>
        <p class="pal-foot">↑ ↓ 选择 · Enter 打开 · Esc 关闭</p></div>`;
    }
    if (o.type === "details") return `<div class="backdrop clear" data-act="close-overlay"></div>${detailsPanel()}`;
    if (o.type === "info") return `<div class="backdrop" data-act="close-overlay"></div><div class="dialog" data-layer role="dialog" aria-modal="true" aria-labelledby="dlg-title"><header><h2 id="dlg-title">${esc(o.title)}</h2><button class="icon-btn" data-act="close-overlay" aria-label="关闭">${ic("x")}</button></header><div class="dialog-body">${o.body}</div></div>`;
    if (o.type === "menu") return `<div class="backdrop clear" data-act="close-overlay"></div><div class="popover" data-layer role="menu" aria-label="${esc(o.label)}" style="${o.style}">${o.items.map(item => item === "-" ? '<hr>' : item.caption ? `<p class="pop-cap">${item.caption}</p>` : `<button role="menuitem" class="pop-item" data-act="${item.act}" ${item.data || ""}>${item.check !== undefined ? `<span class="pop-check">${item.check ? ic("check", "sm") : ""}</span>` : ""}<span class="pop-t">${esc(item.label)}${item.sub ? `<small>${esc(item.sub)}</small>` : ""}</span>${item.kbd ? `<kbd>${item.kbd}</kbd>` : ""}</button>`).join("")}</div>`;
    throw new Error(`未知浮层 ${o.type}`);
  }
  function detailsPanel() {
    const {leaderId, taskId} = L.parse(s.scopeKey);
    const leader = L.leader(leaderId);
    const head = `<header><h2 id="dlg-title">资料</h2><button class="icon-btn" data-act="close-overlay" aria-label="关闭资料">${ic("x")}</button></header>`;
    if (taskId) {
      const item = L.task(taskId);
      return `<aside class="drawer" data-layer role="dialog" aria-modal="true" aria-labelledby="dlg-title">${head}<div class="drawer-body">
        <p class="d-kicker">任务</p><h3 class="d-title">${esc(item.title)}</h3>
        <dl class="d-fields"><dt>所属 leader</dt><dd><button class="link" data-act="leader-general">${esc(leader.name)}</button></dd><dt>状态</dt><dd>${esc(D.labels[item.status])}${item.decision ? " · 有一项待你拍板" : ""}</dd><dt>决策方式</dt><dd>${MODES[L.modeOf(s.scopeKey)][0]}</dd><dt>最近动态</dt><dd>${ago(item.updatedMin)}</dd><dt>执行</dt><dd>${esc(leader.runtime)} · 原生默认模型</dd><dt>编号</dt><dd>${esc(item.id)}</dd></dl>
        <p class="d-kicker">本组职责</p><p class="d-text">${esc(leader.duty)}</p>
        <p class="d-note">任务对话、草稿和决策方式只属于这项任务；总会话是另一段对话。</p>
      </div></aside>`;
    }
    const all = L.tasksOf(leaderId);
    return `<aside class="drawer" data-layer role="dialog" aria-modal="true" aria-labelledby="dlg-title">${head}<div class="drawer-body">
      <p class="d-kicker">Leader · 总会话</p><h3 class="d-title">${esc(leader.name)}</h3>
      <p class="d-text">${esc(leader.duty)}</p>
      <dl class="d-fields"><dt>置顶</dt><dd>${leader.priority ? `第 ${leader.priority} 位` : "未置顶"}</dd><dt>执行</dt><dd>${esc(leader.runtime)} · 原生默认模型</dd>${L.FILTERS.map(([id, label, fn]) => `<dt>${label}</dt><dd>${all.filter(fn).length} 项</dd>`).join("")}</dl>
      <button class="btn" data-act="list" data-leader="${leaderId}">${ic("list", "sm")}查看全部任务</button>
    </div></aside>`;
  }
  function menuAt(anchor, label, items, align) {
    const r = anchor.getBoundingClientRect();
    const width = 260;
    const left = align === "right" ? Math.max(8, r.right - width) : Math.min(r.left, innerWidth - width - 8);
    const below = r.bottom + 6;
    const style = below + 280 > innerHeight ? `left:${left}px;bottom:${innerHeight - r.top + 6}px;width:${width}px` : `left:${left}px;top:${below}px;width:${width}px`;
    return {type: "menu", label, items, style};
  }
  function menuFor(name, anchor) {
    if (name === "project") {
      return menuAt(anchor, "项目菜单", [
        {caption: `${esc(D.project.name)}<br><span>${esc(D.project.path)}</span>`},
        ...[["交流区", "项目成员与各 leader 的公共留言，不是 leader 总会话"], ["Git", "分支、合并请求与推送规则"], ["我的待办", "你自己记下的事；和“待你”可能重叠，位置待定"], ["会话库", "收编外部会话"], ["用量", "额度与花费"]].map(([label, sub]) => ({act: "info", label, sub, data: `data-title="${label}" data-body="${esc(sub)}"`})),
        "-",
        {act: "info", label: "切换工作区…", data: 'data-title="切换工作区" data-body="正式版会离开当前项目回到工作区列表；本原型只有一个示例项目。"'}
      ], "left");
    } else if (name === "more") {
      const {taskId} = L.parse(s.scopeKey);
      const items = [];
      if (s.view === "chat") {
        items.push({act: "details", label: "资料"});
        if (taskId) items.push({act: "leader-general", label: "回到总会话"});
        items.push({act: "list", label: "全部任务", data: `data-leader="${s.leaderId}"`});
        items.push("-");
      }
      if (!isMobile()) items.push({act: "collapse", label: s.sidebarCollapsed ? "展开侧栏" : "收起侧栏", kbd: "Ctrl B"});
      items.push({act: "shortcuts", label: "快捷键"});
      return menuAt(anchor, "更多操作", items, "right");
    } else if (name === "mode") {
      const current = L.modeOf(s.scopeKey);
      return menuAt(anchor, "决策方式", [{caption: "这项任务如何推进"}, ...Object.entries(MODES).map(([id, [label, sub]]) => ({act: "set-mode", label, sub, check: id === current, data: `data-mode="${id}"`}))], "left");
    }
    throw new Error(`未知菜单 ${name}`);
  }

  // ---------- focus ----------
  // render() rebuilds the DOM, so a control is remembered by what it is (selector + position among matches), never by node.
  function focusKey(el) {
    if (!el || !root.contains(el)) return null;
    const caret = typeof el.selectionStart === "number" ? {start: el.selectionStart, end: el.selectionEnd} : {};
    if (el.id) return {sel: `#${CSS.escape(el.id)}`, index: 0, ...caret};
    const data = Object.entries(el.dataset).map(([k, v]) => `[data-${k.replace(/[A-Z]/g, c => `-${c.toLowerCase()}`)}="${CSS.escape(v)}"]`).join("");
    const sel = el.tagName.toLowerCase() + (el.classList.length ? `.${CSS.escape(el.classList[0])}` : "") + data;
    return {sel, index: [...root.querySelectorAll(sel)].indexOf(el), ...caret};
  }
  const findKey = key => root.querySelectorAll(key.sel)[key.index] || null;
  function focusAt(el, key) {
    el.focus({preventScroll: true});
    if (key.start !== undefined) el.setSelectionRange(key.start, key.end);
  }
  // Closing a layer hands focus back to the control that opened it; a vanished opener is a bug and is reported, not skipped.
  function returnFocus(key) {
    if (!key) return;                        // the layer was opened while nothing had focus
    const el = findKey(key);
    if (!el) throw new Error(`关闭后找不到打开它的控件：${key.sel}`);
    focusAt(el, key);
  }
  // The last [data-layer] is the top one (overlay renders after the mobile nav); only it is interactive.
  const topLayer = () => [...root.querySelectorAll("[data-layer]")].pop() || null;
  const tabbables = layer => [...layer.querySelectorAll("button, input, textarea, [tabindex]")].filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length);
  const focusFirst = () => tabbables(topLayer())[0].focus();

  // ---------- render ----------
  function restore(focus) {
    const chat = root.querySelector(".chat-scroll");
    if (chat) { const top = s.scroll[chat.dataset.key]; chat.scrollTop = top == null ? chat.scrollHeight : top; }
    const list = root.querySelector(".list-scroll");
    if (list) list.scrollTop = s.list[s.listLeader].scrollTop;
    const sb = root.querySelector(".sb-leaders");
    if (sb) sb.scrollTop = ui.sbScroll;
    if (focus) {
      const el = findKey(focus);             // after a view change the same control may legitimately be gone
      if (el) focusAt(el, focus);
    }
  }
  function render() {
    const focus = focusKey(document.activeElement);
    if (s.view === "assistant") root.innerHTML = assistantPage() + overlay();
    else {
      const main = s.view === "list" ? listView() : s.view === "workbench" ? workbenchView() : chatView();
      root.innerHTML = `<div class="app ${s.sidebarCollapsed ? "is-collapsed" : ""}">${sidebar()}<main class="main">${main}</main></div>${mobileNav()}${overlay()}`;
    }
    const top = topLayer();
    for (const child of root.children) child.inert = Boolean(top) && !child.contains(top) && !child.classList.contains("backdrop");
    restore(focus);
    document.title = `${s.view === "assistant" ? "全局助手" : s.view === "workbench" ? "工作台" : s.view === "list" ? `${L.leader(s.listLeader).name} · 全部任务` : `${scopeTitle(s.scopeKey)} · ${L.leader(s.leaderId).name}`} — Crew 多 leader 导航原型`;
  }
  function toast(text) {
    let el = document.getElementById("toast");
    if (!el) { el = document.createElement("div"); el.id = "toast"; el.className = "toast"; el.setAttribute("role", "status"); document.body.append(el); }
    el.textContent = text;
    el.hidden = false;
    clearTimeout(ui.toastTimer);
    ui.toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
  }
  const focusComposer = () => { if (!isMobile()) document.getElementById("composer-input")?.focus(); };

  // ---------- actions ----------
  function showLayer(o) { ui.overlay = o; render(); focusFirst(); }
  // A layer opened from another layer keeps the first opener, so closing lands where the user started.
  function openOverlay(o, opener) { o.ret = ui.overlay ? ui.overlay.ret : focusKey(opener); showLayer(o); }
  function closeOverlay() { const {ret} = ui.overlay; ui.overlay = null; render(); returnFocus(ret); }
  // The palette replaces the mobile nav, so it returns to whatever opened the nav.
  function openPalette(opener) {
    const ret = s.mobileNav ? ui.mnavRet : ui.overlay ? ui.overlay.ret : focusKey(opener);
    s.mobileNav = null; ui.palQuery = ""; ui.palIndex = 0;
    showLayer({type: "palette", ret});
  }
  function closeMobileNav() { s.mobileNav = null; render(); returnFocus(ui.mnavRet); ui.mnavRet = null; }
  function pickPalette(key) {
    ui.overlay = null;
    L.openScope(key);
    render();
    focusComposer();
  }
  function act(el, event) {
    const a = el.dataset.act;
    switch (a) {
      case "scope": L.openScope(el.dataset.key); s.mobileNav = null; ui.overlay = null; render(); focusComposer(); break;
      case "leader": L.selectLeader(el.dataset.leader); s.mobileNav = null; ui.overlay = null; render(); focusComposer(); break;
      case "leader-general": L.openScope(L.keyOf(s.leaderId)); ui.overlay = null; render(); focusComposer(); break;
      case "peek": L.togglePeek(el.dataset.leader); render(); break;
      case "list": L.openList(el.dataset.leader, el.dataset.filter); s.mobileNav = null; ui.overlay = null; render(); break;
      case "open-task": L.openScope(L.keyOf(s.listLeader, el.dataset.task), {fromList: s.listLeader}); render(); focusComposer(); break;
      case "back-list": L.openList(s.openedFromList, undefined, {resume: true}); render(); break;
      case "back": L.goBack(); render(); break;
      case "filter": s.list[s.listLeader].filter = el.dataset.filter; s.list[s.listLeader].scrollTop = 0; render(); break;
      case "clear-search": s.list[s.listLeader].query = ""; render(); break;
      case "workbench": L.openWorkbench(); s.mobileNav = null; render(); break;
      case "assistant": s.view = "assistant"; s.mobileNav = null; ui.overlay = null; render(); break;
      case "assistant-back": L.openScope(s.lastScope[s.leaderId]); render(); break;
      case "palette": openPalette(el); break;
      case "pal-pick": pickPalette(el.dataset.key); break;
      case "collapse": s.sidebarCollapsed = !s.sidebarCollapsed; if (ui.overlay) closeOverlay(); else render(); break;
      case "details": openOverlay({type: "details"}, el); break;
      case "menu": openOverlay(menuFor(el.dataset.menu, el), el); break;
      case "close-overlay": closeOverlay(); break;
      case "info": openOverlay({type: "info", title: el.dataset.title, body: `<p>${esc(el.dataset.body)}</p><p class="muted">正式 Crew 已有这个入口。本原型只验证多 leader 导航，没有展开它；它在新导航里的位置还没定。</p>`}, el); break;
      case "shortcuts": openOverlay({type: "info", title: "快捷键", body: '<dl class="d-fields"><dt><kbd>Ctrl K</kbd></dt><dd>跨 leader 找 leader 或任务</dd><dt><kbd>Ctrl B</kbd></dt><dd>收起 / 展开侧栏</dd><dt><kbd>Esc</kbd></dt><dd>关闭浮层；在清单或工作台时返回原对话</dd><dt><kbd>Enter</kbd></dt><dd>发送，Shift Enter 换行</dd></dl><p class="muted">Mac 上用 ⌘ 代替 Ctrl。</p>'}, el); break;
      case "set-mode": L.setMode(s.scopeKey, el.dataset.mode); closeOverlay(); toast(`决策方式已改为“${MODES[el.dataset.mode][0]}”。只是保存设置，不会开始任务。`); break;
      case "jump-pending": document.getElementById("pending-anchor")?.scrollIntoView({block: "center", behavior: "smooth"}); break;
      case "start": L.start(el.dataset.key); render(); toast("本地演示：任务已开始推进，没有唤醒真实 AI。"); break;
      case "decide": L.decide(el.dataset.key, el.dataset.option); render(); toast("已记录你的选择（本地演示）。"); break;
      case "accept": L.accept(el.dataset.key, el.dataset.pass === "1"); render(); break;
      case "mnav": ui.mnavRet = focusKey(el); s.mobileNav = s.leaderId; render(); focusFirst(); break;
      case "mnav-leaders": { const from = s.mobileNav; s.mobileNav = "leaders"; render(); root.querySelector(`.mdrill[data-leader="${from}"]`).focus(); break; }
      case "mnav-leader": s.mobileNav = el.dataset.leader; render(); focusFirst(); break;
      case "mnav-close": closeMobileNav(); break;
      default: throw new Error(`未处理的操作 ${a}`);
    }
    event.preventDefault();
  }

  document.addEventListener("click", event => {
    const el = event.target.closest("[data-act]");
    if (el && !el.disabled) act(el, event);
  });
  document.addEventListener("input", event => {
    const t = event.target;
    if (t.id === "composer-input") {
      if (t.dataset.scope === "assistant") s.assistantDraft = t.value; else s.drafts[t.dataset.scope] = t.value;
      t.closest("form").querySelector(".send-btn").disabled = !t.value.trim();
    } else if (t.id === "list-search") {
      s.list[s.listLeader].query = t.value;
      s.list[s.listLeader].scrollTop = 0;
      document.getElementById("task-list").innerHTML = listRows(s.listLeader);
      root.querySelector(".list-scroll").scrollTop = 0;
    } else if (t.id === "pal-input") {
      ui.palQuery = t.value; ui.palIndex = 0;
      const list = paletteList();
      document.getElementById("pal-list").innerHTML = list.html;
      t.setAttribute("aria-activedescendant", "pal-0");
    }
  });
  document.addEventListener("submit", event => {
    event.preventDefault();
    const form = event.target;
    const key = form.dataset.scope;          // the conversation this composer was rendered for
    const text = form.querySelector("textarea").value.trim();
    if (!text) return;
    if (key === "assistant") {
      s.assistantMessages.push({role: "user", text}, {role: "local", text: "本地演示：全局助手没有被调用。"});
      s.assistantDraft = "";
      s.scroll.assistant = null;
      render();
      focusComposer();
      return;
    }
    const started = L.send(key, text);
    render();
    focusComposer();
    toast(started ? "已发送并开始推进（本地演示，没有调用 AI）。" : "已发送到本页（本地演示，没有调用 AI）。");
  });
  document.addEventListener("keydown", event => {
    // While an IME is composing, every key (Esc, Enter, arrows) belongs to the IME. Safari reports the closing key as keyCode 229.
    if (event.isComposing || event.keyCode === 229) return;
    if (event.key === "Tab") {
      const layer = topLayer();
      if (!layer) return;
      // Tab cycles inside the top layer; everything under it is inert.
      event.preventDefault();
      const items = tabbables(layer);
      const at = items.indexOf(document.activeElement);
      const step = event.shiftKey ? -1 : 1;
      items[at < 0 ? (step > 0 ? 0 : items.length - 1) : (at + step + items.length) % items.length].focus();
      return;
    }
    const mod = event.ctrlKey || event.metaKey;
    if (mod && event.key.toLowerCase() === "k") { event.preventDefault(); if (s.view !== "assistant") openPalette(document.activeElement); return; }
    if (mod && event.key.toLowerCase() === "b") { event.preventDefault(); if (s.view !== "assistant" && !isMobile()) { s.sidebarCollapsed = !s.sidebarCollapsed; render(); } return; }
    if (event.target.id === "composer-input" && event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.target.form.requestSubmit();
      return;
    }
    if (event.target.id === "pal-input") {
      const options = [...document.querySelectorAll(".pal-row")];
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (!options.length) return;
        ui.palIndex = (ui.palIndex + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
        options.forEach((row, i) => row.setAttribute("aria-selected", String(i === ui.palIndex)));
        options[ui.palIndex].scrollIntoView({block: "nearest"});
        event.target.setAttribute("aria-activedescendant", `pal-${ui.palIndex}`);
        return;
      }
      if (event.key === "Enter" && options[ui.palIndex]) { event.preventDefault(); pickPalette(options[ui.palIndex].dataset.key); return; }
    }
    if (event.key === "Escape") {
      if (ui.overlay) { closeOverlay(); return; }
      if (s.mobileNav) { closeMobileNav(); return; }
      if (s.view === "list" || s.view === "workbench") { L.goBack(); render(); }
    }
  });
  // Scroll does not bubble; listen in the capture phase and remember each position as it changes.
  document.addEventListener("scroll", event => {
    const t = event.target;
    if (!(t instanceof Element)) return;
    if (t.classList.contains("chat-scroll")) s.scroll[t.dataset.key] = t.scrollHeight - t.scrollTop - t.clientHeight < 4 ? null : t.scrollTop;
    else if (t.classList.contains("list-scroll")) s.list[s.listLeader].scrollTop = t.scrollTop;
    else if (t.classList.contains("sb-leaders")) ui.sbScroll = t.scrollTop;
  }, true);
  let resizeQueued = false;
  addEventListener("resize", () => {
    if (resizeQueued) return;
    resizeQueued = true;
    requestAnimationFrame(() => {
      resizeQueued = false;
      // The phone nav does not exist at desktop widths; left open it would keep the page inert.
      // Any overlay open at that moment was opened from the nav (the palette closes it), so it goes too.
      if (s.mobileNav && !isMobile()) { s.mobileNav = null; ui.mnavRet = null; ui.overlay = null; }
      if (ui.overlay && ui.overlay.type === "menu") closeOverlay(); else render();
    });
  });

  render();
})();
