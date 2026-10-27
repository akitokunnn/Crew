(() => {
  "use strict";
  const STORAGE = "crew-design-prototype-v1";
  let saved;
  try {
    saved = JSON.parse(localStorage.getItem(STORAGE));
  } catch (_) {
    /* Start from explicit sample data when storage is absent. */
  }
  const model = CrewPrototypeState.create(CrewSample, saved);
  const s = model.state;
  if (s.workspaces?.length) CrewSample.workspaces = s.workspaces;
  else s.workspaces = CrewSample.workspaces;
  const $ = (selector) => document.querySelector(selector);
  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const icons = {
    home: "M3 10 12 3l9 7M5 9v11h5v-6h4v6h5V9",
    spark: "m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4Z",
    check: "m5 12 4 4L19 6",
    todo: "M9 5h11M9 12h11M9 19h11M3 5h1M3 12h1M3 19h1",
    plus: "M12 5v14M5 12h14",
    arrow: "M5 12h14m-5-5 5 5-5 5",
    chevron: "m9 5 7 7-7 7",
    down: "m6 9 6 6 6-6",
    back: "M19 12H5m6-6-6 6 6 6",
    folder: "M3 7V5h7l2 3h9v11H3Z",
    chat: "M4 4h16v12H9l-5 4Z",
    team: "M9 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM2 21v-3c0-4 14-4 14 0v3M17 5a3 3 0 0 1 0 6m2 4c2 0 3 1 3 3v3",
    file: "M14 3H5v18h14V8Zm0 0v5h5M8 13h8M8 17h6",
    menu: "M4 6h16M4 12h16M4 18h16",
    close: "m6 6 12 12M6 18 18 6",
    settings: "M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M10 15v6",
    more: "M5 12h.01M12 12h.01M19 12h.01",
    pin: "m8 3 8 0-1 6 4 4H5l4-4ZM12 13v8",
    clock: "M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
    link: "m10 13 4-4M8 15l-2 2a3 3 0 0 1-4-4l5-5a3 3 0 0 1 4 0m2 1 2-2a3 3 0 0 1 4 4l-5 5a3 3 0 0 1-4 0",
    external: "M14 3h7v7m0-7L10 14M10 3H3v18h18v-7",
    stop: "M6 6h12v12H6Z",
    up: "M12 20V4m-6 6 6-6 6 6",
    attach: "m9 14 6-6a3 3 0 0 1 4 4l-8 8a5 5 0 0 1-7-7l9-9",
    git: "M6 5a2 2 0 1 0 0 .1M6 7v10m0 0a2 2 0 1 0 0 .1M18 5a2 2 0 1 0 0 .1M18 7v3c0 4-12 0-12 4",
    sun: "M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0Z",
    panel: "M3 4h18v16H3ZM9 4v16",
    info: "M12 11v6m0-10v.1M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
    archive: "M3 3h18v5H3Zm2 5v13h14V8m-10 5h6",
    leaf: "M19 3c-9-1-17 4-13 12 8 4 14-3 13-12ZM4 21 15 9",
    bell: "M5 17h14l-2-4V8a5 5 0 0 0-10 0v5ZM10 21h4",
  };
  const icon = (name) =>
    `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${icons[name] || icons.file}"/></svg>`;
  const statusNames = {
    working: "正在推进",
    review: "待你体验",
    stopped: "已停止",
    idle: "已交付",
  };
  const status = (state) =>
    `<span class="status ${esc(state)}">${statusNames[state] || "待继续"}</span>`;
  const ws = (id) =>
    CrewSample.workspaces.find((w) => w.id === id) || {
      id,
      name: "未分配",
      color: "sage",
      initials: "·",
      path: "普通文件夹",
    };
  const workspaceOptions = (selected) =>
    `<option value="">未分配</option>${CrewSample.workspaces.map((w) => `<option value="${w.id}" ${w.id === selected ? "selected" : ""}>${esc(w.name)}</option>`).join("")}`;
  let resetting = false;
  let route = "";
  let parts = [];
  let settingsCleanup;
  let toastTimer;
  let todoFilter = "all";
  let dialogAction;
  function persist() {
    try {
      localStorage.setItem(STORAGE, JSON.stringify(s));
    } catch (_) {
      toast("浏览器存储不可用，本次体验仍可继续。");
    }
  }
  function toast(text) {
    const t = $("#toast");
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      t.hidden = true;
    }, 3800);
  }
  function context() {
    if (parts[0] === "assistant")
      return {
        id: "assistant",
        person: "assistant",
        name: "个人助手",
        leader: "个人助手",
        global: true,
        members: [],
        state: "working",
        title: "把零散的想法，慢慢理清",
      };
    if (parts[0] === "workspace-chat") {
      const w = ws(parts[1]);
      return {
        id: "workspace-" + w.id,
        person: "leader",
        name: "工作区负责人",
        leader: "工作区负责人",
        global: true,
        workspace: w.id,
        members: [],
        state: "working",
        title: w.name + " · 工作区对话",
      };
    }
    if (parts[0] !== "task") return null;
    const t = s.tasks.find((x) => x.id === parts[1]);
    if (!t) return null;
    const member = t.members.find((m) => m.id === parts[2]);
    return {
      ...t,
      person: member?.id || "leader",
      name: member?.name || t.leader,
      member,
    };
  }
  function saveView() {
    if (!route || resetting) return;
    const c = context();
    if (c && $("#message-input"))
      model.draft(c.id, c.person, $("#message-input").value);
    const scroll = $("[data-scroll]");
    if (scroll) s.positions[route] = scroll.scrollTop;
    persist();
  }
  function go(next) {
    saveView();
    if (location.hash.slice(1) === next) render();
    else location.hash = next;
  }
  function restoreView() {
    requestAnimationFrame(() => {
      const area = $("[data-scroll]");
      if (area)
        area.scrollTop =
          s.positions[route] ?? (context() ? area.scrollHeight : 0);
    });
  }
  function closeNavigation() {
    $("#app").classList.remove("nav-open");
    $("#nav-backdrop").hidden = true;
    syncAccessibility();
  }
  function syncAccessibility() {
    const mobile = matchMedia("(max-width:700px)").matches;
    $("#sidebar").inert =
      (mobile && !$("#app").classList.contains("nav-open")) ||
      (!mobile && $("#app").classList.contains("focus"));
    const overlay = mobile && !$("#inspector").hidden;
    $("#main").inert =
      overlay || (mobile && $("#app").classList.contains("nav-open"));
    $("#inspector").setAttribute("role", overlay ? "dialog" : "complementary");
    if (overlay) $("#inspector").setAttribute("aria-modal", "true");
    else $("#inspector").removeAttribute("aria-modal");
  }
  function topbar(crumbs, actions = "") {
    return `<header class="topbar"><div class="row grow"><button class="icon-button mobile-menu" data-action="nav" aria-label="打开导航">${icon("menu")}</button><div class="breadcrumb">${crumbs}</div></div><div class="top-actions"><span class="sample-label">交互原型 · 样例</span>${actions || `<button class="icon-button" data-action="guide" aria-label="体验指引" title="体验指引">${icon("info")}</button>`}</div></header>`;
  }
  function navItem(target, label, name, count = "") {
    return `<button class="nav-item ${route === target ? "active" : ""}" data-go="${target}" ${route === target ? 'aria-current="page"' : ""}>${icon(name)}<span>${label}</span>${count ? `<span class="count">${count}</span>` : ""}</button>`;
  }
  function renderSidebar() {
    $("#sidebar").innerHTML =
      `<div class="brand"><img src="crew.svg" alt="Crew 标志"><div><strong>Crew</strong><div class="eyebrow">A little further.</div></div><button class="icon-button" data-action="focus" title="收起导航，专注对话" aria-label="收起导航">${icon("panel")}</button></div>
      <nav aria-label="日常">${navItem("home", "首页", "home")}${navItem("assistant", "个人助手", "spark")}${navItem("todos", "TODO 与灵感", "todo", s.todos.filter((x) => !x.done).length)}</nav>
      <div class="workspace-nav"><div class="nav-section-title"><span>工作区</span><button class="icon-button" data-action="workspace-add" aria-label="添加工作区">${icon("plus")}</button></div>
      ${CrewSample.workspaces
        .map(
          (w) =>
            `<div class="workspace-heading"><span class="workspace-symbol ${w.color}">${esc(w.initials)}</span><button class="ws-name" data-go="workspace/${w.id}">${esc(w.name)}</button><button class="icon-button" data-action="new" data-workspace="${w.id}" aria-label="在${esc(w.name)}开始对话">${icon("plus")}</button></div>${s.tasks
              .filter((t) => t.workspace === w.id)
              .map(
                (t) =>
                  `<button class="task-nav ${parts[1] === t.id && ["task", "report"].includes(parts[0]) ? "active" : ""}" data-go="task/${t.id}/leader"><span class="task-nav-title"><span class="dot"></span><span class="truncate">${esc(t.title)}</span>${t.pinned ? icon("pin") : ""}</span><span class="task-nav-meta"><span>${statusNames[t.state]}</span>${t.fresh ? `<span class="badge">新成果 ${esc(t.version)}</span>` : ""}</span></button>`,
              )
              .join("")}${s.tasks
              .filter((t) => t.related.includes(w.id))
              .map(
                (t) =>
                  `<button class="related-nav" data-go="task/${t.id}/leader">${icon("link")}<span class="truncate">${esc(t.title)}</span><span class="chip">关联</span></button>`,
              )
              .join("")}`,
        )
        .join("")}</div>
      <div class="sidebar-bottom">${navItem("more", "更多工作能力", "more")}${navItem("settings", "设置", "settings")}<div class="sample-indicator"><span><i></i>独立样例环境</span><button data-action="guide">体验指引 ↗</button></div></div>`;
  }
  function captureForm(workspace = "") {
    return `<form class="quick-capture" id="capture-form"><textarea id="capture-input" aria-label="记下想法或开始聊" placeholder="一个想法，一件小事，或者下一步想做的…" rows="2">${esc(s.captureDraft || "")}</textarea><div class="capture-actions"><label class="muted"><select id="capture-workspace" aria-label="记录所属工作区">${workspaceOptions(workspace)}</select></label><div class="row"><button class="button" type="submit" name="intent" value="memo">${icon("plus")}记下来</button><button class="button primary" type="submit" name="intent" value="chat">开始聊 ${icon("arrow")}</button></div></div></form>`;
  }
  function taskRow(t) {
    return `<button class="task-row" data-go="task/${t.id}/leader"><div class="grow"><div class="task-row-title">${esc(t.title)}</div><p>${esc(t.update)}</p></div>${status(t.state)}${t.fresh ? `<span class="badge">新成果 ${esc(t.version)}</span>` : ""}${icon("chevron")}</button>`;
  }
  function renderHome(workspace) {
    const w = workspace ? ws(workspace) : null;
    const tasks = workspace
      ? s.tasks.filter(
          (t) => t.workspace === workspace || t.related.includes(workspace),
        )
      : s.tasks;
    const featured = workspace ? tasks[0] : model.task("voice");
    const needsDecision =
      featured?.id === "voice" && !s.choices.voice?.submitted;
    $("#main").innerHTML =
      topbar(
        `<span>${w ? "工作区 / " + w.name : "你的工作台"}</span>`,
        `<button class="button small subtle desktop-label" data-action="new" data-workspace="${workspace || "crew"}">${icon("plus")}开始一段对话</button><button class="icon-button" data-action="theme" aria-label="切换深浅主题">${icon("sun")}</button>`,
      ) +
      `<div class="page-scroll" data-scroll><div class="page"><div class="home-intro"><div class="eyebrow">WEDNESDAY, OCTOBER 28</div><h1>${w ? `${esc(w.name)}，继续向前。` : "让想法，继续向前。"}</h1><p>${w ? "从这里继续已有的任务，或和负责人聊一件新事。" : `${CrewSample.workspaces.length} 个工作区各自推进。你可以随时回来，接着聊。`}</p></div>
      ${featured ? `<div class="attention"><button class="attention-card" ${needsDecision ? 'data-action="decision-jump"' : `data-go="task/${featured.id}/leader"`}><div class="card-kicker">${icon("chat")}${needsDecision ? "需要你拍板" : "继续对话"} <span class="mono">${needsDecision ? "02" : "↗"}</span></div><h3>${needsDecision ? "声纹的两个取舍" : esc(featured.title)}</h3><p>${needsDecision ? "回到原任务，一起聊清楚。" : "已有约定和讨论，留在原任务。"}</p>${icon("arrow").replace('class="icon"', 'class="icon arrow"')}</button><button class="attention-card new-result" data-action="open-result" data-task="${featured.id}"><div class="card-kicker">${icon("spark")}${featured.fresh ? "有新成果" : "阶段成果"} <span class="badge">${esc(featured.version || "待形成")}</span></div><h3>${featured.id === "voice" ? "声纹识别，可以试试了" : "看看这个任务的新进展"}</h3><p>查看报告、体验页面与文件。</p>${icon("arrow").replace('class="icon"', 'class="icon arrow"')}</button></div>` : ""}
      <div class="home-grid"><div><div class="section-heading"><h2>${w ? "这个工作区的任务" : "工作在继续"} <span class="muted small">${tasks.length}</span></h2><span class="section-note">任务独立推进</span></div><div class="workspace-cards">${(w
        ? [w]
        : CrewSample.workspaces
      )
        .map(
          (v) =>
            `<section class="workspace-card"><div class="workspace-card-head"><span class="workspace-symbol ${v.color}">${esc(v.initials)}</span><div><h3>${esc(v.name)}</h3><span class="mono">${esc(v.path)}</span></div><span class="count">${tasks.filter((t) => t.workspace === v.id).length} 个任务</span><button class="icon-button" data-action="folder" data-workspace="${v.id}" aria-label="打开${esc(v.name)}文件夹">${icon("folder")}</button></div>${tasks
              .filter(
                (t) => t.workspace === v.id || (w && t.related.includes(v.id)),
              )
              .map(taskRow)
              .join("")}</section>`,
        )
        .join(
          "",
        )}</div>${w ? `<button class="button subtle" data-go="workspace-chat/${w.id}">${icon("chat")}工作区对话 · 安排与协调</button>` : ""}<div class="section-heading"><h2>留住一个想法</h2><span class="section-note">记下，或者从这里开始</span></div>${captureForm(workspace)}<div class="footer-note">${icon("leaf")}样例数据 · 页面操作只保存在此浏览器，不连接正式 Crew</div></div>
      <aside class="home-aside"><section><div class="section-heading"><h2>留给你的小事</h2><button data-go="todos" class="small muted" aria-label="查看全部 TODO">全部 ↗</button></div>${s.todos
        .filter((x) => !x.done && (!workspace || x.workspace === workspace))
        .slice(0, 3)
        .map(
          (t) =>
            `<div class="mini-todo"><button class="todo-check" data-action="todo-check" data-id="${t.id}" aria-label="完成：${esc(t.text)}"></button><div><button data-action="todo-open" data-id="${t.id}">${esc(t.text)}</button><p>${ws(t.workspace).name} · ${t.tags[0]}</p></div></div>`,
        )
        .join(
          "",
        )}</section><section class="assistant-card"><div class="assistant-orbit">${icon("spark")}</div><h3>今天想先聊什么？</h3><p>进展、安排，或还没想清楚的事。个人助手在这里。</p><button class="button" data-go="assistant">和助手聊聊 ${icon("arrow")}</button></section></aside></div></div></div>`;
  }
  function seedChat(c) {
    if (c.id === "assistant")
      return `<div class="message ai"><div class="message-meta"><span class="avatar">${icon("spark")}</span><strong>个人助手</strong><span class="time">全局 · 样例对话</span></div><p>声纹实验室有一个新版本可以体验，也有两个取舍等你决定。Crew 的对话优化和个人网站还在各自推进。</p><p>我们可以先聊安排，也可以把一个新想法记下来。</p><button class="member-inline" data-go="task/voice/leader"><span class="avatar">声</span><span><strong>让声纹识别真正可用</strong><p>2 个待拍板 · 新成果 v0.3.0</p></span>${icon("arrow")}</button><p class="muted small">操作设置、传达要求或停止任务，需要你明确说出意图。</p></div>`;
    if (c.global)
      return `<div class="message ai"><div class="message-meta"><span class="avatar">C</span><strong>工作区负责人</strong><span class="time">工作区总对话 · 样例</span></div><p>这里用来讨论整个工作区、安排和协调任务。各任务对话独立保留，输入不会猜送到某个任务。</p><p>具体任务的进展和成果，仍然回到对应任务查看。</p></div>`;
    if (c.member)
      return `<div class="member-origin">${icon("link")}来自「${esc(c.title)}」 · ${esc(c.leader)} 按需邀请<span class="badge">${c.auto ? "继承：全自动" : "继承：用户把关"}</span></div><div class="message user"><div class="user-body">把这个环节单独验证清楚，结论和边界同步给 leader。</div></div><div class="message ai"><div class="message-meta"><span class="avatar member">${esc(c.name.slice(0, 1))}</span><strong>${esc(c.name)}</strong><span class="time">${esc(c.member.runtime)} · 样例</span></div><p>${esc(c.member.update)}。目前已经完成基础路径，正在整理容易影响实际体验的边界。</p><p>你可以直接在这里补充要求。影响任务整体的内容会保留原话，并同步给 ${esc(c.leader)}。</p><div class="activity-line">${icon("check")}已将阶段结论同步给 leader · 样例事件</div></div>`;
    if (c.id === "voice")
      return `<div class="chat-time">今天 · 10 月 28 日 · 以下均为样例对话</div><div class="message user"><div class="user-body">想把声纹识别真正用起来。本地运行优先，别只是一个跑通的 demo，速度和准确度都要实际试。</div></div><div class="message ai"><div class="message-meta"><span class="avatar">林</span><strong>林序</strong><span class="badge">leader</span><span class="time">09:12 · Claude Code / 模型为样例</span></div><p>我会先把本地流程做到可体验，再验证真实麦克风和短音频的边界。任务核心已整理好，你随时可以修改。</p><div class="activity-line">${icon("check")}任务详情已随对话更新 · 继续按用户把关模式推进</div><button class="member-inline" data-go="task/voice/engine"><span class="avatar member">知</span><span><strong>知衡 <span class="muted small">· 识别引擎</span></strong><p>验证短音频与噪声边界 · Codex</p></span>${status("working")}${icon("chevron")}</button><div class="result-inline"><span class="eyebrow">A new milestone</span><h3>v0.3.0，已经可以体验了。</h3><p>完成本地识别流程与结果反馈。报告会随实验继续更新。</p><div class="row"><button class="button primary small" data-action="panel" data-panel="results">${icon("spark")}查看成果</button><button class="button subtle small" data-action="report" data-task="voice">阅读阶段报告 ${icon("arrow")}</button></div></div><p>有两处会影响使用感受，想和你一起定下来。其他验证会继续，不会等在这里。</p>${decisionBox(c)}</div>`;
    if (c.id.startsWith("sample-")) return "";
    return `<div class="chat-time">今天 · 以下均为样例对话</div><div class="message user"><div class="user-body">${esc(c.goal)}</div></div><div class="message ai"><div class="message-meta"><span class="avatar">${esc(c.leader.slice(0, 1))}</span><strong>${esc(c.leader)}</strong><span class="badge">leader</span><span class="time">样例</span></div><p>${esc(c.update)}。我会保留已确定的方向，先完成可以继续推进的部分。</p><p>你可以随时补充要求，也可以查看任务详情、成员和已有成果。</p>${c.members.map((m) => `<button class="member-inline" data-go="task/${c.id}/${m.id}"><span class="avatar member">${m.name.slice(0, 1)}</span><span><strong>${m.name}</strong><p>${m.update} · ${m.runtime}</p></span>${icon("chevron")}</button>`).join("")}</div>`;
  }
  function decisionBox(c) {
    const choices = s.choices[c.id] || {};
    if (choices.submitted)
      return `<div class="inbox-note">${icon("check")}已模拟提交统一拍板：${esc(choices.speed)}；${esc(choices.network)}。原始选择保留在本任务对话。</div>`;
    return `<section class="decision-box" id="decision-section"><div class="eyebrow">一起定下来 · 2 个问题</div><h3>先保留选择，聊清楚后一起提交。</h3>${[
      {
        key: "speed",
        label: "1. 第一次反馈，优先什么？",
        options: ["先反馈，再精确识别", "等待完整识别结果"],
      },
      {
        key: "network",
        label: "2. 本地效果不足时，如何继续？",
        options: ["继续优化本地方案", "可以研究联网备选"],
      },
    ]
      .map(
        (q) =>
          `<div class="decision-question"><p>${q.label}</p><div class="choice-row">${q.options.map((o) => `<button class="choice ${choices[q.key] === o ? "selected" : ""}" data-action="choice" data-key="${q.key}" data-value="${o}" aria-pressed="${choices[q.key] === o}">${o}</button>`).join("")}</div></div>`,
      )
      .join(
        "",
      )}<div class="decision-footer"><button class="button small" data-action="decide" ${!choices.speed || !choices.network ? "disabled" : ""}>一起拍板 ${icon("check")}</button><span>也可以直接在对话里说。选择会暂存。</span></div></section>`;
  }
  function composer(c) {
    const key = model.keyFor(c.id, c.person),
      queue = s.queues[key] || [],
      stopped = s.stopped[key];
    return `<div class="composer-wrap">${queue.length ? `<div class="queue-strip">${icon("clock")}<span>排队 ${queue.length} 条 · ${esc(queue[0].text)}</span><button data-action="queue-send">发送下一条</button><button data-action="queue-cancel" aria-label="取消第一条排队消息">取消</button></div>` : ""}<form id="chat-form" class="composer">${(s.attachments?.[key] || []).map((f, i) => `<div class="attachment-chip">${icon("file")}${esc(f)}<span class="muted">仅本地样例</span><button type="button" data-action="attachment-remove" data-index="${i}" aria-label="移除附件">×</button></div>`).join("")}<textarea id="message-input" rows="2" aria-label="给${esc(c.name)}发消息" placeholder="${stopped ? "继续聊，会保留之前的成果…" : `随时和${esc(c.name)}说，补充要求或聊聊结果…`}">${esc(model.draft(c.id, c.person))}</textarea><div class="composer-tools"><button type="button" class="icon-button" data-action="attach" aria-label="添加图片或文件" title="添加图片或文件">${icon("attach")}</button><input id="attachment-input" type="file" multiple hidden><span class="composer-hint">${c.member ? "整体要求会同步给 leader" : stopped ? "当前 AI 已停止" : "执行中也可以插话"}</span>${stopped ? "" : `<button type="button" class="stop-ai" data-action="stop-ai" title="只停止${esc(c.name)}，其他 AI 继续">${icon("stop")}停止当前 AI</button>`}<span class="divider"></span><button type="button" class="queue-button" data-action="queue" title="明确排队，稍后再发送">排队发送</button><button class="send-button" type="submit" aria-label="立即发送">${icon("up")}</button></div></form><div class="composer-caption"><span>${c.global ? "样例回复 · 不调用真实 AI" : "仅此对话 · 草稿自动保留 · 样例回复"}</span><span class="shortcut">Enter 发送 · Shift + Enter 换行</span></div></div>`;
  }
  function renderChat() {
    const c = context();
    if (!c) {
      go("home");
      return;
    }
    const key = model.keyFor(c.id, c.person),
      panel = s.panels[key];
    const crumbs = c.global
      ? `<span>${c.id === "assistant" ? "全局 / 个人助手" : ws(c.workspace).name + " / 工作区对话"}</span>`
      : `<button data-go="task/${c.id}/leader">${esc(c.title)}</button><span>›</span><button data-go="task/${c.id}/leader">${esc(c.leader)} <span class="muted">leader</span></button>${c.member ? `<span>›</span><span class="accent">${esc(c.name)}</span>` : ""}`;
    $("#main").innerHTML =
      topbar(
        crumbs,
        `${!c.global ? `<button class="icon-button" data-action="pin" title="置顶任务" aria-label="${c.pinned ? "取消置顶" : "置顶任务"}">${icon("pin")}</button>` : ""}<button class="icon-button" data-action="focus" aria-label="切换专注布局" title="切换专注布局">${icon("panel")}</button>`,
      ) +
      `<div class="task-header"><div class="task-title-line"><h1>${c.member ? `${esc(c.name)} <span class="muted small">/ ${esc(c.member.role)}</span>` : esc(c.title)}</h1>${!c.global ? `<button class="task-stop" data-action="task-stop">${c.state === "stopped" ? "继续任务" : "停止整个任务"}</button>` : ""}</div><div class="task-subline">${status(s.stopped[key] ? "stopped" : c.member ? c.member.state : c.state)}<span class="divider"></span><span>${c.member ? esc(c.member.runtime) : c.global ? "全局助手" : esc(c.leader) + " 负责"}</span>${!c.global ? `<span class="divider"></span><button class="mode-control" role="switch" aria-checked="${c.auto}" data-action="mode" aria-label="本任务全自动决定"><span class="switch-track"></span>全自动决定</button><span class="pill">${ws(c.workspace).name}${c.related.length ? " + " + c.related.length + " 关联工作区" : ""}</span>` : ""}</div>${!c.global ? `<nav class="task-tabs" aria-label="任务内容"><button data-action="panel" data-panel="none" class="${!panel ? "active" : ""}">${icon("chat")}对话</button><button data-action="panel" data-panel="details" class="${panel === "details" ? "active" : ""}">${icon("file")}详情</button><button data-action="panel" data-panel="team" class="${panel === "team" ? "active" : ""}">${icon("team")}成员 <span class="count">${c.members.length}</span></button><button data-action="panel" data-panel="results" class="${panel === "results" ? "active" : ""}">${icon("spark")}成果${c.fresh ? '<span class="badge">新</span>' : ""}</button></nav>` : "<hr>"}</div><div class="chat-scroll" data-scroll tabindex="0" aria-label="对话记录"><div class="chat-column">${seedChat(c)}${(s.messages[key] || []).map((m, i) => `<div class="message ${m.role}">${m.role === "user" ? `<div class="user-body">${esc(m.text)}</div>` : `<div class="message-meta"><span class="avatar">${esc(c.name.slice(0, 1))}</span><strong>${esc(c.name)}</strong><span class="time">模拟回复</span></div><p>${esc(m.text)}</p>`}</div>`).join("")}${s.stopped[key] ? `<div class="chat-stopped">${icon("stop")}已模拟停止 ${esc(c.name)}。${c.state === "stopped" ? "本任务的所有成员也已停止。" : "其他对话 AI 继续，已有成果保留。"}</div>` : ""}</div></div>${composer(c)}`;
    if (panel) renderInspector(c, panel);
    $("#message-input").addEventListener("input", (e) => {
      model.draft(c.id, c.person, e.target.value);
      persist();
    });
    $("#message-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        submitChat(false);
      }
    });
    $("#attachment-input").addEventListener("change", (e) => {
      s.attachments ||= {};
      s.attachments[key] = [
        ...(s.attachments[key] || []),
        ...Array.from(e.target.files).map((f) => f.name),
      ];
      saveView();
      render();
      toast("附件仅保存在样例草稿，不会上传。");
    });
  }
  function renderInspector(c, panel) {
    $("#app").classList.add("has-inspector");
    const titles = {
      details: "任务详情",
      team: "一起做这件事的人",
      results: "成果与文档",
    };
    let body = "";
    if (panel === "details")
      body = `<form id="details-form"><label class="field-label" for="task-title">任务标题</label><input class="editable" id="task-title" name="title" value="${esc(c.title)}" required><label class="field-label" for="task-goal">当前目标</label><textarea class="editable" rows="3" id="task-goal" name="goal" required>${esc(c.goal)}</textarea><label class="field-label" for="task-criteria">条件与完成标准</label><textarea class="editable" rows="4" id="task-criteria" name="criteria">${esc(c.criteria)}</textarea><label class="field-label" for="task-workspace">主工作区</label><select id="task-workspace" name="workspace" class="editable">${workspaceOptions(c.workspace)}</select><div class="inspector-label">关联工作区 · 同一任务，仍由 ${esc(c.leader)} 负责</div>${CrewSample.workspaces
        .filter((w) => w.id !== c.workspace)
        .map(
          (w) =>
            `<label class="row small"><input type="checkbox" name="related" value="${w.id}" ${c.related.includes(w.id) ? "checked" : ""}>${esc(w.name)}</label>`,
        )
        .join(
          "",
        )}<hr><p class="muted small">AI 随交流维护，你也可以直接修改。任务级模式与约定随 leader 或载体切换保留。</p><br><button class="button primary" type="submit">保存样例详情 ${icon("check")}</button></form>`;
    if (panel === "team")
      body = `<div class="inspector-label">负责人 · 优先自己推进，按需邀请成员</div><button class="member-inline" data-go="task/${c.id}/leader"><span class="avatar">${esc(c.leader.slice(0, 1))}</span><span><strong>${esc(c.leader)} <span class="badge">leader</span></strong><p>Claude Code · 模型遵循原生配置（样例）</p></span>${icon("chevron")}</button><div class="inspector-label">成员 · 每个人都有自己的对话</div>${c.members.length ? c.members.map((m) => `<button class="member-inline" data-go="task/${c.id}/${m.id}"><span class="avatar member">${m.name.slice(0, 1)}</span><span class="grow"><strong>${m.name} <span class="muted small">· ${m.role}</span></strong><p>${m.update}</p><span class="small muted">${m.runtime} · ${statusNames[m.state]}</span></span>${icon("chevron")}</button>`).join("") : '<p class="muted small">暂未启动成员，leader 正在自己推进。</p>'}<div class="inspector-label">团队关系 · 节点可以进入对话</div><div class="member-graph"><button class="graph-leader" data-go="task/${c.id}/leader">${c.leader} · leader</button>${c.members.length ? `<div class="graph-line"></div><div class="graph-children">${c.members.map((m) => `<button data-go="task/${c.id}/${m.id}">${m.name}</button>`).join("")}</div>` : ""}</div><div class="inspector-label">内部协调</div><button class="button" data-action="coordination">${icon("chat")}查看协作记录</button>`;
    if (panel === "results")
      body = c.version
        ? `<div class="inspector-label">当前阶段 · ${c.state === "stopped" ? "执行已停止，成果仍保留" : "等待体验不妨碍其他工作推进"}</div><section class="artifact-card"><div class="row between"><span class="badge">${esc(c.version)} · ${c.fresh ? "新成果" : "阶段成果"}</span><span class="small muted">第 2 轮 · 需求 r3</span></div><h3>${esc(c.title)} · 阶段报告</h3><p>结论、取舍、验证与下一步，随实质进展持续更新。</p><button class="button primary" data-action="report" data-task="${c.id}">${icon("file")}打开格式化报告 ${icon("arrow")}</button><div class="artifact-links"><a href="preview.html?task=${c.id}" target="_blank" rel="noopener">${icon("external")}体验网页</a><button data-action="file">${icon("file")}文件</button><button data-action="folder" data-workspace="${c.workspace}">${icon("folder")}文件夹</button></div></section><div class="inspector-label">已保留的版本 · 均为样例</div>${[
            c.version,
            "v0.2.0",
            "v0.1.0",
          ]
            .filter((v, i, a) => a.indexOf(v) === i)
            .map(
              (v, i) =>
                `<div class="version-row"><div><strong class="mono">${v}</strong><p>${i === 0 ? "当前阶段 · 报告持续更新" : "历史交付 · 文件与记录关联"}</p></div><button class="button small" data-action="restore" data-version="${v}" data-task="${c.id}">恢复到此版本</button></div>`,
            )
            .join(
              "",
            )}<div class="inspector-label">相关说明</div><button class="member-inline" data-action="report" data-task="${c.id}"><span class="avatar">${icon("file")}</span><span><strong>使用说明与边界</strong><p>任务内阅读 · 来源对话可回溯</p></span>${icon("chevron")}</button>`
        : '<div class="empty-state">任务刚刚开始。<br>有可体验的阶段成果时，会在这里出现。</div>';
    const host = $("#inspector");
    host.hidden = false;
    host.innerHTML = `<div class="inspector-header"><div><h2>${titles[panel]}</h2><p>${esc(c.title)} · ${esc(c.leader)} 负责</p></div><button class="icon-button" data-action="panel-close" aria-label="关闭资料，返回对话">${icon("close")}</button></div><div class="inspector-body">${body}</div><div class="inspector-footer">${panel === "details" ? "内容归属本任务 · 由 AI 维护，用户可修改" : panel === "team" ? "点击任意成员，进入本人对话" : "成果版本为样例，不代表 Crew 真实版本"}</div>`;
    const panelKey = model.keyFor(c.id, c.person) + ":" + panel;
    host.querySelector(".inspector-body").scrollTop =
      s.positions[panelKey] || 0;
    host.querySelector(".inspector-body").addEventListener("scroll", (e) => {
      s.positions[panelKey] = e.target.scrollTop;
    });
  }
  function reportBody(t) {
    if (t.id !== "voice")
      return `<article class="report-page"><div class="report-cover"><span class="eyebrow">WORK IN PROGRESS</span><h1>${esc(t.title)}</h1><div class="row"><span class="badge">${esc(t.version)} · 样例阶段报告</span><span>${esc(t.leader)} 负责 · 需求 r1 · 稿次 1</span></div></div><div class="report-callout">${esc(t.update)}。本报告只归属「${esc(t.title)}」，阶段成果与历史记录保留在本任务中。</div><h2>当前目标</h2><p>${esc(t.goal)}</p><h2>完成标准</h2><p>${esc(t.criteria)}</p><h2>团队与验证</h2><p>${esc(t.leader)} 对整体结果负责。${t.members.length ? t.members.map((m) => `${esc(m.name)}：${esc(m.update)}。`).join("") : "当前由 leader 自己推进。"}</p><table><thead><tr><th>范围</th><th>状态</th></tr></thead><tbody><tr><td>当前阶段</td><td>${statusNames[t.state]}（样例）</td></tr><tr><td>载体与文件</td><td>未连接，原型不读取真实配置</td></tr><tr><td>体验反馈</td><td>返回本任务继续沟通</td></tr></tbody></table><h2>成果入口</h2><a class="button primary" href="preview.html?task=${esc(t.id)}" target="_blank" rel="noopener">打开本任务样例体验页 ${icon("external")}</a><h2>下一步</h2><p>继续围绕当前目标推进，已有结论和验证保留。报告更新不会结束任务。</p><p class="report-source">以上为明确标注的样例，未操作真实文件、数据库或 AI。</p><hr><button class="button" data-action="report-back">${icon("back")}回原对话，继续反馈</button></article>`;
    return `<article class="report-page"><div class="report-cover"><span class="eyebrow">WORK IN PROGRESS / 02</span><h1>${esc(t.title)}</h1><div class="row"><span class="badge">${esc(t.version)} · 样例阶段报告</span><span>需求 r3 · 稿次 2</span><span>林序 · 10 月 28 日 09:42</span></div></div><div class="report-callout">本地流程已经可以体验。当前版本完成了从输入到识别结果的完整反馈，下一步验证真实使用中的噪声与短音频。两项体验取舍仍在原任务对话中，其他验证继续推进。</div><h2>这次，可以体验什么</h2><p>打开样例体验页，尝试一次输入、等待与结果反馈。界面会明确显示当前阶段；没有把“正在处理”包装成无法验证的精确进度。</p><a class="button primary" href="preview.html" target="_blank" rel="noopener">打开样例体验页 ${icon("external")}</a><h2>一个更连续的流程</h2><p>先让你知道发生了什么，再呈现结果和下一步。所有状态在界面上都有明确去处。</p><div class="flow-diagram" role="img" aria-label="输入音频，进行本地识别，显示结果，提交体验反馈"><span>选择音频</span>${icon("arrow")}<span>本地识别</span>${icon("arrow")}<span>结果与反馈</span></div><h2>阶段变化</h2><table><thead><tr><th>版本</th><th>已经做到</th><th>保留的问题</th></tr></thead><tbody><tr><td class="mono">v0.1.0</td><td>基础流程可运行</td><td>等待状态不清晰</td></tr><tr><td class="mono">v0.2.0</td><td>增加状态与结果反馈</td><td>短音频仍需验证</td></tr><tr><td class="mono">v0.3.0</td><td>可连续体验与反馈</td><td>真实麦克风体验</td></tr></tbody></table><h2>反馈时机的实验</h2><p>示意图用于体验报告中的可视化阅读。以下是人为构造的样例数字，不是实际测量结果。</p><div class="report-chart" role="img" aria-label="样例首次反馈时间：原流程 1.8秒，阶段方案 0.86秒；非真实测量"><div class="chart-bar"><span>原流程</span><div class="chart-track"><div class="chart-fill baseline full"></div></div><b>1.8 s</b></div><div class="chart-bar"><span>阶段方案</span><div class="chart-track"><div class="chart-fill partial"></div></div><b>0.86 s</b></div><div class="chart-label">演示数字 · 仅为格式化图表样例</div></div><h2>仍需你体验的部分</h2><p>用自己的麦克风试一段声音，判断反馈是否及时、结果是否符合预期。这件事已放进同一张 TODO 清单，保留任务入口。</p><button class="button" data-go="todos">打开 TODO 与灵感 ${icon("arrow")}</button><h2>验证与边界</h2><p>已验证的路径保留结论，新改动只复验受影响部分。真实项目应记录测试方法、原始原因与实际结果；原型展示的状态不能作为工程验证依据。</p><details><summary class="small">查看样例验证记录</summary><table><tbody><tr><td>基础流程</td><td>样例：可体验</td></tr><tr><td>短音频与噪声</td><td>样例：验证中</td></tr><tr><td>用户实际感受</td><td>待体验，回原任务继续反馈</td></tr></tbody></table></details><h2>下一步</h2><p>由知衡继续验证边界；林序汇合结论并维护本报告。报告更新不会结束任务，也不会改变你的手动停止状态。</p><p class="report-source">本文为明确标注的样例内容。未读取真实文件、数据库或本机 AI 配置。</p><hr><button class="button" data-action="report-back">${icon("back")}回原对话，继续反馈</button></article>`;
  }
  function renderReport() {
    const t = s.tasks.find((t) => t.id === parts[1]);
    if (!t) {
      go("home");
      return;
    }
    t.fresh = false;
    $("#main").innerHTML =
      topbar(
        `<button data-action="report-back" aria-label="返回原对话">${icon("back")}</button><button data-go="task/${t.id}/leader">${esc(t.title)}</button><span>›</span><span>阶段报告</span>`,
        `<button class="button small subtle" data-action="report-update">模拟收到更新 ${icon("spark")}</button>`,
      ) +
      `<div id="report-update" class="report-update" hidden><span>报告已有新稿次，你正在阅读的内容与位置已保留。</span><button data-action="report-new">查看新版</button></div><div class="report-shell" data-scroll>${reportBody(t)}</div>`;
  }
  function renderTodos() {
    const items = s.todos.filter(
      (t) =>
        todoFilter === "all" ||
        (todoFilter === "done" ? t.done : t.workspace === todoFilter),
    );
    $("#main").innerHTML =
      topbar("<span>个人 / TODO 与灵感</span>") +
      `<div class="page-scroll" data-scroll><div class="page todo-page"><div class="page-heading"><div><div class="eyebrow">A place for the little things</div><h1>TODO 与灵感</h1><p>要亲自动手的事，和还没成形的想法，放在一起。</p></div></div>${captureForm()}<div class="filters">${[{ id: "all", name: "全部" }, ...CrewSample.workspaces, { id: "done", name: "已完成" }].map((f) => `<button class="filter ${todoFilter === f.id ? "active" : ""}" data-action="todo-filter" data-filter="${f.id}">${f.name}</button>`).join("")}</div>${items.map((t) => `<section class="todo-row ${t.done ? "completed" : ""}"><button class="todo-check ${t.done ? "done" : ""}" data-action="todo-check" data-id="${t.id}" aria-label="${t.done ? "标为未完成" : "完成"}：${esc(t.text)}">${t.done ? icon("check") : ""}</button><div class="grow"><h3>${esc(t.text)}</h3><div class="todo-meta"><span>${ws(t.workspace).name}</span>${t.tags.map((tag) => `<span class="chip">${esc(tag)}</span>`).join("")}</div><p class="todo-note">${esc(t.note)}</p></div><div class="todo-actions">${t.task ? `<button class="button subtle small" data-go="task/${t.task}/leader">原任务 ↗</button>` : `<button class="button subtle small" data-action="todo-chat" data-id="${t.id}">开始聊 ↗</button>`}<button class="icon-button" data-action="todo-open" data-id="${t.id}" aria-label="事项详情">${icon("more")}</button></div></section>`).join("")}<p class="footer-note">${icon("bell")}新增事项会模拟 QQ / Lark 提醒；没有发送真实通知。</p></div></div>`;
  }
  function renderMore() {
    const cards = [
      ["channel", "公共频道", "可选的跨团队交流，摘要按需生成。", "chat"],
      ["git", "Git", "按工作区查看改动、分支与提交。", "git"],
      ["pinned", "置顶", "常用任务与重要消息，随时回来。", "pin"],
      ["usage", "用量与额度", "查看两个载体的用量和可用额度。", "sun"],
      ["sessions", "会话库", "待接入、团队中、历史归档与后台执行。", "team"],
      ["history", "历史", "保留完成的任务、过去的轮次与交付。", "clock"],
      ["trash", "垃圾箱", "恢复记录后，由你明确继续执行。", "archive"],
      [
        "operations",
        "后台与运维",
        "连接、本机数据、恢复与按需排障。",
        "settings",
      ],
    ];
    $("#main").innerHTML =
      topbar("<span>更多工作能力</span>") +
      `<div class="page-scroll" data-scroll><div class="page"><div class="page-heading"><div><div class="eyebrow">Everything has its place</div><h1>熟悉的能力，都在。</h1><p>主路径保持轻盈，需要时从这里进入。</p></div></div><div class="library-grid">${cards.map(([id, title, desc, i]) => `<button class="library-card" data-go="utility/${id}"><div class="row between">${icon(i)}${icon("chevron")}</div><h3>${title}</h3><p>${desc}</p><span class="small accent">打开样例视图 ↗</span></button>`).join("")}</div></div></div>`;
  }
  function renderUtility() {
    const name =
      {
        channel: "公共频道",
        git: "Git",
        pinned: "置顶",
        usage: "用量与额度",
        sessions: "会话库",
        history: "历史",
        trash: "垃圾箱",
        operations: "后台与运维",
      }[parts[1]] || "更多";
    let body = "";
    if (parts[1] === "pinned")
      body =
        s.tasks
          .filter((t) => t.pinned)
          .map(taskRow)
          .join("") ||
        '<div class="empty-state">在任务顶部点击图钉，置顶任务。</div>';
    if (parts[1] === "channel")
      body = `<p class="muted small">可选交流入口 · 样例消息，未连接任何真实频道</p><div class="channel-message"><div class="message-meta"><span class="avatar">夏</span><strong>夏木</strong><span class="muted">Crew · leader</span></div><p>我在调整任务切换，不会碰声纹实验的文件。</p></div><div class="channel-message"><div class="message-meta"><span class="avatar">林</span><strong>林序</strong><span class="muted">声纹实验室 · leader</span></div><p>收到。跨工作区的体验入口已关联到同一任务。</p></div><br><button class="button" data-action="channel-summary">${icon("spark")}按需生成摘要</button><br><br><form id="channel-form" class="quick-capture"><textarea aria-label="公共频道消息" placeholder="在公共频道说点什么…" rows="2"></textarea><button class="button" type="submit">发送样例消息</button></form>`;
    if (parts[1] === "git")
      body = `<label class="field-label">工作区<select class="editable" aria-label="Git 工作区">${workspaceOptions("crew")}</select></label><div class="inbox-note">样例仓库 · codex/interaction · 只读展示，不执行 Git 操作。</div><table class="utility-table"><tbody><tr><td class="mono">M</td><td>src/navigation.js</td><td class="accent">+24 −8</td></tr><tr><td class="mono">M</td><td>src/theme.css</td><td class="accent">+12 −3</td></tr></tbody></table><hr><p class="muted small">普通文件夹也可以作为工作区，只有使用 Git 功能时才需要仓库。</p>`;
    if (parts[1] === "usage")
      body = `<div class="library-grid">${["Claude Code", "Codex"].map((n) => `<section class="library-card"><h3>${n}</h3><p>原生提供的用量与额度</p><strong>样例环境未连接</strong><p>不推测额度，不把空值当作剩余为零。</p><button class="button small" data-action="usage-refresh">读取原生状态（模拟）</button></section>`).join("")}</div>`;
    if (parts[1] === "sessions")
      body = `<div class="filters">${["待接入", "团队中", "历史归档", "后台执行", "全部"].map((n, i) => `<button class="filter ${i === 0 ? "active" : ""}" data-action="session-filter">${n}</button>`).join("")}</div><div id="session-samples"><button class="member-inline" data-action="session-preview"><span class="avatar member">C</span><span><strong>旧的识别实验</strong><p>Codex · 声纹实验室 · 样例原生会话</p></span><span class="badge">只读预览</span>${icon("chevron")}</button></div>`;
    if (parts[1] === "history")
      body = `<p class="muted small">已归档的任务与原始对话仍可查看，恢复记录不会启动执行。</p><br><button class="member-inline" data-action="historical"><span class="avatar">${icon("archive")}</span><span><strong>声纹方案探索 · 第一阶段</strong><p>2026-10-26 · 已归档 · 保留 v0.1 成果</p></span>${icon("chevron")}</button>`;
    if (parts[1] === "trash")
      body = `<p class="muted small">删除仅移入可恢复的垃圾箱，项目文件夹和成果文件保留。</p><br><div class="member-inline"><span class="avatar">${icon("archive")}</span><span class="grow"><strong>早期首页探索</strong><p>样例记录 · 执行已停止</p></span><button class="button small" data-action="trash-restore">恢复记录</button></div><hr><button class="button danger small" data-action="trash-clear">永久清空所选样例</button><p class="footer-note">永久清空由用户手动操作，个人助手不能代替清空。</p>`;
    if (parts[1] === "operations")
      body = `<div class="library-card"><h3>独立样例环境</h3><p>未检查正式后台、home 或任何运行中服务。</p><div class="row"><span class="badge">正式服务状态未确认</span><button class="button small" data-go="settings">连接与本机设置</button></div></div><hr><p class="muted small">程序负责保活与基础恢复。需要分析故障时，再按需进入运维对话。</p><br><button class="button" data-action="operation-chat">查看运维样例</button>`;
    $("#main").innerHTML =
      topbar(
        `<button data-go="more" aria-label="返回更多能力">${icon("back")}</button><button data-go="more">更多</button><span>›</span><span>${name}</span>`,
      ) +
      `<div class="page-scroll" data-scroll><div class="page"><div class="page-heading"><div><div class="eyebrow">EXISTING CAPABILITIES</div><h1>${name}</h1><p>已有能力的入口安排 · 以下均为样例</p></div></div>${body}</div></div>`;
  }
  function renderNew() {
    const workspace = parts[1] || "crew";
    $("#main").innerHTML =
      topbar(
        `<button data-go="home" aria-label="返回首页">${icon("back")}</button><span>${ws(workspace).name} / 新的对话</span>`,
      ) +
      `<div class="page-scroll" data-scroll><div class="page new-page"><div class="assistant-orbit">${icon("spark")}</div><div class="page-heading"><div><h1>先说说，你想做什么？</h1><p>不必先填任务表。从一句话开始，AI 会和你一起理清。</p></div></div><form id="new-chat-form" class="quick-capture"><textarea id="new-input" rows="5" aria-label="新任务需求" placeholder="想解决一个问题，或者试试一个新想法…">${esc(s.newDraft || "")}</textarea><div class="capture-actions"><select id="new-workspace" aria-label="新对话主工作区">${workspaceOptions(workspace)}</select><button class="button primary" type="submit">开始聊 ${icon("arrow")}</button></div></form><div class="footer-note">${icon("info")}使用预设样例回复，不启动 Claude Code 或 Codex。</div></div></div>`;
    $("#new-input").addEventListener("input", (e) => {
      s.newDraft = e.target.value;
      persist();
    });
  }
  function renderSettings() {
    $("#main").innerHTML =
      topbar(
        `<button data-action="settings-back" aria-label="返回之前的对话">${icon("back")}</button><span>设置</span>`,
      ) + `<div id="settings-host" class="page-scroll" data-scroll></div>`;
    const last = s.settingsReturn?.split("/");
    const t = ["task", "report"].includes(last?.[0])
      ? s.tasks.find((t) => t.id === last[1])
      : s.tasks[0];
    settingsCleanup = PrototypeSettings.render($("#settings-host"), {
      toast,
      close: () => go(s.settingsReturn || "home"),
      taskId: t?.id,
      taskTitle: t?.title,
      workspaceName: ws(t?.workspace).name,
      workspaceId: t?.workspace,
      leaderName: t?.leader,
      members: t?.members || [],
      icon,
      escape: esc,
      embedded: true,
    });
  }
  function render() {
    settingsCleanup?.();
    settingsCleanup = null;
    const next = location.hash.slice(1) || "home";
    if (next === "settings" && route !== "settings")
      s.settingsReturn = route || "home";
    route = next;
    parts = route.split("/");
    $("#app").classList.remove("has-inspector");
    $("#inspector").hidden = true;
    closeNavigation();
    if (parts[0] === "home") renderHome();
    else if (parts[0] === "workspace") renderHome(parts[1]);
    else if (["task", "assistant", "workspace-chat"].includes(parts[0]))
      renderChat();
    else if (parts[0] === "report") renderReport();
    else if (parts[0] === "todos") renderTodos();
    else if (parts[0] === "settings") renderSettings();
    else if (parts[0] === "new") renderNew();
    else if (parts[0] === "more") renderMore();
    else if (parts[0] === "utility") renderUtility();
    else {
      location.hash = "home";
      return;
    }
    renderSidebar();
    syncAccessibility();
    restoreView();
    persist();
    document.querySelectorAll("#main details").forEach((detail, index) => {
      s.expanded ||= {};
      detail.open = Boolean(s.expanded[route + ":" + index]);
      detail.addEventListener("toggle", () => {
        s.expanded[route + ":" + index] = detail.open;
        persist();
      });
    });
    const area = $("[data-scroll]");
    area?.addEventListener(
      "scroll",
      () => {
        s.positions[route] = area.scrollTop;
      },
      { passive: true },
    );
  }
  function panel(name) {
    const c = context();
    if (!c || c.global) return;
    saveView();
    const key = model.keyFor(c.id, c.person);
    s.panels[key] = name === "none" || s.panels[key] === name ? null : name;
    render();
    if (name !== "none" && s.panels[key])
      requestAnimationFrame(() =>
        $("#inspector button")?.focus({ preventScroll: true }),
      );
  }
  function submitChat(queued) {
    const c = context();
    if (!c) return;
    const input = $("#message-input"),
      key = model.keyFor(c.id, c.person),
      files = s.attachments?.[key] || [];
    const text =
      input.value +
      (files.length ? "\n附件（本地样例）：" + files.join("、") : "");
    if (!text.trim()) {
      toast("先写一句话，再选择立即或排队发送。");
      input.focus();
      return;
    }
    model.send(c.id, c.person, text, queued);
    if (s.attachments) delete s.attachments[key];
    if (!queued) {
      const key = model.keyFor(c.id, c.person);
      (s.messages[key] ||= []).push({
        role: "ai",
        text:
          c.state === "stopped"
            ? "补充已保留。本任务保持手动停止，需要你明确点击继续任务才恢复执行。（预设样例回复）"
            : c.member
              ? `已收到你的补充：「${text}」。涉及整体任务的要求会保留原话并同步给 ${c.leader}。（预设样例回复）`
              : `收到，我会把这条补充留在当前对话，继续整理下一步。（预设样例回复，不调用 AI）`,
      });
      s.positions[route] = Number.MAX_SAFE_INTEGER;
    }
    persist();
    render();
    toast(
      queued
        ? "已加入此对话的待发队列；立即插话仍可使用。"
        : "已模拟立即送达当前 AI。",
    );
  }
  function showDialog(title, body, button = "知道了", action) {
    dialogAction = action;
    $("#dialog").innerHTML =
      `<h2 id="dialog-title">${title}</h2>${body}<div class="dialog-footer">${action ? '<button class="button" data-action="dialog-cancel">取消</button>' : ""}<button class="button primary" data-action="dialog-confirm">${button}</button></div>`;
    $("#dialog").showModal();
  }
  function openReport(id) {
    saveView();
    s.reportReturn ||= {};
    s.reportReturn[id] = route.startsWith("task/")
      ? route
      : `task/${id}/leader`;
    go(`report/${id}`);
  }
  const actions = {
    nav() {
      const on = !$("#app").classList.contains("nav-open");
      $("#app").classList.toggle("nav-open", on);
      $("#nav-backdrop").hidden = !on;
      syncAccessibility();
      if (on) $("#sidebar .brand button").focus();
    },
    focus() {
      $("#app").classList.toggle("focus");
      if (matchMedia("(max-width:700px)").matches) actions.nav();
      syncAccessibility();
    },
    theme() {
      PrototypeSettings.toggleTheme();
    },
    guide() {
      showDialog(
        "在 Crew 里，走一小段。",
        `<p>这是交互设计原型 v1，所有数据和反馈均为样例。你可以自由修改，刷新后仍会保留。</p>${[
          ["首页 → 任务", "点「声纹的两个取舍」，回到原任务讨论。"],
          [
            "任务 → 成员 → 返回",
            "进入知衡的对话，再点顶部的林序或任务标题返回。",
          ],
          ["成果 → 体验 → 反馈", "查看 v0.3.0 报告，打开样例网页，再回对话。"],
          [
            "顺手试试",
            "切换任务前写一句草稿；展开资料或设置后再回来。手机也能切模式、拍板和设置。",
          ],
        ]
          .map(
            (x, i) =>
              `<div class="guide-step"><b>${i + 1}</b><div><strong>${x[0]}</strong><p>${x[1]}</p></div></div>`,
          )
          .join("")}`,
        "开始体验",
      );
      $("#dialog .dialog-footer").insertAdjacentHTML(
        "afterbegin",
        '<a class="button" href="mobile.html" target="_blank" rel="noopener">390px 手机预览 ↗</a><button class="button subtle" data-action="reset-samples">重置样例</button>',
      );
    },
    new(el) {
      go("new/" + (el.dataset.workspace || "crew"));
    },
    "workspace-add"() {
      showDialog(
        "添加普通文件夹工作区",
        '<p>正式产品中可以选择或创建普通文件夹，也可以导入工作区包；无需自动初始化 Git。</p><label class="field-label" for="sample-ws-name">样例工作区名称</label><input id="sample-ws-name" class="editable" value="我的小工具" maxlength="40">',
        "添加样例",
        () => {
          const name = $("#sample-ws-name").value.trim();
          if (!name) return;
          CrewSample.workspaces.push({
            id: "local-" + Date.now(),
            name,
            initials: name.slice(0, 1),
            color: "sage",
            path: "D:\\Projects\\SampleFolder",
          });
          render();
          toast("已添加本次体验的样例文件夹，不创建真实目录。");
        },
      );
    },
    "decision-jump"() {
      s.panels[model.keyFor("voice", "leader")] = null;
      go("task/voice/leader");
      requestAnimationFrame(() =>
        requestAnimationFrame(() =>
          $("#decision-section")?.scrollIntoView({ block: "center" }),
        ),
      );
    },
    "open-result"(el) {
      s.panels[model.keyFor(el.dataset.task, "leader")] = "results";
      go(`task/${el.dataset.task}/leader`);
    },
    panel(el) {
      panel(el.dataset.panel);
    },
    "panel-close"() {
      panel("none");
      requestAnimationFrame(() =>
        $("#message-input")?.focus({ preventScroll: true }),
      );
    },
    pin() {
      const c = context();
      if (!c || c.global) return;
      model.task(c.id).pinned = !c.pinned;
      saveView();
      render();
      toast(c.pinned ? "已取消样例置顶" : "已置顶，可以从更多 → 置顶找到。");
    },
    mode() {
      const c = context();
      if (!c || c.global) return;
      saveView();
      model.task(c.id).auto = !c.auto;
      render();
      toast(
        !c.auto
          ? "本任务的关键取舍交给 AI，成员继承；原生执行权限仍在设置。"
          : "本任务切回用户把关，重要取舍回对话讨论。",
      );
    },
    "stop-ai"() {
      const c = context();
      saveView();
      model.stopConversation(c.id, c.person);
      render();
      toast("已模拟停止当前对话 AI；没有停止其他 AI。");
    },
    "task-stop"() {
      const c = context();
      if (c.state === "stopped") {
        model.resumeTask(c.id);
        render();
        toast("已模拟继续本任务及其成员。");
        return;
      }
      showDialog(
        "停止整个任务？",
        `<p>「${esc(c.title)}」及 ${c.members.length} 位成员都会停止。已有文件和成果保留；其他任务继续。</p><p>这里只模拟状态变化。</p>`,
        "停止任务及成员",
        () => {
          saveView();
          model.stopTask(c.id);
          render();
          toast("本任务及全部成员已模拟停止，成果保留。");
        },
      );
    },
    queue() {
      submitChat(true);
    },
    "queue-send"() {
      const c = context(),
        key = model.keyFor(c.id, c.person),
        m = s.queues[key]?.shift();
      if (!m) return;
      saveView();
      const preserved = model.draft(c.id, c.person);
      model.send(c.id, c.person, m.text);
      model.draft(c.id, c.person, preserved);
      render();
      toast("已模拟发送下一条排队消息；当前草稿保留。");
    },
    "queue-cancel"() {
      const c = context();
      s.queues[model.keyFor(c.id, c.person)].shift();
      saveView();
      render();
      toast("已取消第一条样例排队消息。");
    },
    choice(el) {
      const c = context();
      s.choices[c.id] ||= {};
      s.choices[c.id][el.dataset.key] = el.dataset.value;
      saveView();
      render();
    },
    decide() {
      const c = context();
      s.choices[c.id].submitted = true;
      saveView();
      render();
      toast("已模拟统一拍板，结论留在原任务。");
    },
    report(el) {
      openReport(el.dataset.task);
    },
    "report-back"() {
      go(s.reportReturn?.[parts[1]] || `task/${parts[1]}/leader`);
    },
    "report-update"() {
      model.task(parts[1]).fresh = true;
      renderSidebar();
      persist();
      $("#report-update").hidden = false;
      toast("样例报告有更新。当前稿次和阅读位置未改变。");
    },
    "report-new"() {
      model.task(parts[1]).fresh = false;
      renderSidebar();
      persist();
      const position = $("[data-scroll]").scrollTop;
      $("#report-update").hidden = true;
      $(".report-cover .row").insertAdjacentHTML(
        "beforeend",
        '<span class="badge">稿次 3 · 新版样例</span>',
      );
      $("[data-scroll]").scrollTop = position;
      toast("已打开样例新版，保留阅读位置。");
    },
    restore(el) {
      showDialog(
        "恢复到 " + esc(el.dataset.version),
        `<p>向本任务 leader 明确提出恢复目标。实际恢复需要对应文件记录，并协调保留其他任务的修改。</p><p>本原型仅把请求放入原任务对话，不改变任何真实文件。</p>`,
        "模拟发送恢复要求",
        () => {
          const id = el.dataset.task,
            text = `请恢复到 ${el.dataset.version} 的效果，保留其他并行任务的改动。`;
          const keptDraft = model.draft(id, "leader");
          model.send(id, "leader", text);
          model.draft(id, "leader", keptDraft);
          go(`task/${id}/leader`);
          toast("恢复要求已模拟发送，未执行真实恢复。");
        },
      );
    },
    folder(el) {
      showDialog(
        "文件夹入口 · 样例",
        `<p class="mono">${esc(ws(el.dataset.workspace).path)}</p><p>PC 正式产品在这里打开项目文件夹。手机保留路径和相关成果链接。</p><p>原型不会打开或修改你的真实目录。</p>`,
      );
    },
    file() {
      showDialog(
        "阶段成果文件",
        `<p>样例文件 · ${esc(context()?.title || "阶段成果")}-${esc(context()?.version || "v0.1.0")}.zip</p><p>正式产品可打开文件或进入所在文件夹。原型提供本地格式化报告供体验。</p>`,
        "阅读样例报告",
        () => openReport(context()?.id || "voice"),
      );
    },
    coordination() {
      showDialog(
        "任务内部协调 · 样例",
        "<p><strong>知衡 → 林序</strong><br>短音频边界验证继续推进，已有体验页保持可用。</p><p><strong>林序 → 夏木（另一任务 leader）</strong><br>跨工作区入口引用同一任务，不复制成果记录。</p><p>影响任务的结论保留在任务记录。内部交流按需查看。</p>",
      );
    },
    attach() {
      $("#attachment-input").click();
    },
    "attachment-remove"(el) {
      const c = context();
      s.attachments[model.keyFor(c.id, c.person)].splice(
        Number(el.dataset.index),
        1,
      );
      saveView();
      render();
    },
    "todo-check"(el) {
      const t = model.completeTodo(el.dataset.id);
      saveView();
      render();
      toast(
        t.done
          ? "已完成样例 TODO；关联任务收到模拟通知，手动停止仍保持。"
          : "已标记为未完成。",
      );
    },
    "todo-filter"(el) {
      todoFilter = el.dataset.filter;
      saveView();
      render();
    },
    "todo-chat"(el) {
      const t = s.todos.find((t) => t.id === el.dataset.id);
      s.newDraft = t.text;
      go("new/" + (t.workspace || "crew"));
    },
    "todo-open"(el) {
      const t = s.todos.find((t) => t.id === el.dataset.id);
      showDialog(
        "TODO 与灵感",
        `<p><strong>${esc(t.text)}</strong></p><p>${ws(t.workspace).name} · ${t.tags.map(esc).join(" / ")}</p><p>${esc(t.note)}</p><label for="todo-comment" class="field-label">补充完成说明，或说明做不了的原因</label><textarea id="todo-comment" rows="3" class="editable" placeholder="保留给相关 leader 的说明…">${esc(t.comment || "")}</textarea>`,
        "保存说明",
        () => {
          t.comment = $("#todo-comment").value;
          persist();
          toast("说明已保存在此样例事项；可以回原任务继续沟通。");
        },
      );
    },
    "settings-back"() {
      go(s.settingsReturn || "home");
    },
    "channel-summary"() {
      toast("样例摘要：两个任务已协调文件范围，声纹边界验证继续推进。");
    },
    "usage-refresh"() {
      toast("样例环境没有接入原生用量，未发出请求。");
    },
    "session-filter"(el) {
      document
        .querySelectorAll(".filters .filter")
        .forEach((x) => x.classList.toggle("active", x === el));
      $("#session-samples").innerHTML =
        `<div class="inbox-note">${esc(el.textContent)} · 样例筛选</div><button class="member-inline" data-action="session-preview"><span class="avatar member">C</span><span><strong>识别实验记录</strong><p>独立原生会话 · 只读样例</p></span>${icon("chevron")}</button>`;
    },
    "session-preview"() {
      showDialog(
        "会话只读预览 · 样例",
        "<p>原始对话和载体来源保留。接入团队需要明确选择任务与角色。</p><p>这里没有接管真实会话，也不会启动 AI。</p>",
        "模拟接入",
        () => toast("已模拟选择接入；正式接管能力未连接。"),
      );
    },
    historical() {
      showDialog(
        "历史任务 · 样例",
        "<p>原始需求、对话、v0.1 交付和验收记录保留。查看历史不会恢复执行。</p>",
      );
    },
    "trash-restore"() {
      toast("已模拟恢复记录，执行仍保持停止。");
    },
    "trash-clear"() {
      showDialog(
        "永久清空所选样例？",
        "<p>正式产品只能由用户手动永久清空 Crew 记录。代码、文件夹和成果文件保留。</p><p>本次只演示这一确认步骤。</p>",
        "模拟永久清空",
        () => toast("已模拟清空所选记录，未删除真实数据。"),
      );
    },
    "operation-chat"() {
      showDialog(
        "按需运维对话 · 样例",
        "<p>程序已做少量基础恢复尝试，仍无法连接时保留真实原因。需要你明确安排后，才进入 AI 分析。</p><p>原型未读取进程、home 或正式后台。</p>",
      );
    },
    "reset-samples"() {
      showDialog(
        "重置此原型的样例？",
        "<p>清除本原型的样例草稿、选择和设置，重新开始体验。只影响 crew-design-* 的两个浏览器存储键。</p><p>不会连接正式 Crew，也不影响任何真实文件或数据。</p>",
        "重置样例",
        () => {
          resetting = true;
          localStorage.removeItem(STORAGE);
          localStorage.removeItem("crew-design-settings-v1");
          history.replaceState(null, "", "#home");
          location.reload();
        },
      );
    },
    "dialog-cancel"() {
      $("#dialog").close();
    },
    "dialog-confirm"() {
      const fn = dialogAction;
      fn?.();
      $("#dialog").close();
      dialogAction = null;
    },
  };
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-go],[data-action]");
    if (!el) return;
    if (el.dataset.go) {
      go(el.dataset.go);
      return;
    }
    actions[el.dataset.action]?.(el);
  });
  document.addEventListener("submit", (e) => {
    if (e.target.id === "chat-form") {
      e.preventDefault();
      submitChat(false);
    }
    if (e.target.id === "capture-form") {
      e.preventDefault();
      const text = $("#capture-input").value,
        workspace = $("#capture-workspace").value;
      if (!text.trim()) {
        toast("先写下一个想法。");
        return;
      }
      s.captureDraft = "";
      if (e.submitter?.value === "chat") {
        const id = model.newTask(text, workspace || "crew");
        go("task/" + id + "/leader");
        toast("已从对话形成样例任务，无需先填任务表。");
      } else {
        model.addTodo(text, workspace);
        persist();
        render();
        toast("原话已记下 · 已模拟 QQ / Lark 提醒。");
      }
    }
    if (e.target.id === "new-chat-form") {
      e.preventDefault();
      const text = $("#new-input").value;
      if (!text.trim()) {
        toast("从一句话开始。");
        return;
      }
      const id = model.newTask(text, $("#new-workspace").value || "crew");
      s.newDraft = "";
      go(`task/${id}/leader`);
      toast("已从对话形成样例任务，详情可随时修改。");
    }
    if (e.target.id === "details-form") {
      e.preventDefault();
      const c = context(),
        f = new FormData(e.target),
        t = model.task(c.id);
      t.title = String(f.get("title")).trim() || t.title;
      t.goal = String(f.get("goal"));
      t.criteria = String(f.get("criteria"));
      t.workspace = String(f.get("workspace")) || t.workspace;
      t.related = f.getAll("related").filter((x) => x !== t.workspace);
      saveView();
      render();
      toast("样例任务详情已更新，成员与成果仍归属同一任务。");
    }
    if (e.target.id === "channel-form") {
      e.preventDefault();
      const input = e.target.querySelector("textarea");
      if (!input.value.trim()) return;
      const node = document.createElement("div");
      node.className = "channel-message";
      node.textContent = "你 · 样例消息：" + input.value;
      e.target.before(node);
      input.value = "";
      toast("已发送到本地样例频道。");
    }
  });
  document.addEventListener("input", (e) => {
    if (e.target.id === "capture-input") {
      s.captureDraft = e.target.value;
      persist();
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#dialog").open) {
      const c = context();
      if (c && s.panels[model.keyFor(c.id, c.person)]) panel("none");
      else closeNavigation();
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Tab" || $("#dialog").open) return;
    const scope = matchMedia("(max-width:700px)").matches
      ? !$("#inspector").hidden
        ? $("#inspector")
        : $("#app").classList.contains("nav-open")
          ? $("#sidebar")
          : null
      : null;
    if (!scope) return;
    const items = Array.from(
        scope.querySelectorAll(
          "button:not(:disabled),a[href],input,textarea,select",
        ),
      ).filter((x) => !x.hidden),
      first = items[0],
      last = items.at(-1);
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last?.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first?.focus();
    }
  });
  matchMedia("(max-width:700px)").addEventListener("change", syncAccessibility);
  $("#nav-backdrop").addEventListener("click", closeNavigation);
  window.addEventListener("hashchange", render);
  window.addEventListener("pagehide", saveView);
  window.addEventListener("storage", (event) => {
    if (event.key === "crew-design-settings-v1") {
      PrototypeSettings.applyAppearance();
      return;
    }
    if (event.key !== STORAGE || !event.newValue) return;
    try {
      const incoming = JSON.parse(event.newValue);
      if (incoming.schema !== 1) return;
      Object.assign(s, incoming);
      CrewSample.workspaces = s.workspaces;
      render();
    } catch (_) {
      toast("其他预览页的样例状态无法读取，本页内容保留。");
    }
  });
  PrototypeSettings.applyAppearance();
  render();
})();
