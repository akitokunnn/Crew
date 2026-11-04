(function (root) {
  "use strict";

  // 本地可改的副本：开始推进只改本页状态。
  const sample = structuredClone(root.CrewOrgSample);
  const model = root.CrewOrgModel;
  const { icon } = root.CrewLightIcons;

  const TEXT = {
    running: "推进中", waiting_user: "等你答复", paused: "已暂停", pausing: "暂停中", completed: "已完成", awaiting_acceptance: "待验收",
    working: "执行中", waiting: "等你答复", queued: "未开始", starting: "启动中", idle: "待继续", offline: "离线",
    cancelled: "已取消", failed: "未达标", planned: "未启动", unknown: "状态未知"
  };
  const SHAPE = {
    running: "st-run", working: "st-run", queued: "st-plan", starting: "st-queue", waiting_user: "st-wait", waiting: "st-wait",
    paused: "st-pause", pausing: "st-pause", idle: "st-idle", planned: "st-plan", completed: "st-done", awaiting_acceptance: "st-done",
    failed: "st-fail", cancelled: "st-fail", offline: "st-off"
  };
  const TONE = { running: "go", working: "go", starting: "go", waiting_user: "warn", waiting: "warn", awaiting_acceptance: "warn", failed: "bad" };
  const PENDING = new Set(["waiting_user", "awaiting_acceptance"]);
  const MOVING = new Set(["running", "pausing"]);
  const ENDED = new Set(["completed", "failed", "cancelled"]);
  const FILTERS = [
    ["all", "全部", () => true], ["pending", "待你", task => PENDING.has(task.status)], ["running", "推进中", task => MOVING.has(task.status)],
    ["paused", "已暂停", task => task.status === "paused"], ["queued", "未开始", task => task.status === "queued"], ["ended", "已结束", task => ENDED.has(task.status)]
  ];

  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const runtimeName = runtime => runtime === "claude" ? "CC" : runtime === "codex" ? "Codex" : String(runtime || "");
  const avatar = (person, size = 20) => `<span class="av av-${esc(person.tint || "stone")}" style="--av:${size}px" aria-hidden="true">${esc(person.name.slice(0, 1))}</span>`;
  const stateIcon = status => `<span class="st st-${TONE[status] || "mute"}" title="${esc(TEXT[status] || TEXT.unknown)}">${icon(SHAPE[status] || "st-idle")}</span>`;
  const stateTag = status => `<span class="st st-${TONE[status] || "mute"}">${icon(SHAPE[status] || "st-idle")}<span>${esc(TEXT[status] || TEXT.unknown)}</span></span>`;
  const ago = minutes => minutes < 60 ? `${minutes} 分钟` : minutes < 1440 ? `${Math.round(minutes / 60)} 小时` : `${Math.round(minutes / 1440)} 天`;

  function mount(element) {
    const id = element.id || "cl";
    const narrow = matchMedia("(max-width: 760px)");
    const compact = matchMedia("(max-width: 1180px)");
    const leaders = sample.people.filter(person => person.role === "leader");
    const state = {
      view: "workbench",
      leaderId: "lin", personId: "lin", taskId: "org",
      last: new Map(),
      back: null,
      list: { query: "", filter: "all" },
      panelOpen: true,
      openRow: null, selected: null, mapFocus: null, roster: new Set(),
      drafts: new Map(), scrolls: new Map(), messages: new Map(),
      visited: new Set(), recent: []
    };
    const key = (personId, taskId) => model.conversationKey(personId, taskId ?? null);
    const hasDraft = (personId, taskId) => Boolean((state.drafts.get(key(personId, taskId)) || "").trim());
    const leaderOf = personId => { const person = sample.people.find(item => item.session_id === personId); return person?.role === "leader" ? person.session_id : person?.leader_session_id; };

    function group(leaderId = state.leaderId) {
      const data = model.project(sample, leaderId);
      const tasks = data.tasks.filter(task => !task.history);
      const taskIds = new Set(tasks.map(task => task.id));
      // 后端建任务时给 leader 自动写入一条分工；界面上 leader 是任务的主人，组员 = 分工 − leader。
      const links = data.links.filter(link => !link.history && taskIds.has(link.taskId) && link.personId !== data.leader.id);
      const person = personId => data.people.find(item => item.id === personId);
      const task = taskId => tasks.find(item => item.id === taskId);
      const linksOf = personId => links.filter(link => link.personId === personId);
      const linksIn = taskId => links.filter(link => link.taskId === taskId);
      const link = (personId, taskId) => links.find(item => item.personId === personId && item.taskId === taskId);
      const members = data.people.filter(item => item.id !== data.leader.id);
      const standby = members.filter(item => !linksOf(item.id).length);
      const asks = (sample.generalAsks || []).filter(item => item.leader_session_id === leaderId);
      const pending = tasks.filter(item => PENDING.has(item.status));
      return { ...data, allTasks: data.tasks, tasks, links, person, task, linksOf, linksIn, link, members, standby, asks, pending };
    }

    element.classList.add("cl");
    element.innerHTML = `
      <div class="cl-navwrap">
        <nav class="cl-rail" aria-label="工作台与 leader"></nav>
        <nav class="cl-side" aria-label="当前 leader 的对话"></nav>
      </div>
      <main class="cl-main">
        <section class="cl-chat" aria-label="对话">
          <header class="cl-head"></header>
          <div class="cl-log" role="log" tabindex="0" aria-label="对话记录"><div class="cl-log-inner"></div></div>
          <form class="cl-compose">
            <div class="cl-start" hidden></div>
            <div class="cl-box">
              <textarea id="${id}-draft" rows="1" aria-label="消息草稿"></textarea>
              <div class="cl-box-foot">
                <span class="cl-hint" role="status"></span>
                <button type="submit" class="cl-send" aria-label="发送（样例，仅本页保存）">${icon("send")}</button>
              </div>
            </div>
          </form>
        </section>
        <section class="cl-page" hidden></section>
      </main>
      <aside class="cl-panel" aria-label="人员与分工">
        <header class="cl-panel-head"></header>
        <div class="cl-tree-scroll"></div>
      </aside>
      <dialog class="cl-sheet cl-sheet-left" aria-label="导航"></dialog>
      <dialog class="cl-sheet cl-sheet-right" aria-label="人员与分工"></dialog>
      <dialog class="cl-palette" aria-label="快速切换">
        <div class="pl-input">${icon("search")}<input type="search" placeholder="找 leader、任务或总会话" aria-label="搜索 leader、任务或总会话" autocomplete="off"><kbd>Esc</kbd></div>
        <div class="pl-results" role="listbox" aria-label="搜索结果"></div>
      </dialog>
      <dialog class="cl-docs" aria-labelledby="${id}-docs-title"></dialog>
      <dialog class="cl-menu" aria-label="项目菜单"></dialog>
      <dialog class="cl-map" aria-labelledby="${id}-map-title">
        <header class="cl-map-head">
          <h2 id="${id}-map-title">完整关系图谱</h2>
          <span class="cl-map-legend">
            <span><i class="lg lg-solid"></i>有执行记录</span><span><i class="lg lg-dash"></i>已分配未启动</span><span><i class="lg lg-on"></i>当前对话</span>
          </span>
          <button type="button" class="cl-icon-btn" data-act="close-map" aria-label="关闭图谱" title="关闭（Esc）">${icon("close")}</button>
        </header>
        <div class="cl-map-scroll"><div class="cl-map-canvas"></div></div>
        <div class="cl-map-detail" aria-live="polite"></div>
      </dialog>`;

    const $ = selector => element.querySelector(selector);
    const navwrap = $(".cl-navwrap");
    const rail = $(".cl-rail");
    const side = $(".cl-side");
    const panel = $(".cl-panel");
    const chatView = $(".cl-chat");
    const page = $(".cl-page");
    const log = $(".cl-log");
    const draft = $("textarea");
    const tree = $(".cl-tree-scroll");
    const leftSheet = $(".cl-sheet-left");
    const rightSheet = $(".cl-sheet-right");
    const palette = $(".cl-palette");
    const mapDialog = $(".cl-map");
    let paletteReturn = null;

    /* ---------- 左：leader 栏 ---------- */
    function renderRail() {
      const pendingAll = leaders.reduce((sum, leader) => { const data = group(leader.session_id); return sum + data.pending.length + data.asks.length; }, 0);
      rail.innerHTML = `
        <button type="button" class="rl-project" data-act="menu" aria-label="Crew 项目菜单" title="Crew 项目"><img src="crew.svg" alt=""></button>
        <button type="button" class="rl-item${state.view === "workbench" ? " is-on" : ""}" data-act="workbench" aria-label="工作台，${pendingAll} 项待你" title="工作台">
          ${icon("home")}${pendingAll ? `<span class="rl-badge">${pendingAll}</span>` : ""}</button>
        <button type="button" class="rl-item" data-act="palette" aria-label="快速切换（Ctrl K）" title="快速切换  Ctrl K">${icon("search")}</button>
        <span class="rl-sep" aria-hidden="true"></span>
        <div class="rl-leaders">
          ${leaders.map(leader => {
            const data = group(leader.session_id);
            const pending = data.pending.length + data.asks.length;
            const moving = data.tasks.some(task => MOVING.has(task.status));
            const on = state.view === "chat" && state.leaderId === leader.session_id;
            return `<button type="button" class="rl-item rl-leader${on ? " is-on" : ""}${state.leaderId === leader.session_id ? " is-scope" : ""}" data-act="leader" data-leader="${esc(leader.session_id)}"
                aria-label="${esc(leader.name)}${pending ? `，${pending} 项待你` : ""}" title="${esc(leader.name)}">
              <span class="rl-av av-${esc(leader.tint)}">${esc(leader.name.slice(0, 1))}</span>
              ${pending ? `<span class="rl-badge">${pending}</span>` : moving ? '<span class="rl-live" aria-hidden="true"></span>' : ""}
            </button>`;
          }).join("")}
        </div>
        <span class="rl-fill"></span>
        <button type="button" class="rl-item${state.view === "assistant" ? " is-on" : ""}" data-act="assistant" aria-label="全局助手" title="全局助手">${icon("spark")}</button>
        <button type="button" class="rl-item" data-act="settings" aria-label="设置" title="设置（含音效）">${icon("gear")}</button>`;
    }

    /* ---------- 左：当前 leader 的对话 ---------- */
    function renderSide() {
      const data = group();
      const leader = data.leader;
      const inChat = state.view === "chat";
      const isCurrent = (personId, taskId) => inChat && state.personId === personId && state.taskId === taskId;
      const visited = [...state.visited].map(item => JSON.parse(item)).filter(([personId, taskId]) => personId !== leader.id && data.person(personId) && data.task(taskId));
      const item = (personId, taskId, content, extra = "") => `
        <button type="button" class="nv-item${extra}${isCurrent(personId, taskId) ? " is-current" : ""}" data-act="go" data-person="${esc(personId)}" data-task="${esc(taskId ?? "")}"${isCurrent(personId, taskId) ? ' aria-current="page"' : ""}>
          ${content}${hasDraft(personId, taskId) ? `<span class="nv-draft" title="有未发送草稿">${icon("pencil")}</span>` : ""}
        </button>`;
      const subs = taskId => visited.filter(([, t]) => t === taskId).map(([personId]) => {
        const person = data.person(personId);
        const link = data.link(personId, taskId);
        return item(personId, taskId, `${avatar(person, 16)}<span class="nv-label">${esc(person.name)}</span>${link ? stateIcon(link.state) : ""}`, " nv-sub");
      }).join("");
      const taskItem = task => `<div class="nv-group${inChat && state.taskId === task.id ? " has-current" : ""}">
          ${item(leader.id, task.id, `${stateIcon(task.status)}<span class="nv-label" title="${esc(task.title)}">${esc(task.title)}</span>`)}${subs(task.id)}</div>`;
      // 侧栏规则固定：待你的全部列出；在做 = 推进中，加上这次打开过的任务。其余进完整清单。
      const pending = data.pending.slice().sort((a, b) => b.updated - a.updated);
      const opened = new Set([...state.visited].map(item => JSON.parse(item)[1]).concat(inChat ? [state.taskId] : []));
      const moving = data.tasks.filter(task => !PENDING.has(task.status) && (MOVING.has(task.status) || opened.has(task.id))).sort((a, b) => a.updated - b.updated);
      const shown = moving.slice(0, 8);
      const hidden = moving.length - shown.length;
      side.innerHTML = `
        <header class="sb-head">
          <h2 class="sb-name" title="${esc(leader.name)}">${esc(leader.name)}</h2>
          <p class="sb-duty">${esc(leader.summary)} · ${esc(runtimeName(leader.runtime))}</p>
        </header>
        ${item(leader.id, null, `<span class="nv-ic">${icon("person")}</span><span class="nv-label">总会话</span>${data.asks.length ? stateIcon("waiting_user") : ""}`)}
        ${pending.length ? `<h3 class="nv-heading nv-warn">待你<span>${pending.length}</span></h3>${pending.map(taskItem).join("")}` : ""}
        ${shown.length ? `<h3 class="nv-heading">在做<span>${moving.length}</span></h3>${shown.map(taskItem).join("")}` : ""}
        ${hidden ? `<button type="button" class="nv-more" data-act="list" data-filter="running">另 ${hidden} 项推进中</button>` : ""}
        ${data.allTasks.length ? `<button type="button" class="nv-all${state.view === "list" ? " is-current" : ""}" data-act="list" data-filter="all">${icon("list")}<span>全部任务</span><span class="nv-count">${data.allTasks.length}</span></button>`
          : '<p class="nv-empty">还没有任务。在总会话里交代需求，leader 会建任务。</p>'}`;
    }

    /* ---------- 中：对话 ---------- */
    function contextState(data, person, task) {
      if (!task) return null;
      if (person.id === data.leader.id) return task.status;
      return data.link(person.id, task.id)?.state || "planned";
    }

    function roll(rows) {
      return `<ul class="msg-roll">${rows.map(row => `<li>${avatar(row.person, 18)}<b>${esc(row.person.name)}</b><span class="msg-roll-work">${esc(row.text)}</span>${stateTag(row.state)}</li>`).join("")}</ul>`;
    }

    function transcriptFor(data, person, task) {
      const you = text => `<article class="msg msg-you"><p>${esc(text)}</p></article>`;
      const them = (text, extra = "") => `<article class="msg"><header>${avatar(person, 20)}<b>${esc(person.name)}</b><span>样例记录</span></header><p>${esc(text)}</p>${extra}</article>`;
      const note = text => `<p class="msg-note">${esc(text)}</p>`;
      const out = [];
      if (task) {
        const members = data.linksIn(task.id);
        if (task.current_round > 1) out.push(note(`第 ${task.current_round - 1} 轮记录已归档 · 当前第 ${task.current_round} 轮`));
        out.push(you(task.status === "queued" ? `${task.title}：${task.summary}` : task.summary));
        if (person.id === data.leader.id) {
          if (task.status === "queued") out.push(them("要求已记下。你确认开始后我再派工推进。"));
          else out.push(them(members.length ? `${task.code} 的分工如下，每人的执行记录独立保存：` : "我直接推进这个任务，暂时没有派给组员。",
            members.length ? roll(members.map(link => ({ person: data.person(link.personId), text: link.work, state: link.state }))) : ""));
          if (task.ask) out.push(them(task.ask));
        } else {
          const link = data.link(person.id, task.id);
          out.push(them(link?.executionId ? `我在这个任务里负责：${link.work}。` : `已分配「${link?.work || "待补充"}」，还没有执行记录。`,
            link ? `<p class="msg-exec">${stateTag(link.state)}<span>${esc(runtimeName(link.runtime))} · 记录归属 ${esc(task.code)}</span></p>` : ""));
        }
        const shared = members.filter(link => data.linksOf(link.personId).length > 1 && (person.id === data.leader.id || link.personId === person.id)).slice(0, 3);
        if (shared.length) {
          out.push(you("同时在别的任务里的人，会互相影响吗？"));
          out.push(them(`不会混在一起。${shared.map(link => {
            const rest = data.linksOf(link.personId).filter(item => item.taskId !== task.id);
            const other = rest.slice(0, 2).map(item => `${data.task(item.taskId).code}（${TEXT[item.state]}）`).join("、") + (rest.length > 2 ? ` 等 ${rest.length} 项` : "");
            return `${link.personId === person.id ? "我" : data.person(link.personId).name}另在 ${other}`;
          }).join("；")}，各自的对话和记录分开保存。`));
        }
      } else {
        out.push(you("这组的几个任务都在推进，我想看看成员分别负责什么。"));
        out.push(them(data.tasks.length ? `本组 ${data.tasks.length} 项任务，${data.pending.length} 项等你。待你的几项：` : "本组还没有任务。在这里交代需求，我会建任务。",
          data.pending.length ? roll(data.pending.slice(0, 5).map(task => ({ person: { name: task.code, tint: "stone" }, text: task.title, state: task.status }))) : ""));
        data.asks.forEach(item => out.push(them(item.ask)));
      }
      (state.messages.get(key(person.id, task?.id)) || []).forEach(text => out.push(you(text)));
      return out.join("");
    }

    function renderChat() {
      const data = group();
      const person = data.person(state.personId) || data.leader;
      const task = data.task(state.taskId);
      const leader = data.leader;
      const status = contextState(data, person, task);
      const name = task ? task.title : "总会话";
      const paused = task && (task.status === "paused" || task.status === "pausing");
      const isLeader = person.id === leader.id;
      // 标题即位置：leader 名可回总会话，组员对话里的任务名可回任务主线。
      const personPart = isLeader && task
        ? `<button type="button" class="hd-crumb hd-person" data-act="go" data-person="${esc(leader.id)}" data-task="" title="回到 ${esc(leader.name)} 的总会话">${esc(person.name)}</button>`
        : `<span class="hd-person" title="${esc(person.name)}">${esc(person.name)}</span>`;
      const contextPart = !isLeader && task
        ? `<button type="button" class="hd-crumb hd-context" data-act="go" data-person="${esc(leader.id)}" data-task="${esc(task.id)}" title="回到 ${esc(leader.name)} 的任务主线">${esc(name)}</button>`
        : `<span class="hd-context" title="${esc(task ? `${task.code} · ${task.title}` : name)}">${esc(name)}</span>`;
      $(".cl-head").innerHTML = `
        <button type="button" class="cl-icon-btn cl-nav-btn" data-act="open-nav" aria-label="打开导航">${icon("menu")}</button>
        ${avatar(person, 24)}
        <h1>${personPart}<span class="hd-sep" aria-hidden="true">/</span>${contextPart}</h1>
        ${status ? stateTag(status) : ""}
        <span class="hd-fill"></span>
        <span class="hd-runtime" title="执行载体">${esc(runtimeName(task ? data.link(person.id, task.id)?.runtime || person.runtime : person.runtime))}</span>
        ${task ? `<button type="button" class="cl-icon-btn" data-act="docs" aria-label="交付与文档" title="交付与文档">${icon("file")}</button>` : ""}
        <button type="button" class="cl-icon-btn cl-panel-btn" data-act="toggle-panel" aria-pressed="${panelShown()}" aria-label="人员与分工" title="人员与分工">${icon("panel")}</button>`;
      $(".cl-log-inner").innerHTML = transcriptFor(data, person, task);
      const k = key(person.id, task?.id);
      log.scrollTop = state.scrolls.has(k) ? state.scrolls.get(k) : log.scrollHeight;
      draft.value = state.drafts.get(k) || "";
      draft.disabled = Boolean(paused);
      draft.placeholder = paused ? "任务已暂停，只能查看记录" : `发给 ${person.name} · ${name}`;
      chatView.classList.toggle("is-paused", Boolean(paused));
      const start = $(".cl-start");
      const queued = task && task.status === "queued" && isLeader;
      start.hidden = !queued;
      start.innerHTML = queued ? `${icon("st-plan")}<span>还没开始。打开、切走都不会启动；发消息或点「开始推进」后才开始。</span>
        <button type="button" data-act="start" data-task="${esc(task.id)}">${icon("play")}开始推进</button>` : "";
      sizeDraft();
      updateHint();
    }

    function updateHint() {
      const text = draft.value.trim();
      $(".cl-send").disabled = draft.disabled || !text;
      $(".cl-hint").innerHTML = draft.disabled ? `${icon("st-pause")}已暂停` : text ? `${icon("pencil")}草稿只保存在这个对话` : "";
    }

    function sizeDraft() {
      draft.style.height = "auto";
      draft.style.height = `${Math.min(draft.scrollHeight, 168)}px`;
    }

    /* ---------- 中：工作台、清单、助手 ---------- */
    function renderWorkbench() {
      const groups = leaders.map(leader => ({ leader, data: group(leader.session_id) }));
      const pendingTotal = groups.reduce((sum, item) => sum + item.data.pending.length + item.data.asks.length, 0);
      const taskTotal = groups.reduce((sum, item) => sum + item.data.allTasks.length, 0);
      const recent = state.recent.slice(0, 6).map(([personId, taskId]) => {
        const leaderId = leaderOf(personId);
        const data = group(leaderId);
        const person = data.person(personId);
        const task = data.task(taskId);
        if (!person || (taskId && !task)) return "";
        return `<button type="button" class="wb-row" data-act="go" data-person="${esc(personId)}" data-task="${esc(taskId ?? "")}">
          ${avatar(person, 20)}<span class="wb-copy"><span class="wb-title">${esc(person.name)} / ${esc(task ? task.title : "总会话")}</span>
          <span class="wb-sub">${esc(data.leader.name)}组</span></span>${hasDraft(personId, taskId) ? `<span class="wb-draft">${icon("pencil")}草稿</span>` : ""}</button>`;
      }).join("");
      page.innerHTML = `<div class="pg">
        <header class="pg-head">
          <button type="button" class="cl-icon-btn cl-nav-btn" data-act="open-nav" aria-label="打开导航">${icon("menu")}</button>
          <h1>工作台</h1><span class="pg-meta">Crew · ${leaders.length} 个 leader · ${taskTotal} 项任务</span>
        </header>
        <section class="wb-section">
          <h2>待你处理<span class="wb-count">${pendingTotal}</span></h2>
          ${groups.filter(item => item.data.pending.length || item.data.asks.length).map(({ leader, data }) => `
            <div class="wb-group">
              <h3>${avatar(leader, 18)}<span title="${esc(leader.name)}">${esc(leader.name)}</span></h3>
              ${data.asks.map(item => `<button type="button" class="wb-row" data-act="go" data-person="${esc(leader.session_id)}" data-task="">
                ${stateIcon("waiting_user")}<span class="wb-copy"><span class="wb-title">总会话里的拍板</span><span class="wb-sub">${esc(item.ask)}</span></span><span class="wb-ago">${ago(item.updated)}</span></button>`).join("")}
              ${data.pending.slice().sort((a, b) => b.updated - a.updated).map(task => `<button type="button" class="wb-row" data-act="go" data-person="${esc(leader.session_id)}" data-task="${esc(task.id)}">
                ${stateIcon(task.status)}<span class="wb-copy"><span class="wb-title">${esc(task.title)}</span><span class="wb-sub">${esc(task.ask || TEXT[task.status])}</span></span><span class="wb-ago">等了 ${ago(task.updated)}</span></button>`).join("")}
            </div>`).join("") || '<p class="wb-empty">没有等你处理的事。</p>'}
        </section>
        ${recent ? `<section class="wb-section"><h2>最近对话</h2>${recent}</section>` : ""}
        <section class="wb-section">
          <h2>各组</h2>
          ${groups.map(({ leader, data }) => {
            const count = filter => data.tasks.filter(filter).length;
            return `<div class="wb-team">
              <button type="button" class="wb-row wb-leader" data-act="leader" data-leader="${esc(leader.session_id)}">
                <span class="rl-av av-${esc(leader.tint)}">${esc(leader.name.slice(0, 1))}</span>
                <span class="wb-copy"><span class="wb-title wb-wrap">${esc(leader.name)}</span><span class="wb-sub">${esc(leader.summary)} · ${data.members.length} 位组员</span></span>
                <span class="wb-stats">
                  ${data.pending.length + data.asks.length ? `<span class="st st-warn">${icon("st-wait")}${data.pending.length + data.asks.length} 待你</span>` : ""}
                  <span class="st st-go">${icon("st-run")}${count(task => MOVING.has(task.status))} 推进中</span>
                  <span class="st st-mute">${icon("st-plan")}${count(task => task.status === "queued")} 未开始</span>
                </span>
              </button>
              ${data.allTasks.length ? `<button type="button" class="wb-link" data-act="list" data-leader="${esc(leader.session_id)}" data-filter="all">全部 ${data.allTasks.length} 项</button>` : ""}
            </div>`;
          }).join("")}
        </section>
      </div>`;
    }

    function renderList() {
      const data = group();
      const query = state.list.query.trim().toLowerCase();
      const rows = data.allTasks.filter(FILTERS.find(([value]) => value === state.list.filter)[2])
        .filter(task => !query || `${task.title} ${task.code} ${data.linksIn(task.id).map(link => data.person(link.personId).name).join(" ")}`.toLowerCase().includes(query))
        .sort((a, b) => (PENDING.has(b.status) - PENDING.has(a.status)) || (ENDED.has(a.status) - ENDED.has(b.status)) || a.updated - b.updated);
      const linkPeople = task => model.project(sample, state.leaderId).links.filter(link => link.taskId === task.id && link.personId !== data.leader.id && (!link.released));
      page.innerHTML = `<div class="pg pg-list">
        <header class="pg-head">
          <button type="button" class="cl-icon-btn cl-nav-btn" data-act="open-nav" aria-label="打开导航">${icon("menu")}</button>
          ${state.back ? `<button type="button" class="pg-back" data-act="back" title="返回（Esc）">${icon("back")}<span>返回</span></button>` : ""}
          <h1><span class="pg-crumb" title="${esc(data.leader.name)}">${esc(data.leader.name)}</span><span class="hd-sep">/</span>全部任务</h1>
        </header>
        <div class="ls-tools">
          <label class="ls-search">${icon("search")}<input type="search" value="${esc(state.list.query)}" placeholder="搜索任务名、编号或组员" aria-label="搜索任务"></label>
          <div class="ls-filters" role="group" aria-label="按状态">
            ${FILTERS.map(([value, label, test]) => `<button type="button" data-act="filter" data-filter="${value}" aria-pressed="${state.list.filter === value}">${label}<span>${data.allTasks.filter(test).length}</span></button>`).join("")}
          </div>
        </div>
        <div class="ls-rows">
          ${rows.map(task => {
            const people = linkPeople(task).map(link => data.person(link.personId)).filter(Boolean);
            return `<button type="button" class="ls-row${state.taskId === task.id ? " is-current" : ""}" data-act="go" data-person="${esc(data.leader.id)}" data-task="${esc(task.id)}">
              ${stateIcon(task.status)}
              <span class="wb-copy"><span class="wb-title wb-wrap">${esc(task.title)}</span><span class="wb-sub">${esc(task.code)} · ${esc(TEXT[task.status])}${task.ask && PENDING.has(task.status) ? ` · ${esc(task.ask)}` : ""}</span></span>
              <span class="ls-people">${people.slice(0, 3).map(person => avatar(person, 20)).join("")}${people.length > 3 ? `<span class="ls-more">+${people.length - 3}</span>` : ""}</span>
              <span class="wb-ago">${ago(task.updated)}</span>
              ${hasDraft(data.leader.id, task.id) ? `<span class="nv-draft" title="有未发送草稿">${icon("pencil")}</span>` : ""}
            </button>`;
          }).join("") || '<p class="wb-empty">没有符合条件的任务。</p>'}
        </div>
      </div>`;
    }

    function renderAssistant() {
      page.innerHTML = `<div class="pg">
        <header class="pg-head">
          <button type="button" class="cl-icon-btn cl-nav-btn" data-act="open-nav" aria-label="打开导航">${icon("menu")}</button>
          <button type="button" class="pg-back" data-act="back" title="返回项目（Esc）">${icon("back")}<span>返回项目</span></button>
          <h1>全局助手</h1>
        </header>
        <p class="wb-empty">全局助手是独立页面，不属于任何 leader 或任务。可以让它新开任务、向 leader 传达要求、查询进度。这里只演示入口和返回路径。</p>
      </div>`;
    }

    /* ---------- 右：当前对话涉及的人 ---------- */
    function panelShown() { return state.view === "chat" && (compact.matches ? rightSheet.open : state.panelOpen); }

    function detail(data, person, contextTaskId) {
      const mine = data.linksOf(person.id);
      const isCurrent = (personId, taskId) => state.personId === personId && state.taskId === taskId;
      const destination = (taskId, title, sub, status, personId = person.id) => {
        const current = isCurrent(personId, taskId);
        return `<button type="button" class="dt-go${current ? " is-current" : ""}${taskId === contextTaskId ? " is-here" : ""}" data-act="go" data-person="${esc(personId)}" data-task="${esc(taskId ?? "")}"${current ? ' aria-current="true"' : ""}>
          ${status ? stateIcon(status) : `<span class="dt-ic">${icon("person")}</span>`}
          <span class="dt-copy"><span class="dt-title">${esc(title)}</span>${sub ? `<span class="dt-sub">${esc(sub)}</span>` : ""}</span>
          <span class="dt-end">${current ? "正在聊" : icon("chat")}</span>
        </button>`;
      };
      const wrap = (intro, items) => `<div class="dt" role="group" aria-label="${esc(person.name)}的对话入口"><p class="dt-intro">${esc(intro)}</p>${items}</div>`;
      if (person.id === data.leader.id) {
        return wrap(`负责 ${data.tasks.length} 项任务`, destination(null, "总会话", "跨任务安排与汇总", null)
          + data.tasks.slice(0, 8).map(task => destination(task.id, `${task.code} ${task.title}`, "任务主线", task.status)).join(""));
      }
      if (!mine.length) return wrap("还没有分配任务。leader 派活后，这里会出现任务对话。", "");
      return wrap(`参与 ${mine.length} 项任务`, mine.map(link => {
        const task = data.task(link.taskId);
        return destination(task.id, `${task.code} ${task.title}`, `${link.work} · ${runtimeName(link.runtime)}`, link.state);
      }).join(""));
    }

    function pathClasses(items, isLit) {
      const last = items.reduce((at, item, index) => isLit(item) ? index : at, -1);
      return items.map((item, index) => `${isLit(item) ? " hl" : ""}${index < last ? " pass" : ""}`);
    }

    function renderPanel() {
      const data = group();
      const scrollTop = tree.scrollTop;
      const leader = data.leader;
      const task = data.task(state.taskId);
      const focusId = state.selected;
      const dim = Boolean(focusId);
      const rowOpen = (personId, taskId) => state.openRow === key(personId, taskId);
      const head = (title, meta) => `
        <h2 title="${esc(title)}">${esc(title)}</h2><span class="ph-meta">${esc(meta)}</span>
        <button type="button" class="cl-icon-btn" data-act="open-map" aria-label="展开完整关系图谱" title="完整图谱">${icon("graph")}</button>
        <button type="button" class="cl-icon-btn" data-act="close-panel" aria-label="收起人员与分工" title="收起">${icon("close")}</button>`;

      const memberRow = (link, cls) => {
        const person = data.person(link.personId);
        const count = data.linksOf(person.id).length;
        const current = state.personId === person.id && state.taskId === link.taskId;
        const open = rowOpen(person.id, link.taskId);
        const others = data.linksOf(person.id).filter(item => item.taskId !== link.taskId).map(item => data.task(item.taskId).title).join("、");
        return `<li class="tr-member${cls}${current ? " is-current" : ""}${dim && focusId !== person.id ? " is-dim" : ""}" data-person="${esc(person.id)}">
          <div class="tr-row">
            <button type="button" class="tr-main" data-act="select" data-person="${esc(person.id)}" data-task="${esc(link.taskId)}" aria-expanded="${open}">
              ${avatar(person, 20)}
              <span class="tr-copy">
                <span class="tr-line"><span class="tr-name">${esc(person.name)}</span>
                  ${count > 1 ? `<span class="tr-multi" title="也参与：${esc(others)}">${icon("layers")}${count}</span>` : ""}
                  <span class="tr-state">${stateTag(link.state)}</span></span>
                <span class="tr-sub">${esc(link.work)}</span>
              </span>
            </button>
            ${hasDraft(person.id, link.taskId) ? `<span class="tr-draft" title="有未发送草稿">${icon("pencil")}</span>` : ""}
            <button type="button" class="tr-act${current ? " is-on" : ""}" data-act="go" data-person="${esc(person.id)}" data-task="${esc(link.taskId)}"
              aria-label="${current ? "正在聊：" : "进入"}${esc(person.name)}的本任务对话" title="${current ? "正在聊" : "本任务对话"}">${icon("chat")}</button>
          </div>
          ${open ? detail(data, person, link.taskId) : ""}
        </li>`;
      };

      if (task) {
        // 任务里：任务主线（leader 负责）下面挂本任务的组员。
        const members = data.linksIn(task.id);
        const hl = link => focusId ? link.personId === focusId : state.personId === link.personId;
        const classes = pathClasses(members, hl);
        const leaderHere = state.personId === leader.id;
        $(".cl-panel-head").innerHTML = head("本任务分工", `${members.length} 位组员`);
        tree.innerHTML = `<div class="tr${dim ? " has-focus" : ""}"><ul class="tr-tasks"><li class="tr-task is-open${classes.some(item => item.includes("hl")) ? " hl-down" : ""}${leaderHere ? " is-current" : ""}">
          <div class="tr-row tr-task-row">
            <button type="button" class="tr-main" data-act="go" data-person="${esc(leader.id)}" data-task="${esc(task.id)}">
              ${stateIcon(task.status)}
              <span class="tr-copy"><span class="tr-line"><span class="tr-name" title="${esc(leader.name)}">${esc(leader.name)}</span><span class="tr-role">负责</span></span>
              <span class="tr-sub">${esc(TEXT[task.status])} · ${esc(task.code)}${leaderHere ? " · 正在聊" : " · 任务主线"}</span></span>
            </button>
          </div>
          ${members.length ? `<ul class="tr-members">${members.map((link, index) => memberRow(link, classes[index])).join("")}</ul>` : '<p class="tr-empty">还没有派给组员，leader 自己推进。</p>'}
        </li></ul></div>`;
      } else {
        // 总会话里：按人看，谁在哪些任务。
        const busy = data.members.filter(person => data.linksOf(person.id).length);
        $(".cl-panel-head").innerHTML = head(`${leader.name}组`, `${data.members.length} 人 · ${data.tasks.length} 任务`);
        tree.innerHTML = `<div class="tr tr-roster${dim ? " has-focus" : ""}">
          ${busy.length ? `<ul class="tr-tasks">${busy.map(person => {
            const mine = data.linksOf(person.id);
            const open = state.roster.has(person.id);
            const waiting = mine.filter(link => link.state === "waiting").length;
            return `<li class="tr-task${open ? " is-open" : ""}">
              <div class="tr-row tr-task-row">
                <button type="button" class="tr-main" data-act="roster" data-person="${esc(person.id)}" aria-expanded="${open}">${avatar(person, 20)}<span class="tr-copy"><span class="tr-line"><span class="tr-name">${esc(person.name)}</span>
                  ${mine.length > 1 ? `<span class="tr-multi">${icon("layers")}${mine.length}</span>` : ""}<span class="tr-role">${esc(person.summary)}</span>
                  ${waiting ? `<span class="tr-state">${stateTag("waiting")}</span>` : ""}</span>
                  <span class="tr-sub">${esc(mine.map(link => data.task(link.taskId).title).join("、"))}</span></span>
                  <span class="tr-chev">${icon("chevron")}</span></button>
              </div>
              ${open ? `<ul class="tr-members">${mine.map(link => {
                const item = data.task(link.taskId);
                return `<li class="tr-member"><div class="tr-row">
                  <button type="button" class="tr-main" data-act="go" data-person="${esc(person.id)}" data-task="${esc(item.id)}" title="进入${esc(person.name)}在这个任务的对话">
                    ${stateIcon(link.state)}<span class="tr-copy"><span class="tr-line"><span class="tr-title">${esc(item.title)}</span></span><span class="tr-sub">${esc(link.work)} · ${esc(TEXT[link.state])}</span></span>
                  </button></div></li>`;
              }).join("")}</ul>` : ""}
            </li>`;
          }).join("")}</ul>` : '<p class="tr-empty">组员还没有分配任务。</p>'}
          ${data.standby.length ? `<section class="tr-standby" aria-label="组内待命"><h3>待命<span>派活后可在任务里对话</span></h3>
            ${data.standby.map(person => `<div class="tr-standby-item">${avatar(person, 20)}<span class="tr-copy"><span class="tr-line"><span class="tr-name">${esc(person.name)}</span></span><span class="tr-sub">${esc(person.summary)}</span></span></div>`).join("")}
          </section>` : ""}
        </div>`;
      }
      tree.scrollTop = scrollTop;
    }

    /* ---------- 完整图谱 ---------- */
    function renderMap() {
      const data = group();
      const leader = data.leader;
      const members = [...new Set(data.links.map(link => link.personId))].map(data.person);
      const G = { pad: 28, lw: 176, tw: 248, mw: 208, gap: 104, th: 52, tg: 10, mh: 52, mg: 10, sh: 30 };
      const X = { l: G.pad, t: G.pad + G.lw + G.gap, m: G.pad + G.lw + G.gap + G.tw + G.gap };
      const colTask = data.tasks.length * G.th + Math.max(0, data.tasks.length - 1) * G.tg;
      const colMember = members.length * G.mh + Math.max(0, members.length - 1) * G.mg + (data.standby.length ? G.sh + data.standby.length * (G.mh + G.mg) : 0);
      const H = Math.max(colTask, colMember, 72) + G.pad * 2;
      const W = X.m + G.mw + G.pad;
      const ty = index => (H - colTask) / 2 + index * (G.th + G.tg);
      const my = index => (H - colMember) / 2 + index * (G.mh + G.mg);
      const curve = (x1, y1, x2, y2) => { const dx = (x2 - x1) * .5; return `M${x1} ${y1}C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`; };
      const edges = data.tasks.map((task, index) => {
        const current = state.personId === leader.id && state.taskId === task.id;
        return `<path class="mp-edge e-own${current ? " e-current" : ""}" data-task="${esc(task.id)}" data-person="${esc(leader.id)}" d="${curve(X.l + G.lw, H / 2, X.t, ty(index) + G.th / 2)}"/>`;
      }).concat(data.links.map(link => {
        const ti = data.tasks.findIndex(task => task.id === link.taskId);
        const mi = members.findIndex(person => person.id === link.personId);
        const current = state.personId === link.personId && state.taskId === link.taskId;
        return `<path class="mp-edge${link.executionId ? "" : " e-dash"}${current ? " e-current" : ""}" data-task="${esc(link.taskId)}" data-person="${esc(link.personId)}" d="${curve(X.t + G.tw, ty(ti) + G.th / 2, X.m, my(mi) + G.mh / 2)}"/>`;
      }));
      const nodes = [`<button type="button" class="mp-node mp-leader${state.personId === leader.id && !state.taskId ? " is-current" : ""}" data-act="map-focus" data-kind="person" data-id="${esc(leader.id)}"
          style="left:${X.l}px;top:${H / 2 - 36}px;width:${G.lw}px">
          ${avatar(leader, 28)}<span class="mp-copy"><span class="mp-title">${esc(leader.name)}</span><span class="mp-sub">leader · ${esc(runtimeName(leader.runtime))}</span></span>
        </button>`];
      data.tasks.forEach((task, index) => nodes.push(`<button type="button" class="mp-node mp-task${state.taskId === task.id ? " is-current" : ""}" data-act="map-focus" data-kind="task" data-id="${esc(task.id)}"
          style="left:${X.t}px;top:${ty(index)}px;width:${G.tw}px;height:${G.th}px">
          ${stateIcon(task.status)}<span class="mp-copy"><span class="mp-title">${esc(task.title)}</span><span class="mp-sub" data-default="${esc(`${task.code} · ${TEXT[task.status]} · ${data.linksIn(task.id).length} 人`)}"></span></span>
        </button>`));
      members.forEach((person, index) => {
        const count = data.linksOf(person.id).length;
        nodes.push(`<button type="button" class="mp-node mp-member${state.personId === person.id ? " is-current" : ""}" data-act="map-focus" data-kind="person" data-id="${esc(person.id)}"
          style="left:${X.m}px;top:${my(index)}px;width:${G.mw}px;height:${G.mh}px">
          ${avatar(person, 24)}<span class="mp-copy"><span class="mp-title">${esc(person.name)}${count > 1 ? `<span class="tr-multi">${icon("layers")}${count}</span>` : ""}</span>
          <span class="mp-sub" data-default="${esc(`${count} 项任务`)}"></span></span><span class="mp-state"></span>
        </button>`);
      });
      if (data.standby.length) {
        const top = my(members.length);
        nodes.push(`<span class="mp-standby-label" style="left:${X.m}px;top:${top + 4}px">待命</span>`);
        data.standby.forEach((person, index) => nodes.push(`<button type="button" class="mp-node mp-member mp-standby" data-act="map-focus" data-kind="person" data-id="${esc(person.id)}"
          style="left:${X.m}px;top:${top + G.sh + index * (G.mh + G.mg)}px;width:${G.mw}px;height:${G.mh}px">
          ${avatar(person, 24)}<span class="mp-copy"><span class="mp-title">${esc(person.name)}</span><span class="mp-sub" data-default="未分配任务"></span></span>
        </button>`));
      }
      const canvas = $(".cl-map-canvas");
      canvas.style.cssText = `width:${W}px;height:${H}px`;
      canvas.innerHTML = `<svg class="mp-edges" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">${edges.join("")}</svg>${nodes.join("")}`;
      applyMapFocus(state.mapFocus);
      renderMapDetail();
    }

    function applyMapFocus(focus) {
      const canvas = $(".cl-map-canvas");
      const data = group();
      const edges = [...canvas.querySelectorAll(".mp-edge")];
      const nodes = [...canvas.querySelectorAll(".mp-node")];
      canvas.classList.toggle("has-focus", Boolean(focus));
      nodes.forEach(node => {
        const sub = node.querySelector(".mp-sub[data-default]");
        if (sub) sub.textContent = sub.dataset.default;
        const slot = node.querySelector(".mp-state");
        if (slot) slot.innerHTML = "";
      });
      if (!focus) { edges.concat(nodes).forEach(item => item.classList.remove("is-on", "is-off")); return; }
      const lit = edges.filter(edge => focus.kind === "task" ? edge.dataset.task === focus.id : edge.dataset.person === focus.id || focus.id === data.leader.id);
      const litNodes = new Set([`${focus.kind}:${focus.id}`]);
      lit.forEach(edge => { litNodes.add(`task:${edge.dataset.task}`); litNodes.add(`person:${edge.dataset.person}`); });
      if (focus.kind === "person" && focus.id !== data.leader.id) {
        edges.filter(edge => edge.dataset.person === data.leader.id && litNodes.has(`task:${edge.dataset.task}`)).forEach(edge => lit.push(edge));
        litNodes.add(`person:${data.leader.id}`);
      }
      edges.forEach(edge => { const on = lit.includes(edge); edge.classList.toggle("is-on", on); edge.classList.toggle("is-off", !on); });
      nodes.forEach(node => {
        const on = litNodes.has(`${node.dataset.kind}:${node.dataset.id}`);
        node.classList.toggle("is-on", on); node.classList.toggle("is-off", !on);
        if (focus.kind === "task" && node.classList.contains("mp-member")) {
          const link = data.link(node.dataset.id, focus.id);
          if (link) { node.querySelector(".mp-sub").textContent = link.work; node.querySelector(".mp-state").innerHTML = stateIcon(link.state); }
        }
        if (focus.kind === "person" && node.classList.contains("mp-task")) {
          const link = data.link(focus.id, node.dataset.id);
          if (link) node.querySelector(".mp-sub").textContent = `${link.work} · ${TEXT[link.state]}`;
        }
      });
    }

    function renderMapDetail() {
      const target = $(".cl-map-detail");
      const data = group();
      const focus = state.mapFocus;
      if (!focus) { target.innerHTML = '<p class="mp-hint">点任务看分工，点组员看他在哪些任务里。再次点击取消。</p>'; return; }
      if (focus.kind === "task") {
        const task = data.task(focus.id);
        const current = state.personId === data.leader.id && state.taskId === task.id;
        target.innerHTML = `<div class="mp-detail-head">${stateIcon(task.status)}<b>${esc(task.code)} ${esc(task.title)}</b><span>${esc(task.summary)}</span></div>
          <div class="dt dt-row"><button type="button" class="dt-go${current ? " is-current" : ""}" data-act="go" data-person="${esc(data.leader.id)}" data-task="${esc(task.id)}">
            <span class="dt-ic">${icon("chat")}</span><span class="dt-copy"><span class="dt-title">任务主线（${esc(data.leader.name)}）</span></span><span class="dt-end">${current ? "正在聊" : icon("chevron")}</span></button></div>`;
        return;
      }
      const person = data.person(focus.id);
      target.innerHTML = `<div class="mp-detail-head">${avatar(person, 20)}<b>${esc(person.name)}</b><span>${esc(person.summary)}</span></div>${detail(data, person, state.taskId).replace('class="dt"', 'class="dt dt-row"')}`;
    }

    /* ---------- 快速切换 ---------- */
    function paletteItems(query) {
      const q = query.trim().toLowerCase();
      const items = [];
      leaders.forEach(leader => {
        const data = group(leader.session_id);
        items.push({ kind: "总会话", label: `${leader.name} / 总会话`, sub: leader.summary, person: leader.session_id, task: "", av: leader });
        data.tasks.forEach(task => items.push({ kind: TEXT[task.status], label: task.title, sub: `${task.code} · ${leader.name}`, person: leader.session_id, task: task.id, status: task.status }));
      });
      const hits = q ? items.filter(item => `${item.label} ${item.sub}`.toLowerCase().includes(q)) : items.filter(item => !item.task || PENDING.has(item.status));
      return hits.slice(0, 40);
    }

    function renderPalette() {
      const input = palette.querySelector("input");
      const hits = paletteItems(input.value);
      palette.querySelector(".pl-results").innerHTML = hits.map((item, index) => `
        <button type="button" class="pl-item" role="option" aria-selected="${index === 0}" data-act="go" data-person="${esc(item.person)}" data-task="${esc(item.task)}">
          ${item.av ? avatar(item.av, 20) : stateIcon(item.status)}<span class="wb-copy"><span class="wb-title">${esc(item.label)}</span><span class="wb-sub">${esc(item.sub)}</span></span><span class="pl-kind">${esc(item.kind)}</span>
        </button>`).join("") || '<p class="wb-empty">没有匹配的 leader 或任务。</p>';
    }

    function openPalette() {
      paletteReturn = document.activeElement;
      closeSheets();
      const input = palette.querySelector("input");
      input.value = "";
      palette.showModal();
      renderPalette();
      input.focus();
    }

    /* ---------- 状态切换 ---------- */
    function remember() {
      if (state.view !== "chat") return;
      const current = key(state.personId, state.taskId);
      state.drafts.set(current, draft.value);
      state.scrolls.set(current, log.scrollTop);
    }

    function go(personId, taskId) {
      const leaderId = leaderOf(personId);
      if (state.view === "chat" && personId === state.personId && taskId === state.taskId) { closeDialogs(); return; }
      remember();
      state.leaderId = leaderId;
      state.personId = personId;
      state.taskId = taskId;
      state.view = "chat";
      state.back = null;
      state.openRow = null; state.selected = null;
      state.last.set(leaderId, [personId, taskId]);
      if (personId !== leaderId) state.visited.add(key(personId, taskId));
      state.recent = [[personId, taskId], ...state.recent.filter(([p, t]) => !(p === personId && t === taskId))].slice(0, 8);
      closeDialogs();
      renderAll();
    }

    function openLeader(leaderId) {
      const [personId, taskId] = state.last.get(leaderId) || [leaderId, null];
      go(personId, taskId);
    }

    function showPage(view, extra = {}) {
      remember();
      if (state.view === "chat") state.back = [state.personId, state.taskId];
      Object.assign(state, extra, { view });
      closeDialogs();
      renderAll();
      page.focus?.();
    }

    function back() {
      if (state.back) { const [personId, taskId] = state.back; state.back = null; go(personId, taskId); }
      else showPage("workbench");
    }

    function renderAll() {
      renderRail();
      renderSide();
      chatView.hidden = state.view !== "chat";
      page.hidden = state.view === "chat";
      if (state.view === "chat") renderChat();
      if (state.view === "workbench") renderWorkbench();
      if (state.view === "list") renderList();
      if (state.view === "assistant") renderAssistant();
      if (state.view === "chat") renderPanel();
      syncLayout();
    }

    function syncLayout() {
      if (narrow.matches) { if (navwrap.parentNode !== leftSheet) leftSheet.append(navwrap); }
      else { if (leftSheet.open) leftSheet.close(); if (navwrap.parentNode !== element) element.prepend(navwrap); }
      if (compact.matches) { if (panel.parentNode !== rightSheet) rightSheet.append(panel); panel.hidden = false; }
      else {
        if (rightSheet.open) rightSheet.close();
        if (panel.parentNode !== element) element.insertBefore(panel, $(".cl-sheet-left"));
        panel.hidden = !(state.view === "chat" && state.panelOpen);
      }
      if (state.view !== "chat" && rightSheet.open) rightSheet.close();
      element.classList.toggle("has-panel", !compact.matches && state.view === "chat" && state.panelOpen);
      $(".cl-panel-btn")?.setAttribute("aria-pressed", String(panelShown()));
    }

    function closeSheets() { if (leftSheet.open) leftSheet.close(); if (rightSheet.open) rightSheet.close(); }
    function closeDialogs() { closeSheets(); [palette, mapDialog, $(".cl-docs"), $(".cl-menu")].forEach(dialog => { if (dialog.open) dialog.close(); }); }

    function openDocs() {
      const data = group();
      const task = data.task(state.taskId);
      const docs = $(".cl-docs");
      docs.innerHTML = `<header class="dc-head"><h2 id="${id}-docs-title">交付与文档</h2><button type="button" class="cl-icon-btn" data-act="close-docs" aria-label="关闭">${icon("close")}</button></header>
        <p class="dc-sub">${esc(task.code)} ${esc(task.title)}</p>
        <ul class="dc-list"><li>${icon("file")}<span>原始需求与约定</span><em>随任务更新</em></li><li>${icon("file")}<span>第 ${task.current_round} 轮交付记录</span><em>${esc(TEXT[task.status])}</em></li><li>${icon("file")}<span>拍板记录</span><em>${task.ask ? "1 项待你" : "无待办"}</em></li></ul>
        <p class="dc-note">资料只从任务内进入，侧栏不另设资料中心。</p>`;
      docs.showModal();
    }

    function openMenu() {
      const menu = $(".cl-menu");
      menu.innerHTML = `<p class="mn-title">Crew</p>${["交流区", "Git", "我的待办", "会话库", "用量"].map(label => `<button type="button" class="mn-item" data-act="close-menu">${esc(label)}<em>低频入口</em></button>`).join("")}`;
      menu.showModal();
    }

    element.addEventListener("click", event => {
      const button = event.target.closest("[data-act]");
      if (!button || !element.contains(button) || button.tagName === "SELECT") return;
      const { act, person, task } = button.dataset;
      if (act === "go") go(person, task || null);
      if (act === "leader") openLeader(button.dataset.leader);
      if (act === "workbench") showPage("workbench", { back: null });
      if (act === "assistant") showPage("assistant");
      if (act === "settings") { /* 设置沿用现有页面（含三项音效），本原型不展开。 */ }
      if (act === "palette") openPalette();
      if (act === "menu") openMenu();
      if (act === "close-menu") $(".cl-menu").close();
      if (act === "list") {
        const leaderId = button.dataset.leader || state.leaderId;
        showPage("list", { leaderId, list: { query: leaderId === state.leaderId ? state.list.query : "", filter: button.dataset.filter || "all" } });
      }
      if (act === "filter") { state.list.filter = button.dataset.filter; renderList(); page.querySelector(`[data-filter="${button.dataset.filter}"]`)?.focus(); }
      if (act === "back") back();
      if (act === "start") {
        const goal = sample.goals.find(item => item.id === button.dataset.task);
        goal.status = "running";
        goal.updated = 0;
        renderAll();
        draft.focus();
      }
      if (act === "docs") openDocs();
      if (act === "close-docs") $(".cl-docs").close();
      if (act === "select") {
        const rowKey = key(person, task || null);
        if (state.openRow === rowKey) { state.openRow = null; state.selected = null; }
        else { state.openRow = rowKey; state.selected = person; }
        renderPanel();
        tree.querySelector(`[data-act="select"][data-person="${CSS.escape(person)}"][data-task="${CSS.escape(task || "")}"]`)?.focus();
      }
      if (act === "roster") {
        state.roster.has(person) ? state.roster.delete(person) : state.roster.add(person);
        renderPanel();
        tree.querySelector(`[data-act="roster"][data-person="${CSS.escape(person)}"]`)?.focus();
      }
      if (act === "toggle-panel") {
        if (compact.matches) { rightSheet.open ? rightSheet.close() : rightSheet.showModal(); }
        else state.panelOpen = !state.panelOpen;
        syncLayout();
      }
      if (act === "close-panel") {
        if (compact.matches) rightSheet.close(); else state.panelOpen = false;
        syncLayout();
        $(".cl-panel-btn")?.focus();
      }
      if (act === "open-nav") leftSheet.showModal();
      if (act === "open-map") {
        state.mapFocus = state.selected ? { kind: "person", id: state.selected } : null;
        if (rightSheet.open) rightSheet.close();
        mapDialog.showModal();
        renderMap();
      }
      if (act === "close-map") mapDialog.close();
      if (act === "map-focus") {
        const next = { kind: button.dataset.kind, id: button.dataset.id };
        state.mapFocus = state.mapFocus && state.mapFocus.kind === next.kind && state.mapFocus.id === next.id ? null : next;
        applyMapFocus(state.mapFocus);
        renderMapDetail();
      }
    });

    // 悬停高亮同一人在各任务的行，不重新渲染。
    function hoverPerson(personId) {
      element.querySelectorAll(".is-mate").forEach(item => item.classList.remove("is-mate"));
      if (personId) tree.querySelectorAll(`.tr-member[data-person="${CSS.escape(personId)}"]`).forEach(item => item.classList.add("is-mate"));
    }
    tree.addEventListener("pointerover", event => hoverPerson(event.target.closest(".tr-member")?.dataset.person || null));
    tree.addEventListener("pointerleave", () => hoverPerson(null));
    const canvas = $(".cl-map-canvas");
    canvas.addEventListener("pointerover", event => { const node = event.target.closest(".mp-node"); if (node) applyMapFocus({ kind: node.dataset.kind, id: node.dataset.id }); });
    canvas.addEventListener("pointerleave", () => applyMapFocus(state.mapFocus));

    page.addEventListener("input", event => {
      if (!event.target.matches(".ls-search input")) return;
      state.list.query = event.target.value;
      const caret = event.target.selectionStart;
      renderList();
      const input = page.querySelector(".ls-search input");
      input.focus();
      input.setSelectionRange(caret, caret);
    });
    palette.querySelector("input").addEventListener("input", renderPalette);
    palette.addEventListener("keydown", event => {
      const options = [...palette.querySelectorAll(".pl-item")];
      const at = options.findIndex(option => option.getAttribute("aria-selected") === "true");
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const next = options[(at + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length];
        options.forEach(option => option.setAttribute("aria-selected", String(option === next)));
        next?.scrollIntoView({ block: "nearest" });
      }
      if (event.key === "Enter" && !event.isComposing && options[at]) { event.preventDefault(); options[at].click(); }
    });
    palette.addEventListener("close", () => { if (paletteReturn && document.contains(paletteReturn) && !paletteReturn.closest("dialog:not([open])")) paletteReturn.focus(); });

    document.addEventListener("keydown", event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); palette.open ? palette.close() : openPalette(); }
      const typing = event.target.matches?.("input") && event.target.value;
      if (event.key === "Escape" && !event.isComposing && !typing && !element.querySelector("dialog[open]") && (state.view === "list" || state.view === "assistant")) { event.preventDefault(); back(); }
    });

    draft.addEventListener("input", () => {
      const had = hasDraft(state.personId, state.taskId);
      state.drafts.set(key(state.personId, state.taskId), draft.value);
      sizeDraft(); updateHint();
      if (had !== hasDraft(state.personId, state.taskId)) { renderSide(); renderPanel(); }
    });
    draft.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $(".cl-compose").requestSubmit(); }
    });
    $(".cl-compose").addEventListener("submit", event => {
      event.preventDefault();
      const text = draft.value.trim();
      if (!text || draft.disabled) return;
      const goal = sample.goals.find(item => item.id === state.taskId);
      // 和正式产品一致：在未开始的任务里发消息也会开始它。
      if (goal && goal.status === "queued" && state.personId === state.leaderId) { goal.status = "running"; goal.updated = 0; }
      const current = key(state.personId, state.taskId);
      state.messages.set(current, [...(state.messages.get(current) || []), text]);
      state.drafts.set(current, "");
      state.scrolls.delete(current);
      renderAll();
      draft.focus();
    });
    [leftSheet, rightSheet, mapDialog, palette, $(".cl-docs"), $(".cl-menu")].forEach(dialog => dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); }));
    rightSheet.addEventListener("close", () => $(".cl-panel-btn")?.setAttribute("aria-pressed", "false"));
    mapDialog.addEventListener("close", () => { if (state.view === "chat") renderPanel(); });
    narrow.addEventListener("change", syncLayout);
    compact.addEventListener("change", syncLayout);

    // 打开项目先到工作台；左侧和对话区预先停在林序的任务上，方便直接点回。
    state.last.set("lin", ["lin", "org"]);
    renderAll();
    return { state };
  }

  root.CrewOrgChat = { mount };
  const standalone = document.getElementById("crew-org-chat");
  if (standalone) mount(standalone);
})(globalThis);
