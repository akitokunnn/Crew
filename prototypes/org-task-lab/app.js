(function (root) {
  "use strict";

  const variants = {
    lanes: { name: "任务分组", question: "每个任务，谁在做什么" },
    matrix: { name: "成员矩阵", question: "每个组员，关联哪些任务" },
    graph: { name: "关系图谱", question: "leader、任务、成员如何连接" }
  };
  const states = {
    running: "推进中", waiting_user: "等你答复", paused: "已暂停", pausing: "暂停中", completed: "已完成", awaiting_acceptance: "待验收",
    working: "执行中", waiting: "等你答复", queued: "排队中", idle: "待继续", offline: "离线", cancelled: "已取消", failed: "失败", planned: "已分配 · 未启动", unknown: "状态未知"
  };
  const esc = value => String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  const runtimeName = runtime => runtime === "claude" ? "CC" : runtime === "codex" ? "Codex" : runtime;
  const avatar = person => `<span class="org-avatar org-avatar-${esc(person.tint || "stone")}" aria-hidden="true">${esc(person.name.slice(0, 1))}</span>`;
  const statusBadge = status => `<span class="org-state org-state-${esc(status)}">${esc(states[status] || "状态未知")}</span>`;
  let hostPreferences = root.openai?.widgetState?.privateContent || {};

  function mount(element, options = {}) {
    const idPrefix = element.id;
    const storageKey = `crew-org-task-lab-${options.variant || "standalone"}`;
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch { /* 浏览器可禁用本地存储，演示仍可使用。 */ }
    saved = (options.inline && hostPreferences[storageKey]) || saved || {};
    const state = {
      view: options.variant || (variants[saved.view] ? saved.view : "lanes"),
      leaderId: root.CrewOrgSample.people.some(person => person.role === "leader" && person.session_id === saved.leaderId) ? saved.leaderId : "lin",
      history: saved.history === true,
      filter: ["all", "running", "waiting_user", "paused", "queued"].includes(saved.filter) ? saved.filter : "all",
      query: "", selectedTask: null, selectedMember: null, collapsed: new Set(), conversation: null
    };
    let returnFocus = null;

    function persist(interaction) {
      const preferences = { view: state.view, leaderId: state.leaderId, history: state.history, filter: state.filter };
      try { localStorage.setItem(storageKey, JSON.stringify(preferences)); } catch { /* 存储不可用时保留本次页面状态。 */ }
      if (options.inline && interaction && root.openai?.setWidgetState) {
        hostPreferences = { ...hostPreferences, [storageKey]: preferences };
        root.openai.setWidgetState({
          modelContent: { variant: variants[state.view].name, leaderId: state.leaderId, memberId: state.selectedMember, taskId: state.selectedTask },
          privateContent: hostPreferences
        }).catch(() => {});
      }
    }

    function group() {
      const projected = root.CrewOrgModel.project(root.CrewOrgSample, state.leaderId);
      projected.people = projected.people.map(person => ({ ...person, runtime: runtimeName(person.runtime) }));
      projected.leader = projected.people.find(person => person.id === state.leaderId);
      return projected;
    }

    function context() {
      const data = group();
      const query = state.query.trim().toLocaleLowerCase();
      const tasks = data.tasks.filter(task => {
        if (!state.history && task.history) return false;
        if (state.filter !== "all" && task.status !== state.filter) return false;
        const names = data.links.filter(link => link.taskId === task.id && (state.history || !link.history)).map(link => data.people.find(person => person.id === link.personId)?.name).join(" ");
        return !query || `${task.title} ${task.code} ${names}`.toLocaleLowerCase().includes(query);
      });
      return { ...data, tasks, links: data.links.filter(link => state.history || !link.history), ...state, idPrefix, esc, avatar, statusBadge, runtimeName };
    }

    element.classList.add("org-demo");
    element.innerHTML = `
      ${options.inline ? "" : `
        <div class="org-lab-bar">
          <span>组织架构方案</span>
          <nav class="org-variants" aria-label="选择设计方案">
            ${Object.entries(variants).map(([key, variant]) => `
              <button type="button" data-action="variant" data-view="${key}" aria-pressed="${key === state.view}">
                ${variant.name}
              </button>`).join("")}
          </nav>
          <span class="org-lab-note">交互演示 · 2026.11.04</span>
        </div>`}
      <div class="org-window">
        <header class="org-topbar">
          <span class="org-brand"><img class="org-mark" src="${options.logo || "crew.svg"}" alt="">Crew</span>
          <span class="org-workspace">工作区 / Crew</span>
          <span class="org-demo-tag">样例数据</span>
        </header>
        <main class="org-main">
          <div class="org-title-row">
            <div>
              <p class="org-eyebrow">人员与任务</p>
              <h1>本组架构</h1>
            </div>
            <label class="org-leader-picker" for="${idPrefix}-leader">
              负责人
              <select id="${idPrefix}-leader" data-control="leader">
                ${root.CrewOrgSample.people.filter(person => person.role === "leader").map(person => `
                  <option value="${esc(person.session_id)}"${person.session_id === state.leaderId ? " selected" : ""}>
                    ${esc(person.name)} · ${runtimeName(person.runtime)}
                  </option>`).join("")}
              </select>
            </label>
          </div>
          <section class="org-leader-strip" aria-label="小组概况"></section>
          <div class="org-toolbar">
            <label class="org-search" for="${idPrefix}-search">
              <span>查找任务或成员</span>
              <input id="${idPrefix}-search" type="search" data-control="search" placeholder="任务名称、编号、成员" autocomplete="off">
            </label>
            <label class="org-filter" for="${idPrefix}-filter">
              <span>任务状态</span>
              <select id="${idPrefix}-filter" data-control="filter">
                <option value="all">全部状态</option>
                <option value="running">推进中</option>
                <option value="waiting_user">等你答复</option>
                <option value="paused">已暂停</option>
                <option value="queued">排队中</option>
              </select>
            </label>
            <label class="org-history"><input type="checkbox" data-control="history">显示历史参与</label>
          </div>
          <div class="org-view-caption">
            <h2></h2>
            <span></span>
          </div>
          <div class="org-content"></div>
          <footer class="org-footer">
            <span>任务状态与人员执行状态分别显示</span>
            <span>总会话独立于各任务</span>
          </footer>
        </main>
      </div>
      <dialog class="org-detail-dialog" aria-labelledby="${idPrefix}-detail-title">
        <div class="org-detail-top">
          <span>分工详情</span>
          <button type="button" class="org-close" data-action="close" aria-label="关闭分工详情">×</button>
        </div>
        <div class="org-detail-body"></div>
      </dialog>`;

    const dialog = element.querySelector("dialog");
    const content = element.querySelector(".org-content");
    element.querySelector('[data-control="filter"]').value = state.filter;
    element.querySelector('[data-control="history"]').checked = state.history;

    function render(interaction = false) {
      const ctx = context();
      const total = group();
      const current = total.tasks.filter(task => !task.history);
      const cross = total.people.filter(person => person.role !== "leader" && new Set(total.links.filter(link => link.personId === person.id && !link.history).map(link => link.taskId)).size > 1);
      const strip = element.querySelector(".org-leader-strip");
      strip.innerHTML = `
        <button class="org-leader-person" type="button" data-action="member" data-person="${esc(ctx.leader.id)}">
          ${avatar(ctx.leader)}
          <span>
            <strong>${esc(ctx.leader.name)}<small>leader · ${esc(ctx.leader.runtime)}</small></strong>
            <span>${esc(ctx.leader.summary)}</span>
          </span>
        </button>
        <div class="org-group-numbers">
          <span><strong>${total.people.length - 1}</strong> 位成员</span>
          <span><strong>${current.length}</strong> 项当前任务</span>
          ${cross.length ? `
            <span class="org-cross-note">${cross.map(person => esc(person.name)).join("、")}跨任务协作</span>` : ""}
        </div>
        <button class="org-text-button" type="button" data-action="general" data-person="${esc(ctx.leader.id)}">
          leader 总会话 ↗
        </button>`;
      element.querySelector(".org-view-caption h2").textContent = variants[state.view].question;
      element.querySelector(".org-view-caption > span").textContent = `${ctx.tasks.length} 项任务`;
      content.innerHTML = root.CrewOrgViews[state.view](ctx);
      element.querySelectorAll('[data-action="variant"]').forEach(button => button.setAttribute("aria-pressed", String(button.dataset.view === state.view)));
      persist(interaction);
    }

    function linkDetails(data, link) {
      const task = data.tasks.find(item => item.id === link.taskId);
      return `
        <section class="org-detail-assignment${link.history ? " is-history" : ""}">
          <div>
            <span class="org-code">${esc(task.code)}</span>
            ${link.history ? '<span class="org-chip">历史参与</span>' : ""}
            ${statusBadge(task.status)}
          </div>
          <button type="button" class="org-detail-task" data-action="task" data-task="${esc(task.id)}">
            ${esc(task.title)} ↗
          </button>
          <p>${esc(link.work)}</p>
          <div class="org-detail-link-state">
            ${statusBadge(link.state)}
            <span>${esc(runtimeName(link.runtime))}${link.released ? " · 分工已交接" : ""}</span>
          </div>
          <button type="button" class="org-secondary-button" data-action="conversation" data-person="${esc(link.personId)}" data-task="${esc(link.taskId)}">
            查看本任务对话
          </button>
        </section>`;
    }

    function renderDetail() {
      const data = group();
      const person = data.people.find(item => item.id === state.selectedMember);
      const task = data.tasks.find(item => item.id === state.selectedTask);
      let html;
      if (state.conversation) {
        const scopePerson = data.people.find(item => item.id === state.conversation.personId);
        const scopeTask = data.tasks.find(item => item.id === state.conversation.taskId);
        const link = data.links.find(item => item.personId === scopePerson.id && item.taskId === scopeTask?.id);
        const hasExecution = Boolean(link?.executionId);
        html = `
          <p class="org-eyebrow">对话预览 · 样例</p>
          <h2 id="${idPrefix}-detail-title">${esc(scopePerson.name)}${scopeTask ? "的任务对话" : "的总会话"}</h2>
          <div class="org-scope-label">${scopeTask ? `${esc(scopeTask.code)} · ${esc(scopeTask.title)}` : "总会话"}</div>
          <p class="org-scope-note">
            ${scopeTask ? hasExecution ? "当前查看这个任务中的独立执行记录。" : "尚未启动，没有执行记录。" : "当前查看这个人的独立总会话。"}
          </p>
          ${!scopeTask || hasExecution ? `
            <div class="org-message">
              <span>${esc(scopePerson.name)} · ${esc(scopeTask ? runtimeName(link.runtime) : scopePerson.runtime)}</span>
              <p>${esc(link?.work || "这里可以讨论新的安排与小组协调。")}</p>
              ${link ? statusBadge(link.state) : ""}
            </div>` : ""}
          <div class="org-scope-identity">
            <span>人员</span><code>${esc(scopePerson.id)}</code>
            <span>任务作用域</span><code>${esc(scopeTask?.id || "null · 总会话")}</code>
          </div>
          <button type="button" class="org-secondary-button" data-action="member" data-person="${esc(scopePerson.id)}">
            返回成员分工
          </button>`;
      } else if (person) {
        const links = data.links.filter(link => link.personId === person.id);
        const active = links.filter(link => !link.history);
        const history = links.filter(link => link.history);
        html = `
          <div class="org-detail-person">
            ${avatar(person)}
            <div>
              <h2 id="${idPrefix}-detail-title">${esc(person.name)}</h2>
              <p>${esc(person.role === "leader" ? "leader" : "组员")} · ${esc(person.runtime)} · ${esc(person.summary)}</p>
            </div>
          </div>
          <p class="org-detail-summary">
            ${person.role === "leader" ? `统筹 ${data.tasks.filter(item => !item.history).length} 项当前任务，亲自参与 ${active.length} 项。` : `关联 ${active.length} 项当前任务。`}
          </p>
          ${active.length ? active.map(link => linkDetails(data, link)).join("") : `<p class="org-detail-empty">${person.role === "leader" ? "统筹本组任务，当前由成员执行。" : "当前未分配任务，可从总会话继续交流。"}</p>`}
          ${history.length ? `
            <details class="org-history-details"${state.history ? " open" : ""}>
              <summary>历史参与 · ${history.length} 条</summary>
              ${history.map(link => linkDetails(data, link)).join("")}
            </details>` : ""}
          <button type="button" class="org-primary-button" data-action="general" data-person="${esc(person.id)}">
            进入${esc(person.name)}的总会话
          </button>`;
      } else if (task) {
        const links = data.links.filter(link => link.taskId === task.id && (state.history || !link.history));
        const assignments = links.map(link => {
          const member = data.people.find(item => item.id === link.personId);
          return `
            <button type="button" class="org-detail-member-row" data-action="member" data-person="${esc(member.id)}" data-task="${esc(task.id)}">
              ${avatar(member)}
              <span>
                <strong>${esc(member.name)}${link.history ? " · 历史参与" : ""}</strong>
                <span>${esc(link.work)}</span>
              </span>
              ${statusBadge(link.state)}
            </button>`;
        }).join("");
        html = `
          <p class="org-eyebrow">${esc(task.code)} · 第 ${task.current_round} 轮</p>
          <h2 id="${idPrefix}-detail-title">${esc(task.title)}</h2>
          <div class="org-detail-task-status">
            ${statusBadge(task.status)}
            <span>${esc(data.leader.name)}统筹</span>
          </div>
          <p class="org-detail-summary">${esc(task.summary)}</p>
          <h3>本任务分工</h3>
          ${links.length ? assignments : '<p class="org-detail-empty">尚未分配成员。</p>'}
          <button type="button" class="org-primary-button" data-action="conversation" data-person="${esc(data.leader.id)}" data-task="${esc(task.id)}">
            查看 leader 的本任务对话
          </button>`;
      } else return;
      element.querySelector(".org-detail-body").innerHTML = html;
    }

    function openDetail(button) {
      renderDetail();
      if (!dialog.open) {
        returnFocus = { action: button.dataset.action, person: button.dataset.person, task: button.dataset.task };
        dialog.showModal();
      }
      element.querySelector(".org-close").focus();
    }

    element.addEventListener("click", event => {
      const button = event.target.closest("[data-action]");
      if (!button || !element.contains(button)) return;
      const action = button.dataset.action;
      if (action === "variant") { state.view = button.dataset.view; render(true); return; }
      if (action === "collapse") {
        state.collapsed.has(button.dataset.task) ? state.collapsed.delete(button.dataset.task) : state.collapsed.add(button.dataset.task);
        const taskId = button.dataset.task;
        render(true);
        Array.from(content.querySelectorAll('[data-action="collapse"]')).find(item => item.dataset.task === taskId)?.focus();
        return;
      }
      if (action === "close") { dialog.close(); return; }
      if (action === "task") { state.selectedTask = button.dataset.task; state.selectedMember = null; state.conversation = null; }
      if (action === "member") { state.selectedMember = button.dataset.person; state.selectedTask = button.dataset.task || null; state.conversation = null; }
      if (action === "general" || action === "conversation") {
        state.selectedMember = button.dataset.person;
        state.selectedTask = action === "general" ? null : button.dataset.task;
        state.conversation = { personId: button.dataset.person, taskId: state.selectedTask };
      }
      if (["task", "member", "general", "conversation"].includes(action)) { openDetail(button); render(true); }
    });

    element.addEventListener("input", event => {
      if (event.target.dataset.control !== "search") return;
      state.query = event.target.value;
      render(true);
    });
    element.addEventListener("change", event => {
      const control = event.target.dataset.control;
      if (control === "history") state.history = event.target.checked;
      if (control === "filter") state.filter = event.target.value;
      if (control === "leader") {
        state.leaderId = event.target.value;
        state.selectedTask = null; state.selectedMember = null; state.conversation = null; state.collapsed.clear();
      }
      render(true);
    });
    dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener("close", () => {
      if (!returnFocus) return;
      const matching = Array.from(element.querySelectorAll("[data-action]")).find(button => !dialog.contains(button) && button.dataset.action === returnFocus.action && button.dataset.person === returnFocus.person && button.dataset.task === returnFocus.task);
      matching?.focus();
    });
    if (options.inline) {
      root.addEventListener("openai:set_globals", event => {
        const updated = event.detail?.globals?.widgetState?.privateContent;
        const preferences = updated?.[storageKey];
        if (!preferences) return;
        hostPreferences = updated;
        if (preferences.leaderId === state.leaderId && preferences.history === state.history && preferences.filter === state.filter) return;
        const leader = root.CrewOrgSample.people.find(person => person.session_id === preferences.leaderId && person.role === "leader");
        if (leader) state.leaderId = leader.session_id;
        state.history = preferences.history === true;
        state.filter = ["all", "running", "waiting_user", "paused", "queued"].includes(preferences.filter) ? preferences.filter : "all";
        element.querySelector('[data-control="leader"]').value = state.leaderId;
        element.querySelector('[data-control="filter"]').value = state.filter;
        element.querySelector('[data-control="history"]').checked = state.history;
        state.selectedTask = null;
        state.selectedMember = null;
        state.conversation = null;
        dialog.close();
        render();
      });
    }
    render();

    if (globalThis.Tweak && options.inline) {
      const design = { spacing: 16 };
      const tweak = new Tweak({ container: element, onChange: () => element.style.setProperty("--org-density", `${design.spacing}px`) });
      tweak.addSlider(design, "spacing", { label: "内容间距", min: 12, max: 24, step: 4, unit: "px" });
    }
    return { render, state };
  }

  root.CrewOrgLab = { mount };
  const standalone = document.getElementById("crew-org-lab");
  if (standalone) mount(standalone);
})(globalThis);
