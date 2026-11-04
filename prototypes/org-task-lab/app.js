(function (root) {
  "use strict";

  const sample = root.CrewOrgSample;
  const model = root.CrewOrgModel;
  const { icon } = root.CrewLightIcons;

  const TEXT = {
    running: "推进中", waiting_user: "等你答复", paused: "已暂停", pausing: "暂停中", completed: "已完成", awaiting_acceptance: "待验收",
    working: "执行中", waiting: "等你答复", queued: "排队中", starting: "启动中", idle: "待继续", offline: "离线",
    cancelled: "已取消", failed: "失败", planned: "未启动", unknown: "状态未知"
  };
  const SHAPE = {
    running: "st-run", working: "st-run", queued: "st-queue", starting: "st-queue", waiting_user: "st-wait", waiting: "st-wait",
    paused: "st-pause", pausing: "st-pause", idle: "st-idle", planned: "st-plan", completed: "st-done", awaiting_acceptance: "st-done",
    failed: "st-fail", cancelled: "st-fail", offline: "st-off"
  };
  const TONE = { running: "go", working: "go", starting: "go", waiting_user: "warn", waiting: "warn", awaiting_acceptance: "warn", failed: "bad" };

  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const runtimeName = runtime => runtime === "claude" ? "CC" : runtime === "codex" ? "Codex" : String(runtime || "");
  const avatar = (person, size = 20) => `<span class="av av-${esc(person.tint || "stone")}" style="--av:${size}px" aria-hidden="true">${esc(person.name.slice(0, 1))}</span>`;
  const stateIcon = status => `<span class="st st-${TONE[status] || "mute"}" title="${esc(TEXT[status] || TEXT.unknown)}">${icon(SHAPE[status] || "st-idle")}</span>`;
  const stateTag = status => `<span class="st st-${TONE[status] || "mute"}">${icon(SHAPE[status] || "st-idle")}<span>${esc(TEXT[status] || TEXT.unknown)}</span></span>`;

  function mount(element) {
    const id = element.id || "cl";
    const narrow = matchMedia("(max-width: 760px)");
    const state = {
      leaderId: "lin",
      personId: "lin",
      taskId: "org",
      panelOpen: true,
      sheet: null,
      expanded: new Set(["org"]),
      openRow: null,
      selected: null,
      mapFocus: null,
      drafts: new Map(),
      scrolls: new Map(),
      messages: new Map(),
      visited: new Set()
    };
    const key = (personId, taskId) => model.conversationKey(personId, taskId ?? null);
    const hasDraft = (personId, taskId) => Boolean((state.drafts.get(key(personId, taskId)) || "").trim());

    function group() {
      const data = model.project(sample, state.leaderId);
      const tasks = data.tasks.filter(task => !task.history);
      const taskIds = new Set(tasks.map(task => task.id));
      // 后端建任务时会给 leader 自动写入一条分工（承载原始需求和 leader 的执行）。
      // 界面上 leader 是任务的主人：任务行就是 leader 的任务对话，组员 = 分工 − leader。
      const links = data.links.filter(link => !link.history && taskIds.has(link.taskId) && link.personId !== data.leader.id);
      const person = personId => data.people.find(item => item.id === personId);
      const task = taskId => tasks.find(item => item.id === taskId);
      const linksOf = personId => links.filter(link => link.personId === personId);
      const linksIn = taskId => links.filter(link => link.taskId === taskId);
      const link = (personId, taskId) => links.find(item => item.personId === personId && item.taskId === taskId);
      const standby = data.people.filter(item => item.id !== data.leader.id && !linksOf(item.id).length);
      return { ...data, tasks, links, person, task, linksOf, linksIn, link, standby };
    }

    element.classList.add("cl");
    element.innerHTML = `
      <header class="cl-top">
        <span class="cl-brand"><img src="crew.svg" alt="">Crew</span>
        <span class="cl-path">工作区 / Crew</span>
        <span class="cl-top-note">样例数据</span>
      </header>
      <div class="cl-body">
        <nav class="cl-nav" aria-label="对话导航"></nav>
        <main class="cl-chat">
          <header class="cl-head"></header>
          <div class="cl-log" role="log" tabindex="0" aria-label="对话记录"><div class="cl-log-inner"></div></div>
          <form class="cl-compose">
            <div class="cl-box">
              <textarea id="${id}-draft" rows="1" aria-label="消息草稿"></textarea>
              <div class="cl-box-foot">
                <span class="cl-hint" role="status"></span>
                <button type="submit" class="cl-send" aria-label="发送（样例，仅本页保存）">${icon("send")}</button>
              </div>
            </div>
          </form>
        </main>
        <aside class="cl-panel" aria-label="本组架构">
          <header class="cl-panel-head"></header>
          <div class="cl-tree-scroll"></div>
        </aside>
      </div>
      <dialog class="cl-sheet cl-sheet-left" aria-label="对话导航"></dialog>
      <dialog class="cl-sheet cl-sheet-right" aria-label="本组架构"></dialog>
      <dialog class="cl-map" aria-labelledby="${id}-map-title">
        <header class="cl-map-head">
          <h2 id="${id}-map-title">完整关系图谱</h2>
          <span class="cl-map-legend">
            <span><i class="lg lg-solid"></i>有执行记录</span><span><i class="lg lg-dash"></i>已分配未启动</span><span><i class="lg lg-on"></i>当前对话</span>
          </span>
          <button type="button" class="cl-icon-btn" data-act="close-map" aria-label="关闭图谱，回到对话" title="回到对话（Esc）">${icon("close")}</button>
        </header>
        <div class="cl-map-scroll"><div class="cl-map-canvas"></div></div>
        <div class="cl-map-detail" aria-live="polite"></div>
      </dialog>`;

    const $ = selector => element.querySelector(selector);
    const nav = $(".cl-nav");
    const panel = $(".cl-panel");
    const body = $(".cl-body");
    const log = $(".cl-log");
    const draft = $("textarea");
    const tree = $(".cl-tree-scroll");
    const leftSheet = $(".cl-sheet-left");
    const rightSheet = $(".cl-sheet-right");
    const mapDialog = $(".cl-map");
    const chat = $(".cl-chat");

    /* ---------- 导航 ---------- */
    function renderNav() {
      const data = group();
      const leader = data.leader;
      const visited = [...state.visited].map(item => JSON.parse(item)).filter(([personId, taskId]) => personId !== leader.id && data.person(personId) && data.task(taskId));
      const isCurrent = (personId, taskId) => state.personId === personId && state.taskId === taskId;
      const navItem = (personId, taskId, content, extra = "") => `
        <button type="button" class="nv-item${extra}${isCurrent(personId, taskId) ? " is-current" : ""}" data-act="go" data-person="${esc(personId)}" data-task="${esc(taskId ?? "")}"${isCurrent(personId, taskId) ? ' aria-current="page"' : ""}>
          ${content}${hasDraft(personId, taskId) ? `<span class="nv-draft" title="有未发送草稿">${icon("pencil")}</span>` : ""}
        </button>`;
      const subItems = taskId => visited.filter(([, t]) => t === taskId).map(([personId]) => {
        const person = data.person(personId);
        const link = data.link(personId, taskId);
        return navItem(personId, taskId, `${avatar(person, 16)}<span class="nv-label">${esc(person.name)}</span>${link ? stateIcon(link.state) : ""}`, " nv-sub");
      }).join("");
      nav.innerHTML = `
        <label class="nv-leader">
          ${avatar(leader, 22)}
          <select data-act="leader" aria-label="切换负责人">
            ${sample.people.filter(person => person.role === "leader").map(person => `<option value="${esc(person.session_id)}"${person.session_id === leader.id ? " selected" : ""}>${esc(person.name)}</option>`).join("")}
          </select>
          <span class="nv-caret">${icon("chevron")}</span>
        </label>
        ${navItem(leader.id, null, `<span class="nv-ic">${icon("person")}</span><span class="nv-label">总会话</span>`)}
        <h2 class="nv-heading">任务<span>${data.tasks.length}</span></h2>
        ${data.tasks.map(task => `
          <div class="nv-group${state.taskId === task.id ? " has-current" : ""}">
            ${navItem(leader.id, task.id, `${stateIcon(task.status)}<span class="nv-label">${esc(task.title)}</span>`)}
            ${subItems(task.id)}
          </div>`).join("") || '<p class="nv-empty">还没有任务</p>'}`;
    }

    /* ---------- 对话 ---------- */
    function contextState(data, person, task) {
      if (!task) return null;
      if (person.id === data.leader.id) return task.status;
      return data.link(person.id, task.id)?.state || "planned";
    }

    function roll(data, rows) {
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
        out.push(you(task.summary));
        if (person.id === data.leader.id) {
          out.push(them(members.length ? `${task.code} 的分工如下，每人的执行记录独立保存：` : "要求已记录，还没有分配成员。",
            members.length ? roll(data, members.map(link => ({ person: data.person(link.personId), text: link.work, state: link.state }))) : ""));
        } else {
          const link = data.link(person.id, task.id);
          out.push(them(link?.executionId ? `我在这个任务里负责：${link.work}。` : `已分配「${link?.work || "待补充"}」，还没有执行记录。`,
            link ? `<p class="msg-exec">${stateTag(link.state)}<span>${esc(runtimeName(link.runtime))} · 记录归属 ${esc(task.code)}</span></p>` : ""));
        }
        const shared = members.filter(link => data.linksOf(link.personId).length > 1 && (person.id === data.leader.id || link.personId === person.id));
        if (shared.length) {
          out.push(you("同时在别的任务里的人，会互相影响吗？"));
          out.push(them(`不会混在一起。${shared.map(link => {
            const other = data.linksOf(link.personId).filter(item => item.taskId !== task.id)
              .map(item => `${data.task(item.taskId).code}（${TEXT[item.state]}）`).join("、");
            return `${link.personId === person.id ? "我" : data.person(link.personId).name}另在 ${other}`;
          }).join("；")}，各自的对话和记录分开保存。`));
        }
      } else {
        // 只有 leader 有总会话；组员的对话都属于某个任务。
        out.push(you("这组的几个任务都在推进，我想看看成员分别负责什么。"));
        out.push(them(data.tasks.length ? `本组 ${data.tasks.length} 项任务。分工可以在右侧展开，进入组员的本任务对话可以直接沟通：` : "本组还没有任务。",
          data.tasks.length ? roll(data, data.tasks.map(task => ({ person: { name: task.code, tint: "stone" }, text: task.title, state: task.status }))) : ""));
      }
      (state.messages.get(key(person.id, task?.id)) || []).forEach(text => out.push(you(text)));
      return out.join("");
    }

    function renderConversation() {
      const data = group();
      const person = data.person(state.personId) || data.leader;
      const task = data.task(state.taskId);
      const status = contextState(data, person, task);
      const name = task ? task.title : "总会话";
      const paused = task && (task.status === "paused" || task.status === "pausing");
      $(".cl-head").innerHTML = `
        <button type="button" class="cl-icon-btn cl-nav-btn" data-act="open-nav" aria-label="打开对话导航">${icon("menu")}</button>
        ${avatar(person, 24)}
        <h1 title="${esc(task ? `${task.code} · ${task.title}` : `${person.name}的总会话`)}">
          <span class="hd-person">${esc(person.name)}</span><span class="hd-sep" aria-hidden="true">/</span><span class="hd-context">${esc(name)}</span>
        </h1>
        ${status ? stateTag(status) : ""}
        <span class="hd-runtime" title="执行载体">${esc(runtimeName(task ? data.link(person.id, task.id)?.runtime || person.runtime : person.runtime))}</span>
        <button type="button" class="cl-icon-btn cl-panel-btn" data-act="toggle-panel" aria-pressed="${state.panelOpen}" aria-label="本组架构" title="本组架构">${icon("panel")}</button>`;
      $(".cl-log-inner").innerHTML = transcriptFor(data, person, task);
      log.scrollTop = state.scrolls.has(key(person.id, task?.id)) ? state.scrolls.get(key(person.id, task?.id)) : log.scrollHeight;
      draft.value = state.drafts.get(key(person.id, task?.id)) || "";
      draft.disabled = Boolean(paused);
      draft.placeholder = paused ? "任务已暂停，只能查看记录" : `发给 ${person.name} · ${name}`;
      chat.classList.toggle("is-paused", Boolean(paused));
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

    /* ---------- 右侧关系树 ---------- */
    function focusModel(data) {
      // 有选中成员时按此人跨任务高亮，否则高亮当前对话所在路径。
      const focusId = state.selected;
      const hlTasks = new Set();
      const hlRows = new Set();
      if (focusId) {
        data.linksOf(focusId).forEach(link => { hlTasks.add(link.taskId); hlRows.add(key(link.personId, link.taskId)); });
      } else if (state.taskId) {
        // leader 的任务对话落在任务行；组员的本任务对话落到组员行。
        hlTasks.add(state.taskId);
        if (data.link(state.personId, state.taskId)) hlRows.add(key(state.personId, state.taskId));
      }
      return { focusId, hlTasks, hlRows };
    }

    function pathClasses(items, isLit) {
      const last = items.reduce((at, item, index) => isLit(item) ? index : at, -1);
      return items.map((item, index) => `${isLit(item) ? " hl" : ""}${index < last ? " pass" : ""}`);
    }

    function detail(data, person, contextTaskId) {
      const mine = data.linksOf(person.id);
      const isLeader = person.id === data.leader.id;
      const isCurrent = (personId, taskId) => state.personId === personId && state.taskId === taskId;
      const destination = (taskId, title, sub, status) => {
        const current = isCurrent(person.id, taskId);
        return `<button type="button" class="dt-go${current ? " is-current" : ""}${taskId === contextTaskId ? " is-here" : ""}" data-act="go" data-person="${esc(person.id)}" data-task="${esc(taskId ?? "")}"${current ? ' aria-current="true"' : ""}>
          ${status ? stateIcon(status) : `<span class="dt-ic">${icon("person")}</span>`}
          <span class="dt-copy"><span class="dt-title">${esc(title)}</span>${sub ? `<span class="dt-sub">${esc(sub)}</span>` : ""}</span>
          <span class="dt-end">${current ? "正在聊" : icon("chat")}</span>
        </button>`;
      };
      const wrap = (intro, items) => `<div class="dt" role="group" aria-label="${esc(person.name)}的对话入口"><p class="dt-intro">${esc(intro)}</p>${items}</div>`;
      if (isLeader) {
        return wrap(`负责 ${data.tasks.length} 项任务`, destination(null, "总会话", "跨任务安排与汇总", null)
          + data.tasks.map(task => destination(task.id, `${task.code} ${task.title}`, "任务主线", task.status)).join(""));
      }
      if (!mine.length) return wrap("还没有分配任务。leader 派活后，这里会出现任务对话。", "");
      return wrap(`参与 ${mine.length} 项任务`, mine.map(link => {
        const task = data.task(link.taskId);
        return destination(task.id, `${task.code} ${task.title}`, `${link.work} · ${runtimeName(link.runtime)}`, link.state);
      }).join(""));
    }

    function renderPanel() {
      const data = group();
      const scrollTop = tree.scrollTop;
      const { focusId, hlTasks, hlRows } = focusModel(data);
      const leader = data.leader;
      const dim = Boolean(focusId);
      const leaderCurrent = state.personId === leader.id && state.taskId === null;
      // leader 不再作为树根：整栏就是 leader 的组，标题旁放总会话入口。
      $(".cl-panel-head").innerHTML = `
        <h2>${esc(leader.name)}组</h2>
        <span class="ph-meta">${data.people.length - 1} 人 · ${data.tasks.length} 任务</span>
        <button type="button" class="ph-general${leaderCurrent ? " is-on" : ""}" data-act="go" data-person="${esc(leader.id)}" data-task=""
          aria-label="${leaderCurrent ? "正在聊：" : "进入"}${esc(leader.name)}的总会话"${leaderCurrent ? ' aria-current="true"' : ""}>${icon("person")}总会话${hasDraft(leader.id, null) ? icon("pencil", "ph-draft") : ""}</button>
        <button type="button" class="cl-icon-btn" data-act="open-map" aria-label="展开完整关系图谱" title="完整图谱">${icon("graph")}</button>
        <button type="button" class="cl-icon-btn" data-act="close-panel" aria-label="收起本组架构" title="收起">${icon("close")}</button>`;

      const rowOpen = (personId, taskId) => state.openRow === key(personId, taskId);

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
                <span class="tr-line">
                  <span class="tr-name">${esc(person.name)}</span>
                  ${count > 1 ? `<span class="tr-multi" title="也参与：${esc(others)}">${icon("layers")}${count}</span>` : ""}
                  <span class="tr-state">${stateTag(link.state)}</span>
                </span>
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

      const taskBranch = task => {
        const members = data.linksIn(task.id);
        const expanded = state.expanded.has(task.id);
        const current = state.taskId === task.id;
        const leaderHere = state.personId === leader.id && current;
        const memberClasses = pathClasses(members, link => hlRows.has(key(link.personId, link.taskId)));
        const names = members.map(link => {
          const person = data.person(link.personId);
          return `<span class="tr-nm${focusId === person.id ? " is-focus" : ""}" data-person="${esc(person.id)}">${esc(person.name)}</span>`;
        }).join("、");
        const sub = expanded
          ? `${TEXT[task.status]} · ${members.length ? `${members.length} 人` : "尚未分配成员"}`
          : `${TEXT[task.status]} · ${names || "尚未分配成员"}`;
        return `<li class="tr-task${hlTasks.has(task.id) ? " hl" : ""}${current ? " is-current" : ""}${expanded ? " is-open" : ""}${memberClasses.some(item => item.includes("hl")) && expanded ? " hl-down" : ""}">
          <div class="tr-row tr-task-row">
            <button type="button" class="tr-main" data-act="toggle-task" data-task="${esc(task.id)}" aria-expanded="${expanded}" aria-label="${esc(task.title)}，${esc(TEXT[task.status])}，${expanded ? "收起" : "展开"}成员">
              ${stateIcon(task.status)}
              <span class="tr-copy">
                <span class="tr-line"><span class="tr-title">${esc(task.title)}</span><span class="tr-code">${esc(task.code)}</span></span>
                <span class="tr-sub">${sub}</span>
              </span>
              <span class="tr-chev">${icon("chevron")}</span>
            </button>
            ${hasDraft(leader.id, task.id) ? `<span class="tr-draft" title="有未发送草稿">${icon("pencil")}</span>` : ""}
            <button type="button" class="tr-act${leaderHere ? " is-on" : ""}" data-act="go" data-person="${esc(leader.id)}" data-task="${esc(task.id)}"
              aria-label="${leaderHere ? "正在聊：" : "进入"}${esc(leader.name)}的${esc(task.title)}任务对话" title="${leaderHere ? "正在聊" : `${esc(leader.name)}的任务对话`}">${icon("chat")}</button>
          </div>
          ${expanded ? (members.length ? `<ul class="tr-members">${members.map((link, index) => memberRow(link, memberClasses[index])).join("")}</ul>` : "") : ""}
        </li>`;
      };

      // 待命组员没有任务，也就没有对话入口；leader 派活后才出现在任务下面。
      tree.innerHTML = `<div class="tr${dim ? " has-focus" : ""}">
        ${data.tasks.length ? `<ul class="tr-tasks">${data.tasks.map(taskBranch).join("")}</ul>` : '<p class="tr-empty">还没有任务</p>'}
        ${data.standby.length ? `<section class="tr-standby" aria-label="组内待命">
          <h3>待命<span>派活后可在任务里对话</span></h3>
          ${data.standby.map(person => `<div class="tr-standby-item${dim ? " is-dim" : ""}">
            ${avatar(person, 20)}<span class="tr-copy"><span class="tr-line"><span class="tr-name">${esc(person.name)}</span></span><span class="tr-sub">${esc(person.summary)}</span></span>
          </div>`).join("")}
        </section>` : ""}
      </div>`;
      tree.scrollTop = scrollTop;
    }

    /* ---------- 完整图谱 ---------- */
    function renderMap() {
      const data = group();
      const leader = data.leader;
      const members = [...new Set(data.links.filter(link => link.personId !== leader.id).map(link => link.personId))].map(data.person);
      const G = { pad: 28, lw: 176, tw: 248, mw: 208, gap: 104, th: 58, tg: 14, mh: 52, mg: 10, sh: 30 };
      const X = { l: G.pad, t: G.pad + G.lw + G.gap, m: G.pad + G.lw + G.gap + G.tw + G.gap };
      const colTask = data.tasks.length * G.th + Math.max(0, data.tasks.length - 1) * G.tg;
      const colMember = members.length * G.mh + Math.max(0, members.length - 1) * G.mg + (data.standby.length ? G.sh + data.standby.length * (G.mh + G.mg) : 0);
      const H = Math.max(colTask, colMember, 72) + G.pad * 2;
      const W = X.m + G.mw + G.pad;
      const ty = index => (H - colTask) / 2 + index * (G.th + G.tg);
      const my = index => (H - colMember) / 2 + index * (G.mh + G.mg);
      const ly = H / 2 - 36;
      const curve = (x1, y1, x2, y2) => { const dx = (x2 - x1) * .5; return `M${x1} ${y1}C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`; };

      const edges = [];
      data.tasks.forEach((task, index) => {
        // leader 到任务是主责关系，统一细线；虚实只用来区分组员是否已有执行记录。
        const current = state.personId === leader.id && state.taskId === task.id;
        edges.push(`<path class="mp-edge e-own${current ? " e-current" : ""}" data-task="${esc(task.id)}" data-person="${esc(leader.id)}" d="${curve(X.l + G.lw, H / 2, X.t, ty(index) + G.th / 2)}"/>`);
      });
      data.links.filter(link => link.personId !== leader.id).forEach(link => {
        const ti = data.tasks.findIndex(task => task.id === link.taskId);
        const mi = members.findIndex(person => person.id === link.personId);
        const current = state.personId === link.personId && state.taskId === link.taskId;
        edges.push(`<path class="mp-edge${link.executionId ? "" : " e-dash"}${current ? " e-current" : ""}" data-task="${esc(link.taskId)}" data-person="${esc(link.personId)}" d="${curve(X.t + G.tw, ty(ti) + G.th / 2, X.m, my(mi) + G.mh / 2)}"/>`);
      });

      const leaderCurrent = state.personId === leader.id;
      const nodes = [`<button type="button" class="mp-node mp-leader${leaderCurrent && !state.taskId ? " is-current" : ""}" data-act="map-focus" data-kind="person" data-id="${esc(leader.id)}"
          style="left:${X.l}px;top:${ly}px;width:${G.lw}px">
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
          <span class="mp-sub" data-default="${esc(`${count} 项任务`)}">${esc(`${count} 项任务`)}</span></span>
          <span class="mp-state"></span>
        </button>`);
      });
      if (data.standby.length) {
        const top = my(members.length);
        nodes.push(`<span class="mp-standby-label" style="left:${X.m}px;top:${top + 4}px">待命</span>`);
        data.standby.forEach((person, index) => nodes.push(`<button type="button" class="mp-node mp-member mp-standby" data-act="map-focus" data-kind="person" data-id="${esc(person.id)}"
          style="left:${X.m}px;top:${top + G.sh + index * (G.mh + G.mg)}px;width:${G.mw}px;height:${G.mh}px">
          ${avatar(person, 24)}<span class="mp-copy"><span class="mp-title">${esc(person.name)}</span><span class="mp-sub" data-default="未分配任务">未分配任务</span></span>
        </button>`));
      }

      $(".cl-map-canvas").style.cssText = `width:${W}px;height:${H}px`;
      $(".cl-map-canvas").innerHTML = `<svg class="mp-edges" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">${edges.join("")}</svg>${nodes.join("")}`;
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
      const lit = edges.filter(edge => focus.kind === "task" ? edge.dataset.task === focus.id : edge.dataset.person === focus.id || (focus.id === data.leader.id));
      const litNodes = new Set([`${focus.kind}:${focus.id}`]);
      lit.forEach(edge => { litNodes.add(`task:${edge.dataset.task}`); litNodes.add(`person:${edge.dataset.person}`); });
      if (focus.kind === "person" && focus.id !== data.leader.id) {
        // 成员聚焦时也点亮 leader 到这些任务的连线。
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
      if (!focus) { target.innerHTML = '<p class="mp-hint">点任务看分工，点成员看他在哪些任务里。再次点击取消。</p>'; return; }
      if (focus.kind === "task") {
        const task = data.task(focus.id);
        const current = state.personId === data.leader.id && state.taskId === task.id;
        target.innerHTML = `<div class="mp-detail-head">${stateIcon(task.status)}<b>${esc(task.code)} ${esc(task.title)}</b><span>${esc(task.summary)}</span></div>
          <div class="dt dt-row"><button type="button" class="dt-go${current ? " is-current" : ""}" data-act="go" data-person="${esc(data.leader.id)}" data-task="${esc(task.id)}">
            <span class="dt-ic">${icon("chat")}</span><span class="dt-copy"><span class="dt-title">${esc(data.leader.name)}的任务对话</span></span><span class="dt-end">${current ? "正在聊" : icon("chevron")}</span></button></div>`;
        return;
      }
      const person = data.person(focus.id);
      target.innerHTML = `<div class="mp-detail-head">${avatar(person, 20)}<b>${esc(person.name)}</b><span>${esc(person.summary)}</span></div>${detail(data, person, state.taskId).replace('class="dt"', 'class="dt dt-row"')}`;
    }

    /* ---------- 状态切换 ---------- */
    function remember() {
      const current = key(state.personId, state.taskId);
      state.drafts.set(current, draft.value);
      state.scrolls.set(current, log.scrollTop);
    }

    function switchConversation(personId, taskId) {
      if (personId === state.personId && taskId === state.taskId) { closeSheets(); return; }
      remember();
      state.personId = personId;
      state.taskId = taskId;
      // 进入对话后收起选中，树回到“当前对话路径”的高亮。
      state.openRow = null;
      state.selected = null;
      if (personId !== group().leader.id) state.visited.add(key(personId, taskId));
      if (taskId) state.expanded.add(taskId);
      closeSheets();
      if (mapDialog.open) mapDialog.close();
      renderAll();
    }

    function renderAll() { renderConversation(); renderNav(); renderPanel(); syncLayout(); }

    function syncLayout() {
      if (narrow.matches) {
        if (panel.parentNode !== rightSheet) rightSheet.append(panel);
        if (nav.parentNode !== leftSheet) leftSheet.append(nav);
      } else {
        closeSheets();
        if (nav.parentNode !== body) body.prepend(nav);
        if (panel.parentNode !== body) body.append(panel);
        panel.hidden = !state.panelOpen;
      }
      body.classList.toggle("no-panel", !narrow.matches && !state.panelOpen);
      $(".cl-panel-btn")?.setAttribute("aria-pressed", String(narrow.matches ? rightSheet.open : state.panelOpen));
    }

    function closeSheets() {
      if (leftSheet.open) leftSheet.close();
      if (rightSheet.open) rightSheet.close();
    }

    element.addEventListener("click", event => {
      const button = event.target.closest("[data-act]");
      if (!button || !element.contains(button) || button.tagName === "SELECT") return;
      const { act, person, task } = button.dataset;
      if (act === "go") switchConversation(person, task || null);
      if (act === "toggle-task") {
        state.expanded.has(task) ? state.expanded.delete(task) : state.expanded.add(task);
        renderPanel();
        tree.querySelector(`[data-act="toggle-task"][data-task="${CSS.escape(task)}"]`)?.focus();
      }
      if (act === "select") {
        const rowKey = key(person, task || null);
        if (state.openRow === rowKey) { state.openRow = null; state.selected = null; }
        else { state.openRow = rowKey; state.selected = person; }
        renderPanel();
        tree.querySelector(`[data-act="select"][data-person="${CSS.escape(person)}"][data-task="${CSS.escape(task || "")}"]`)?.focus();
      }
      if (act === "toggle-panel") {
        if (narrow.matches) { rightSheet.open ? rightSheet.close() : rightSheet.showModal(); }
        else { state.panelOpen = !state.panelOpen; syncLayout(); }
      }
      if (act === "close-panel") {
        if (narrow.matches) rightSheet.close(); else { state.panelOpen = false; syncLayout(); $(".cl-panel-btn").focus(); }
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
        if (state.mapFocus?.kind === "person") { state.selected = next.id; }
        applyMapFocus(state.mapFocus);
        renderMapDetail();
      }
    });

    // 悬停只做轻量高亮，不重新渲染，键盘焦点同理。
    function hoverPerson(personId) {
      element.querySelectorAll(".is-mate").forEach(item => item.classList.remove("is-mate"));
      if (!personId) return;
      tree.querySelectorAll(`.tr-member[data-person="${CSS.escape(personId)}"], .tr-nm[data-person="${CSS.escape(personId)}"]`).forEach(item => item.classList.add("is-mate"));
    }
    tree.addEventListener("pointerover", event => hoverPerson(event.target.closest(".tr-member")?.dataset.person || null));
    tree.addEventListener("pointerleave", () => hoverPerson(null));
    tree.addEventListener("focusin", event => hoverPerson(event.target.closest(".tr-member")?.dataset.person || null));
    const canvas = $(".cl-map-canvas");
    canvas.addEventListener("pointerover", event => {
      const node = event.target.closest(".mp-node");
      if (!node) return;
      applyMapFocus({ kind: node.dataset.kind, id: node.dataset.id });
    });
    canvas.addEventListener("pointerleave", () => applyMapFocus(state.mapFocus));

    element.addEventListener("change", event => {
      if (event.target.dataset.act !== "leader") return;
      remember();
      state.leaderId = event.target.value;
      const data = group();
      state.personId = data.leader.id;
      state.taskId = data.tasks[0]?.id || null;
      state.expanded = new Set([state.taskId].filter(Boolean));
      state.openRow = null; state.selected = null; state.mapFocus = null;
      renderAll();
      nav.querySelector("select")?.focus();
    });

    draft.addEventListener("input", () => {
      const had = hasDraft(state.personId, state.taskId);
      state.drafts.set(key(state.personId, state.taskId), draft.value);
      sizeDraft(); updateHint();
      if (had !== hasDraft(state.personId, state.taskId)) { renderNav(); renderPanel(); }
    });
    draft.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $(".cl-compose").requestSubmit(); }
    });
    $(".cl-compose").addEventListener("submit", event => {
      event.preventDefault();
      const text = draft.value.trim();
      if (!text || draft.disabled) return;
      const current = key(state.personId, state.taskId);
      state.messages.set(current, [...(state.messages.get(current) || []), text]);
      state.drafts.set(current, "");
      state.scrolls.delete(current);
      renderConversation(); renderNav(); renderPanel();
      draft.focus();
    });
    [leftSheet, rightSheet].forEach(sheet => sheet.addEventListener("click", event => { if (event.target === sheet) sheet.close(); }));
    rightSheet.addEventListener("close", () => $(".cl-panel-btn")?.setAttribute("aria-pressed", "false"));
    mapDialog.addEventListener("click", event => { if (event.target === mapDialog) mapDialog.close(); });
    mapDialog.addEventListener("close", () => renderPanel());
    narrow.addEventListener("change", syncLayout);

    renderAll();
    return { state };
  }

  root.CrewOrgChat = { mount };
  const standalone = document.getElementById("crew-org-chat");
  if (standalone) mount(standalone);
})(globalThis);
